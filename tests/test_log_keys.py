"""Tests for log key generation and embedding."""

from __future__ import annotations

import re

import pytest

from pact.interface_stub import (
    project_id_hash,
    render_handoff_brief,
    render_log_key_preamble,
)
from pact.schemas import (
    ComponentContract,
    ContractTestSuite,
    FieldSpec,
    FunctionContract,
)


class TestRenderLogKeyPreamble:
    def test_generates_valid_python(self):
        preamble = render_log_key_preamble("abc123", "pricing_engine")
        # Should be valid Python
        compile(preamble, "<test>", "exec")

    def test_contains_pact_key(self):
        preamble = render_log_key_preamble("abc123", "pricing_engine")
        assert '_PACT_KEY = "PACT:abc123:pricing_engine"' in preamble

    def test_custom_prefix(self):
        preamble = render_log_key_preamble("abc123", "comp", prefix="MYAPP")
        assert '_PACT_KEY = "MYAPP:abc123:comp"' in preamble

    def test_includes_logger_setup(self):
        preamble = render_log_key_preamble("abc123", "comp")
        assert "import logging" in preamble
        assert "logger" in preamble

    def test_includes_formatter(self):
        preamble = render_log_key_preamble("abc123", "comp")
        assert "PactFormatter" in preamble
        assert "pact_key" in preamble


class TestLogKeyFormat:
    def test_matches_expected_pattern(self):
        preamble = render_log_key_preamble("abc123", "pricing")
        # Extract the key from the preamble
        match = re.search(r'PACT:\w+:\w+', preamble)
        assert match is not None
        assert match.group(0) == "PACT:abc123:pricing"

    def test_key_is_extractable_by_regex(self):
        """The PACT key should be extractable by the same regex used in signals.py."""
        preamble = render_log_key_preamble("abc123", "pricing")
        pattern = re.compile(r"PACT:(\w+):(\w+)")
        match = pattern.search(preamble)
        assert match is not None
        assert match.group(1) == "abc123"
        assert match.group(2) == "pricing"


class TestProjectIdHash:
    def test_deterministic(self):
        h1 = project_id_hash("/tmp/my_project")
        h2 = project_id_hash("/tmp/my_project")
        assert h1 == h2

    def test_different_paths(self):
        h1 = project_id_hash("/tmp/project_a")
        h2 = project_id_hash("/tmp/project_b")
        assert h1 != h2

    def test_six_chars(self):
        h = project_id_hash("/tmp/test")
        assert len(h) == 6

    def test_alphanumeric(self):
        h = project_id_hash("/tmp/test")
        assert all(c in "0123456789abcdef" for c in h)


class TestHandoffBriefWithLogKey:
    def _make_contract(self) -> ComponentContract:
        return ComponentContract(
            component_id="pricing",
            name="Pricing Engine",
            description="Calculates prices",
            functions=[
                FunctionContract(
                    name="calculate_price",
                    description="Calculate price",
                    inputs=[FieldSpec(name="unit_id", type_ref="str")],
                    output_type="float",
                ),
            ],
        )

    def test_handoff_contains_preamble(self):
        contract = self._make_contract()
        preamble = render_log_key_preamble("abc123", "pricing")
        brief = render_handoff_brief(
            component_id="pricing",
            contract=contract,
            contracts={"pricing": contract},
            log_key_preamble=preamble,
        )
        assert "LOG KEY PREAMBLE" in brief
        assert "PACT:abc123:pricing" in brief

    def test_handoff_without_preamble(self):
        contract = self._make_contract()
        brief = render_handoff_brief(
            component_id="pricing",
            contract=contract,
            contracts={"pricing": contract},
        )
        assert "LOG KEY PREAMBLE" not in brief

    def test_code_author_includes_log_key(self):
        """Verify the code author prompt includes log key instructions."""
        from pact.agents.code_author import CODE_SYSTEM
        assert "PACT log key" in CODE_SYSTEM
        assert "log statements" in CODE_SYSTEM
