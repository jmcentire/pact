"""Signal ingestion — tail logs, watch processes, receive webhooks.

Async generators that yield Signal objects from various sources.
Fingerprinting deduplicates identical errors. Project matching
maps signals to known Pact projects via embedded log keys.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from datetime import datetime

from pact.schemas_monitoring import (
    LogKey,
    MonitoringTarget,
    Signal,
    SignalFingerprint,
)

logger = logging.getLogger(__name__)

# Regex for PACT log keys: PACT:<project_id>:<component_id>
_LOG_KEY_PATTERN = re.compile(r"PACT:(\w+):(\w+)")

# Patterns stripped during fingerprint normalization
_NORMALIZE_PATTERNS = [
    re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}[.\d]*Z?"),  # timestamps
    re.compile(r"0x[0-9a-fA-F]+"),  # memory addresses
    re.compile(r":\d+"),  # line numbers
    re.compile(r"\b\d{5,}\b"),  # large numbers (PIDs, etc.)
]


def extract_log_key(line: str) -> LogKey | None:
    """Extract a PACT log key from a log line.

    Matches PACT:<project_id>:<component_id> anywhere in the line.
    Returns LogKey or None if no match.
    """
    m = _LOG_KEY_PATTERN.search(line)
    if not m:
        return None
    return LogKey(project_id=m.group(1), component_id=m.group(2))


def fingerprint_signal(signal: Signal) -> str:
    """Normalize error text and produce a stable SHA256 hash.

    Strips timestamps, memory addresses, line numbers, and large
    numbers so that the same logical error produces the same hash
    regardless of when/where it occurred.
    """
    text = signal.raw_text
    for pattern in _NORMALIZE_PATTERNS:
        text = pattern.sub("", text)
    text = " ".join(text.split())  # collapse whitespace
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def match_signal_to_project(
    signal: Signal,
    targets: list[MonitoringTarget],
) -> tuple[str, str] | None:
    """Match a signal to a project and component.

    Strategy:
    1. If the signal has a log key, match by project_id hash
    2. If no log key, try to match by log file path
    3. Returns (project_dir, component_id) or None
    """
    # Strategy 1: Log key matching
    log_key = extract_log_key(signal.log_key or signal.raw_text)
    if log_key:
        for target in targets:
            pid = _project_id_hash(target.project_dir)
            if pid == log_key.project_id:
                return (target.project_dir, log_key.component_id)

    # Strategy 2: File path matching
    if signal.file_path:
        for target in targets:
            if signal.file_path in target.log_files:
                return (target.project_dir, "")

    # Strategy 3: Process name matching
    if signal.process_name:
        for target in targets:
            for pattern in target.process_patterns:
                if pattern in signal.process_name:
                    return (target.project_dir, "")

    return None


def _project_id_hash(project_dir: str) -> str:
    """Generate a 6-char project ID hash from a project directory path."""
    return hashlib.sha256(project_dir.encode()).hexdigest()[:6]


class LogTailer:
    """Async generator that tails log files using tail -F."""

    def __init__(self, path: str, error_patterns: list[str] | None = None) -> None:
        self.path = path
        self._patterns = [re.compile(p) for p in (error_patterns or ["ERROR", "CRITICAL", "Traceback"])]
        self._process: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        """Start the tail -F subprocess."""
        self._process = await asyncio.create_subprocess_exec(
            "tail", "-F", self.path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )

    async def lines(self):
        """Async generator yielding matching log lines."""
        if not self._process or not self._process.stdout:
            return
        while True:
            try:
                line_bytes = await self._process.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").rstrip()
                if any(p.search(line) for p in self._patterns):
                    yield line
            except asyncio.CancelledError:
                break

    def stop(self) -> None:
        """Terminate the tail process."""
        if self._process:
            self._process.terminate()


class ProcessWatcher:
    """Periodically checks for crashed processes matching patterns."""

    def __init__(
        self,
        patterns: list[str],
        poll_interval: float = 10.0,
    ) -> None:
        self.patterns = patterns
        self.poll_interval = poll_interval
        self._known_pids: set[str] = set()

    async def watch(self):
        """Async generator yielding signals when watched processes disappear."""
        while True:
            for pattern in self.patterns:
                try:
                    proc = await asyncio.create_subprocess_exec(
                        "pgrep", "-f", pattern,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.DEVNULL,
                    )
                    stdout, _ = await proc.communicate()
                    current_pids = set(stdout.decode().strip().split("\n")) - {""}
                    lost = self._known_pids - current_pids
                    if self._known_pids and lost:
                        yield Signal(
                            source="process",
                            raw_text=f"Process matching '{pattern}' disappeared (PIDs: {', '.join(lost)})",
                            timestamp=datetime.now().isoformat(),
                            process_name=pattern,
                        )
                    self._known_pids = current_pids
                except Exception:
                    pass
            await asyncio.sleep(self.poll_interval)


class WebhookReceiver:
    """Minimal HTTP server accepting POST error reports."""

    def __init__(self, port: int) -> None:
        self.port = port
        self._queue: asyncio.Queue[Signal] = asyncio.Queue()
        self._server: asyncio.Server | None = None

    async def start(self) -> None:
        """Start the webhook HTTP server."""
        self._server = await asyncio.start_server(
            self._handle_connection, "127.0.0.1", self.port,
        )

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single HTTP connection."""
        try:
            # Read HTTP request
            request_line = await reader.readline()
            headers: dict[str, str] = {}
            while True:
                header_line = await reader.readline()
                if header_line in (b"\r\n", b"\n", b""):
                    break
                if b":" in header_line:
                    key, val = header_line.decode().split(":", 1)
                    headers[key.strip().lower()] = val.strip()

            content_length = int(headers.get("content-length", "0"))
            body = b""
            if content_length > 0:
                body = await reader.readexactly(content_length)

            # Parse JSON body
            try:
                data = json.loads(body.decode())
                signal = Signal(
                    source="webhook",
                    raw_text=data.get("error", data.get("message", str(data))),
                    timestamp=datetime.now().isoformat(),
                    log_key=data.get("log_key", ""),
                )
                await self._queue.put(signal)
                response = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nOK"
            except (json.JSONDecodeError, KeyError):
                response = b"HTTP/1.1 400 Bad Request\r\nContent-Length: 11\r\n\r\nBad Request"

            writer.write(response)
            await writer.drain()
        except Exception:
            pass
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def signals(self):
        """Async generator yielding signals received via webhook."""
        while True:
            signal = await self._queue.get()
            yield signal

    def stop(self) -> None:
        """Stop the webhook server."""
        if self._server:
            self._server.close()


