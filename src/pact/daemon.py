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
from pact.lifecycle import format_run_summary
from pact.project import ProjectManager
from pact.scheduler import Scheduler
from pact.schemas import RunState

logger = logging.getLogger(__name__)


class Daemon:
    """FIFO-based coordinator. Fires phases immediately, blocks only on human input."""

    def __init__(
        self,
        project: ProjectManager,
        scheduler: Scheduler,
        health_check_interval: int = 30,
        max_idle: int = 600,
    ) -> None:
        self.project = project
        self.scheduler = scheduler
        self.fifo_path = project._pact_dir / "dispatch"
        self.pid_path = project._pact_dir / "daemon.pid"
        self.health_check_interval = health_check_interval  # t: PID check interval
        self.max_idle = max_idle  # t': max wait before alert+exit

    # ── Public API ──────────────────────────────────────────────────

    async def run(self) -> RunState:
        """Run the dispatch loop. Returns final state."""
        self._ensure_fifo()
        self._write_pid()

        try:
            return await self._dispatch_loop()
        finally:
            self._cleanup()

    # ── Dispatch Loop ───────────────────────────────────────────────

    async def _dispatch_loop(self) -> RunState:
        """Core loop: fire phases immediately, block on FIFO when paused."""
        while True:
            state = self.project.load_state()

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

        Returns the signal message, or None if max_idle exceeded.
        Uses a thread to avoid blocking the event loop.
        """
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._blocking_wait)

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
