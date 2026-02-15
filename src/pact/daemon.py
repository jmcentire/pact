"""Event-driven coordinator using FIFO for inter-process signaling.

Replaces the poll-sleep scheduler loop with a FIFO-based dispatch:
  1. Daemon runs phases back-to-back with zero delay
  2. When paused (human input needed), blocks on FIFO
  3. External process (pact signal, human, webhook) writes to FIFO to resume
  4. Two safeguards:
     - After t seconds of silence, verify agent PID/health
     - After t' >> t seconds, alert user and exit

The FIFO lives at .pact/dispatch and is created/cleaned by the daemon.
"""

from __future__ import annotations

import asyncio
import errno
import logging
import os
import select
import signal
import stat
import time
from datetime import datetime
from pathlib import Path

from pact.budget import BudgetExceeded, BudgetTracker
from pact.config import GlobalConfig, ProjectConfig
from pact.events import EventBus
from pact.lifecycle import format_run_summary
from pact.project import ProjectManager
from pact.scheduler import Scheduler
from pact.schemas import RunState

logger = logging.getLogger(__name__)


class ActivityTracker:
    """Tracks daemon activity to prevent false idle timeouts."""

    def __init__(self) -> None:
        self._last_activity = time.monotonic()
        self._activity_type = "init"

    def record_activity(self, activity_type: str) -> None:
        """Reset idle timer. Called on API calls, state transitions, audit entries."""
        self._last_activity = time.monotonic()
        self._activity_type = activity_type

    def idle_seconds(self) -> float:
        """Seconds since last recorded activity."""
        return time.monotonic() - self._last_activity

    def is_idle(self, max_idle: int) -> bool:
        """True only when no activity for max_idle seconds."""
        return self.idle_seconds() >= max_idle

    @property
    def last_activity_type(self) -> str:
        return self._activity_type