class SignalIngester:
    """Orchestrates all signal sources with deduplication.

    Consumes from log tailers, process watchers, and webhook receivers.
    Deduplicates via fingerprinting within a configurable window.
    """

    def __init__(
        self,
        targets: list[MonitoringTarget],
        dedup_window_seconds: float = 300.0,
    ) -> None:
        self._targets = targets
        self._dedup_window = dedup_window_seconds
        self._fingerprints: dict[str, SignalFingerprint] = {}
        self._signal_queue: asyncio.Queue[tuple[Signal, MonitoringTarget | None]] = asyncio.Queue()
        self._tailers: list[LogTailer] = []
        self._watchers: list[ProcessWatcher] = []
        self._webhooks: list[WebhookReceiver] = []
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        """Start all signal sources."""
        for target in self._targets:
            # Log tailers
            for log_file in target.log_files:
                tailer = LogTailer(log_file, target.error_patterns)
                self._tailers.append(tailer)
                await tailer.start()
                self._tasks.append(
                    asyncio.create_task(self._consume_tailer(tailer, target))
                )

            # Process watchers
            if target.process_patterns:
                watcher = ProcessWatcher(target.process_patterns)
                self._watchers.append(watcher)
                self._tasks.append(
                    asyncio.create_task(self._consume_watcher(watcher, target))
                )

            # Webhook receivers
            if target.webhook_port > 0:
                receiver = WebhookReceiver(target.webhook_port)
                self._webhooks.append(receiver)
                await receiver.start()
                self._tasks.append(
                    asyncio.create_task(self._consume_webhook(receiver, target))
                )

    async def _consume_tailer(self, tailer: LogTailer, target: MonitoringTarget) -> None:
        """Read from a log tailer and enqueue signals."""
        try:
            async for line in tailer.lines():
                signal = Signal(
                    source="log_file",
                    raw_text=line,
                    timestamp=datetime.now().isoformat(),
                    file_path=tailer.path,
                    log_key=_extract_key_str(line),
                )
                if self._deduplicate(signal):
                    await self._signal_queue.put((signal, target))
        except asyncio.CancelledError:
            pass

    async def _consume_watcher(self, watcher: ProcessWatcher, target: MonitoringTarget) -> None:
        """Read from a process watcher and enqueue signals."""
        try:
            async for signal in watcher.watch():
                if self._deduplicate(signal):
                    await self._signal_queue.put((signal, target))
        except asyncio.CancelledError:
            pass

    async def _consume_webhook(self, receiver: WebhookReceiver, target: MonitoringTarget) -> None:
        """Read from a webhook receiver and enqueue signals."""
        try:
            async for signal in receiver.signals():
                if self._deduplicate(signal):
                    await self._signal_queue.put((signal, target))
        except asyncio.CancelledError:
            pass

    def _deduplicate(self, signal: Signal) -> bool:
        """Check if this signal is new. Returns True if it should be emitted."""
        fp_hash = fingerprint_signal(signal)
        now = datetime.now()

        if fp_hash in self._fingerprints:
            existing = self._fingerprints[fp_hash]
            last_seen = datetime.fromisoformat(existing.last_seen)
            if (now - last_seen).total_seconds() < self._dedup_window:
                existing.count += 1
                existing.last_seen = now.isoformat()
                return False

        self._fingerprints[fp_hash] = SignalFingerprint(
            hash=fp_hash,
            first_seen=now.isoformat(),
            last_seen=now.isoformat(),
            count=1,
            representative=signal,
        )
        return True

    async def watch(self):
        """Async generator yielding (Signal, MonitoringTarget | None) pairs."""
        while True:
            pair = await self._signal_queue.get()
            yield pair

    def stop(self) -> None:
        """Stop all signal sources."""
        for task in self._tasks:
            task.cancel()
        for tailer in self._tailers:
            tailer.stop()
        for webhook in self._webhooks:
            webhook.stop()


def _extract_key_str(line: str) -> str:
    """Extract PACT:xxx:yyy from a line, returning the full key string or empty."""
    m = _LOG_KEY_PATTERN.search(line)
    return m.group(0) if m else ""
