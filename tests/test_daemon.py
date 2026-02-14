"""Tests for FIFO-based daemon coordinator."""

from __future__ import annotations

import os
import stat
import threading
import time
from pathlib import Path

import pytest

from pact.daemon import Daemon, check_daemon_health, send_signal
from pact.project import ProjectManager
from pact.schemas import RunState


@pytest.fixture
def project(tmp_path):
    pm = ProjectManager(tmp_path / "proj")
    pm.init()
    return pm


class TestFifoManagement:
    def test_ensure_fifo_creates(self, project):
        daemon = Daemon(project, scheduler=None)
        daemon._ensure_fifo()
        assert daemon.fifo_path.exists()
        assert stat.S_ISFIFO(os.stat(str(daemon.fifo_path)).st_mode)

    def test_ensure_fifo_idempotent(self, project):
        daemon = Daemon(project, scheduler=None)
        daemon._ensure_fifo()
        daemon._ensure_fifo()  # Should not raise
        assert stat.S_ISFIFO(os.stat(str(daemon.fifo_path)).st_mode)

    def test_ensure_fifo_replaces_regular_file(self, project):
        daemon = Daemon(project, scheduler=None)
        # Create a regular file where FIFO should be
        project._pact_dir.mkdir(parents=True, exist_ok=True)
        daemon.fifo_path.write_text("not a fifo")
        daemon._ensure_fifo()
        assert stat.S_ISFIFO(os.stat(str(daemon.fifo_path)).st_mode)

    def test_write_pid(self, project):
        daemon = Daemon(project, scheduler=None)
        daemon._write_pid()
        assert daemon.pid_path.exists()
        assert int(daemon.pid_path.read_text()) == os.getpid()

    def test_cleanup(self, project):
        daemon = Daemon(project, scheduler=None)
        daemon._ensure_fifo()
        daemon._write_pid()
        assert daemon.fifo_path.exists()
        assert daemon.pid_path.exists()
        daemon._cleanup()
        assert not daemon.fifo_path.exists()
        assert not daemon.pid_path.exists()


class TestSendSignal:
    def test_no_fifo(self, project):
        assert send_signal(project.project_dir, "test") is False

    def test_not_a_fifo(self, project):
        fifo_path = project._pact_dir / "dispatch"
        fifo_path.write_text("regular file")
        assert send_signal(project.project_dir, "test") is False

    def test_send_and_receive(self, project):
        """Test that send_signal can write to a FIFO that a reader is waiting on."""
        fifo_path = project._pact_dir / "dispatch"
        os.mkfifo(str(fifo_path))

        received = []

        def reader():
            fd = os.open(str(fifo_path), os.O_RDONLY)
            try:
                data = os.read(fd, 4096)
                received.append(data.decode().strip())
            finally:
                os.close(fd)

        t = threading.Thread(target=reader)
        t.start()
        time.sleep(0.1)  # Let reader open FIFO

        sent = send_signal(project.project_dir, "hello")
        t.join(timeout=2)

        assert sent is True
        assert received == ["hello"]

        # Cleanup
        fifo_path.unlink()


class TestCheckDaemonHealth:
    def test_no_files(self, project):
        health = check_daemon_health(project.project_dir)
        assert health["alive"] is False
        assert health["pid"] is None
        assert health["fifo_exists"] is False

    def test_with_fifo_only(self, project):
        fifo_path = project._pact_dir / "dispatch"
        os.mkfifo(str(fifo_path))
        health = check_daemon_health(project.project_dir)
        assert health["fifo_exists"] is True
        assert health["alive"] is False
        fifo_path.unlink()

    def test_alive_with_current_pid(self, project):
        fifo_path = project._pact_dir / "dispatch"
        os.mkfifo(str(fifo_path))
        pid_path = project._pact_dir / "daemon.pid"
        pid_path.write_text(str(os.getpid()))

        health = check_daemon_health(project.project_dir)
        assert health["alive"] is True
        assert health["pid"] == os.getpid()
        assert health["fifo_exists"] is True

        fifo_path.unlink()

    def test_dead_pid(self, project):
        pid_path = project._pact_dir / "daemon.pid"
        pid_path.write_text("999999999")  # Almost certainly not a real PID

        health = check_daemon_health(project.project_dir)
        assert health["alive"] is False
        assert health["pid"] == 999999999