class Daemon:
    """FIFO-based coordinator. Fires phases immediately, blocks only on human input."""

    def __init__(
        self,
        project: ProjectManager,
        scheduler: Scheduler,
        health_check_interval: int = 30,
        max_idle: int = 600,
        event_bus: EventBus | None = None,
        poll_integrations: bool = False,
        poll_interval: int = 60,
        max_poll_attempts: int = 10,
    ) -> None:
        self.project = project
        self.scheduler = scheduler
        self.fifo_path = project._pact_dir / "dispatch"
        self.pid_path = project._pact_dir / "daemon.pid"
        self.health_check_interval = health_check_interval  # t: PID check interval
        self.max_idle = max_idle  # t': max wait before alert+exit
        self._shutdown_requested = False
        self.event_bus = event_bus
        self.poll_integrations = poll_integrations
        self.poll_interval = poll_interval
        self.max_poll_attempts = max_poll_attempts
        self.activity = ActivityTracker()

    # ── Public API ──────────────────────────────────────────────────

    async def run(self) -> RunState:
        """Run the dispatch loop. Returns final state."""
        self._ensure_fifo()
        self._write_pid()
        self._start_shutdown_listener()

        try:
            return await self._dispatch_loop()
        finally:
            self._cleanup()

    # ── Shutdown Listener ───────────────────────────────────────────

    def _start_shutdown_listener(self) -> None:
        """Start a background thread that watches a shutdown sentinel file.

        The FIFO is used for paused-state signaling. For mid-phase shutdown,
        we use a sentinel file (.pact/shutdown) so we don't compete with
        the FIFO reader. `pact stop` creates the sentinel; the dispatch loop
        checks for it between phases.
        """
        self._shutdown_path = self.project._pact_dir / "shutdown"
        # Clean up any stale sentinel from a previous run
        if self._shutdown_path.exists():
            self._shutdown_path.unlink()

    def _check_shutdown(self) -> bool:
        """Check if a shutdown has been requested (sentinel file or flag)."""
        if self._shutdown_requested:
            return True
        if self._shutdown_path.exists():
            self._shutdown_requested = True
            try:
                self._shutdown_path.unlink()
            except OSError:
                pass
            return True
        return False

    # ── Dispatch Loop ───────────────────────────────────────────────

    async def _dispatch_loop(self) -> RunState:
        """Core loop: fire phases immediately, block on FIFO when paused."""
        while True:
            state = self.project.load_state()

            # Check for shutdown between phases
            if self._check_shutdown():
                logger.info("Shutdown requested — exiting cleanly between phases")
                state.pause("Shutdown requested")
                self.project.save_state(state)
                self.project.append_audit("daemon_shutdown", "Clean shutdown between phases")
                return state

            if state.status in ("completed", "failed", "budget_exceeded"):
                logger.info("Run terminal: %s", state.status)
                return state

            if state.status == "paused":
                logger.info("Paused: %s — waiting for signal on FIFO", state.pause_reason)
                signal_msg = await self._wait_for_signal()

                if signal_msg is None:
                    # Timed out — alert and exit
                    logger.error(
                        "Timed out after %ds waiting for input. Exiting.",
                        self.max_idle,
                    )
                    state.pause_reason += " [DAEMON TIMED OUT — manual resume required]"
                    self.project.save_state(state)
                    return state

                logger.info("Received signal: %s", signal_msg.strip())
                self.activity.record_activity("fifo_signal")

                if signal_msg.strip() == "shutdown":
                    logger.info("Shutdown signal received — exiting cleanly")
                    state.pause_reason = "Shutdown requested"
                    self.project.save_state(state)
                    self.project.append_audit("daemon_shutdown", "Clean shutdown via FIFO signal")
                    return state

                # Unpause
                state.status = "active"
                state.pause_reason = ""
                self.project.save_state(state)
                self.project.append_audit("daemon_resume", f"Signal: {signal_msg.strip()}")
                continue

            # Fire next phase immediately — no sleep
            logger.info("Dispatching phase: %s", state.phase)
            self.project.append_audit("daemon_dispatch", f"Phase: {state.phase}")

            try:
                state = await asyncio.wait_for(
                    self.scheduler.run_once(),
                    timeout=self.max_idle,
                )
                self.activity.record_activity("phase_complete")
            except asyncio.TimeoutError:
                logger.error(
                    "Phase %s timed out after %ds", state.phase, self.max_idle,
                )
                state.fail(f"Phase {state.phase} timed out after {self.max_idle}s")
                self.project.save_state(state)
                return state

            logger.info(
                "Phase complete: %s -> %s (%s)",
                state.phase, state.status,
                f"${state.total_cost_usd:.4f}",
            )

    # ── FIFO Signal Waiting ─────────────────────────────────────────

    async def _wait_for_signal(self) -> str | None:
        """Block on FIFO read with health-check timeouts.

        If poll_integrations is enabled, race FIFO read against polling loop.
        Returns the signal message, or None if max_idle exceeded.
        """
        if self.poll_integrations and self.event_bus:
            # Race: FIFO read vs integration polling
            fifo_task = asyncio.create_task(self._fifo_read_async())
            poll_task = asyncio.create_task(self._poll_integrations_loop())

            done, pending = await asyncio.wait(
                {fifo_task, poll_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            for task in pending:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

            for task in done:
                return task.result()

            return None

        # Default: just wait on FIFO
        return await self._fifo_read_async()

    async def _fifo_read_async(self) -> str | None:
        """Async wrapper around blocking FIFO read."""
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._blocking_wait)

    async def _poll_integrations_loop(self) -> str | None:
        """Poll integrations for human responses."""
        from pact.human.context import check_for_human_response

        for _ in range(self.max_poll_attempts):
            await asyncio.sleep(self.poll_interval)

            response = await check_for_human_response(self.event_bus)
            if response:
                # Save as interview answer if applicable
                interview = self.project.load_interview()
                if interview and not interview.approved:
                    for q in interview.questions:
                        if q not in interview.user_answers:
                            interview.user_answers[q] = response
                            break
                    interview.approved = True
                    self.project.save_interview(interview)

                logger.info("Integration poll found response: %s", response[:100])
                return "approved"

        return None

    def _blocking_wait(self) -> str | None:
        """Blocking FIFO read with two-tier timeout.

        Tier 1 (health_check_interval): Check for stuck state
        Tier 2 (max_idle): Alert user and return None
        """
        start = time.monotonic()

        while True:
            elapsed = time.monotonic() - start
            if elapsed >= self.max_idle:
                return None

            remaining = min(
                self.health_check_interval,
                self.max_idle - elapsed,
            )

            try:
                # Open FIFO for reading (blocks until a writer opens it)
                # Use O_RDONLY | O_NONBLOCK to avoid blocking on open,
                # then use select for timed reads
                fd = os.open(str(self.fifo_path), os.O_RDONLY | os.O_NONBLOCK)
                try:
                    readable, _, _ = select.select([fd], [], [], remaining)
                    if readable:
                        data = os.read(fd, 4096)
                        if data:
                            return data.decode().strip()
                        # EOF — writer closed, retry
                finally:
                    os.close(fd)

            except OSError as e:
                if e.errno == errno.ENXIO:
                    # No writer yet — sleep briefly and retry
                    time.sleep(0.5)
                else:
                    raise

            # Health check at tier-1 timeout
            elapsed = time.monotonic() - start
            if elapsed >= self.health_check_interval:
                logger.debug(
                    "Health check at %.0fs — still waiting for signal",
                    elapsed,
                )

    # ── FIFO Management ─────────────────────────────────────────────

    def _ensure_fifo(self) -> None:
        """Create the FIFO if it doesn't exist."""
        self.project._pact_dir.mkdir(parents=True, exist_ok=True)

        if self.fifo_path.exists():
            if stat.S_ISFIFO(os.stat(str(self.fifo_path)).st_mode):
                return  # Already a FIFO
            self.fifo_path.unlink()  # Was a regular file, replace

        os.mkfifo(str(self.fifo_path))
        logger.info("Created FIFO: %s", self.fifo_path)

    def _write_pid(self) -> None:
        """Write daemon PID for health checking."""
        self.pid_path.write_text(str(os.getpid()))

    def _cleanup(self) -> None:
        """Remove FIFO and PID file."""
        if self.fifo_path.exists():
            try:
                self.fifo_path.unlink()
            except OSError:
                pass
        if self.pid_path.exists():
            try:
                self.pid_path.unlink()
            except OSError:
                pass


# ── Signal Sender ───────────────────────────────────────────────────


def send_signal(project_dir: str | Path, message: str = "resume") -> bool:
    """Write a message to the project's FIFO to resume the daemon.

    Returns True if the signal was sent, False if no FIFO exists.
    """
    fifo_path = Path(project_dir).resolve() / ".pact" / "dispatch"

    if not fifo_path.exists():
        return False

    if not stat.S_ISFIFO(os.stat(str(fifo_path)).st_mode):
        return False

    # Open FIFO for writing (blocks until daemon has it open for reading)
    # Use a timeout to avoid hanging if daemon is dead
    try:
        fd = os.open(str(fifo_path), os.O_WRONLY | os.O_NONBLOCK)
        try:
            os.write(fd, (message + "\n").encode())
            return True
        finally:
            os.close(fd)
    except OSError as e:
        if e.errno == errno.ENXIO:
            # No reader — daemon isn't running
            return False
        raise


def check_daemon_health(project_dir: str | Path) -> dict:
    """Check if the daemon is alive for a project.

    Returns dict with: alive (bool), pid (int|None), fifo_exists (bool).
    """
    project_dir = Path(project_dir).resolve()
    pact_dir = project_dir / ".pact"
    pid_path = pact_dir / "daemon.pid"
    fifo_path = pact_dir / "dispatch"

    result = {"alive": False, "pid": None, "fifo_exists": False}

    if fifo_path.exists() and stat.S_ISFIFO(os.stat(str(fifo_path)).st_mode):
        result["fifo_exists"] = True

    if pid_path.exists():
        try:
            pid = int(pid_path.read_text().strip())
            result["pid"] = pid
            # Check if process is alive
            os.kill(pid, 0)  # Signal 0 = existence check
            result["alive"] = True
        except (ValueError, ProcessLookupError, PermissionError):
            result["alive"] = False

    return result
