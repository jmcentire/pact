"""Tests for structured directive protocol."""

from __future__ import annotations

import json

from pact.daemon import Directive, parse_directive, send_signal


class TestDirective:
    """Directive dataclass."""

    def test_simple_directive(self):
        d = Directive(type="resume")
        assert d.type == "resume"
        assert d.payload == {}

    def test_directive_with_payload(self):
        d = Directive(type="set_mode", payload={"mode": "unary"})
        assert d.type == "set_mode"
        assert d.payload["mode"] == "unary"

    def test_default_payload(self):
        d = Directive(type="shutdown")
        assert d.payload == {}


class TestParseDirective:
    """parse_directive() parsing logic."""

    def test_plain_resume(self):
        d = parse_directive("resume")
        assert d.type == "resume"
        assert d.payload == {}

    def test_plain_shutdown(self):
        d = parse_directive("shutdown")
        assert d.type == "shutdown"
        assert d.payload == {}

    def test_plain_approved(self):
        d = parse_directive("approved")
        assert d.type == "approved"
        assert d.payload == {}

    def test_plain_with_whitespace(self):
        d = parse_directive("  resume  \n")
        assert d.type == "resume"

    def test_json_set_mode(self):
        raw = json.dumps({"type": "set_mode", "mode": "unary"})
        d = parse_directive(raw)
        assert d.type == "set_mode"
        assert d.payload["mode"] == "unary"

    def test_json_set_config(self):
        raw = json.dumps({"type": "set_config", "parallel_components": True})
        d = parse_directive(raw)
        assert d.type == "set_config"
        assert d.payload["parallel_components"] is True

    def test_json_inject_context(self):
        raw = json.dumps({"type": "inject_context", "context": "extra info"})
        d = parse_directive(raw)
        assert d.type == "inject_context"
        assert d.payload["context"] == "extra info"

    def test_json_type_only(self):
        raw = json.dumps({"type": "resume"})
        d = parse_directive(raw)
        assert d.type == "resume"
        assert d.payload == {}

    def test_invalid_json_falls_back_to_string(self):
        d = parse_directive("{invalid json")
        assert d.type == "{invalid json"
        assert d.payload == {}

    def test_json_with_multiple_payload_keys(self):
        raw = json.dumps({
            "type": "set_config",
            "build_mode": "unary",
            "parallel_components": True,
            "budget": 20.0,
        })
        d = parse_directive(raw)
        assert d.type == "set_config"
        assert d.payload["build_mode"] == "unary"
        assert d.payload["parallel_components"] is True
        assert d.payload["budget"] == 20.0

    def test_empty_string(self):
        d = parse_directive("")
        assert d.type == ""

    def test_json_without_type_key(self):
        """JSON without 'type' uses the raw string as type."""
        raw = json.dumps({"mode": "unary"})
        d = parse_directive(raw)
        assert d.type == raw.strip()  # Falls back to raw string

    def test_backward_compatible_strings(self):
        """All original string commands still work."""
        for cmd in ("resume", "shutdown", "approved", "resumed"):
            d = parse_directive(cmd)
            assert d.type == cmd
            assert d.payload == {}

    def test_json_nested_payload(self):
        raw = json.dumps({
            "type": "set_config",
            "environment": {"python_path": "/usr/bin/python3"},
        })
        d = parse_directive(raw)
        assert d.type == "set_config"
        assert d.payload["environment"]["python_path"] == "/usr/bin/python3"


class TestSendSignal:
    """send_signal() with directive parameter."""

    def test_no_fifo_returns_false(self, tmp_path):
        """No FIFO -> False."""
        result = send_signal(tmp_path, "resume")
        assert result is False

    def test_no_fifo_with_directive_returns_false(self, tmp_path):
        """No FIFO -> False even with directive dict."""
        result = send_signal(
            tmp_path,
            directive={"type": "set_mode", "mode": "unary"},
        )
        assert result is False

    def test_regular_file_returns_false(self, tmp_path):
        """Non-FIFO file at the path -> False."""
        pact_dir = tmp_path / ".pact"
        pact_dir.mkdir()
        dispatch = pact_dir / "dispatch"
        dispatch.write_text("not a fifo")
        result = send_signal(tmp_path, "resume")
        assert result is False
