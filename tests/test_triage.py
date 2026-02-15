"""Tests for triage agent — error-to-component mapping and diagnostic reports."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pact.agents.triage import (
    DiagnosticResult,
    TriageResult,
    generate_diagnostic_report,
    triage_signal,
)
from pact.schemas import (
    ComponentContract,
    DecompositionNode,
    DecompositionTree,
    FunctionContract,
    FieldSpec,
    TestResults,
    TypeSpec,
)
from pact.schemas_monitoring import Incident, Signal


def _make_contracts() -> dict[str, ComponentContract]:
    return {
        "pricing": ComponentContract(
            component_id="pricing",
            name="Pricing Engine",
            description="Calculates prices",
            functions=[
                FunctionContract(
                    name="calculate_price",
                    description="Calculate nightly price",
                    inputs=[FieldSpec(name="unit_id", type_ref="str")],
                    output_type="PriceResult",
                ),
            ],
            types=[TypeSpec(name="PriceResult", kind="struct")],
        ),
        "inventory": ComponentContract(
            component_id="inventory",
            name="Inventory Service",
            description="Manages available units",
            functions=[
                FunctionContract(
                    name="check_availability",
                    description="Check if unit is available",
                    inputs=[FieldSpec(name="unit_id", type_ref="str")],
                    output_type="bool",
                ),
            ],
        ),
    }


def _make_tree() -> DecompositionTree:
    return DecompositionTree(
        root_id="root",
        nodes={
            "root": DecompositionNode(
                component_id="root",
                name="Root",
                description="Top",
                children=["pricing", "inventory"],
            ),
            "pricing": DecompositionNode(
                component_id="pricing",
                name="Pricing Engine",
                description="Prices",
                parent_id="root",
                depth=1,
            ),
            "inventory": DecompositionNode(
                component_id="inventory",
                name="Inventory",
                description="Inventory",
                parent_id="root",
                depth=1,
            ),
        },
    )


class TestTriageSignal:
    @pytest.mark.asyncio
    async def test_maps_to_known_component(self):
        agent = MagicMock()
        agent.assess = AsyncMock(return_value=(
            TriageResult(
                component_id="pricing",
                confidence=0.9,
                reasoning="Error mentions price calculation",
            ),
            100, 50,
        ))

        signal = Signal(
            source="log_file",
            raw_text="ERROR: calculate_price failed — NoneType has no attribute 'total'",
            timestamp=datetime.now().isoformat(),
        )
        project = MagicMock()
        tree = _make_tree()
        contracts = _make_contracts()

        result = await triage_signal(agent, signal, project, tree, contracts)
        assert result == "pricing"

    @pytest.mark.asyncio
    async def test_returns_none_for_unknown(self):
        agent = MagicMock()
        agent.assess = AsyncMock(return_value=(
            TriageResult(
                component_id="unknown",
                confidence=0.1,
                reasoning="Cannot determine source",
            ),
            100, 50,
        ))

        signal = Signal(
            source="manual",
            raw_text="Something went wrong",
            timestamp=datetime.now().isoformat(),
        )
        result = await triage_signal(agent, signal, MagicMock(), _make_tree(), _make_contracts())
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_low_confidence(self):
        agent = MagicMock()
        agent.assess = AsyncMock(return_value=(
            TriageResult(
                component_id="pricing",
                confidence=0.2,  # Below 0.3 threshold
                reasoning="Weak match",
            ),
            100, 50,
        ))

        signal = Signal(
            source="manual",
            raw_text="Generic error",
            timestamp=datetime.now().isoformat(),
        )
        result = await triage_signal(agent, signal, MagicMock(), _make_tree(), _make_contracts())
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_nonexistent_component(self):
        agent = MagicMock()
        agent.assess = AsyncMock(return_value=(
            TriageResult(
                component_id="nonexistent",
                confidence=0.9,
                reasoning="High confidence but wrong",
            ),
            100, 50,
        ))

        signal = Signal(
            source="manual",
            raw_text="Error",
            timestamp=datetime.now().isoformat(),
        )
        result = await triage_signal(agent, signal, MagicMock(), _make_tree(), _make_contracts())
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_for_empty_contracts(self):
        agent = MagicMock()
        signal = Signal(source="manual", raw_text="Error", timestamp="2024-01-01")
        result = await triage_signal(agent, signal, MagicMock(), _make_tree(), {})
        assert result is None


class TestGenerateDiagnosticReport:
    @pytest.mark.asyncio
    async def test_report_structure(self):
        agent = MagicMock()
        agent.assess = AsyncMock(return_value=(
            DiagnosticResult(
                summary="NoneType error in price calculation",
                error_analysis="Missing null check on price lookup result",
                component_context="Pricing engine calculates nightly rates",
                recommended_direction="Add null guard before accessing .total",
                severity="high",
                confidence=0.85,
            ),
            200, 100,
        ))

        incident = Incident(
            id="test123",
            project_dir="/tmp/proj",
            component_id="pricing",
            signals=[Signal(
                source="log_file",
                raw_text="NoneType has no attribute 'total'",
                timestamp="2024-01-01T00:00:00",
            )],
            created_at="2024-01-01T00:00:00",
            updated_at="2024-01-01T00:00:00",
        )

        contract = _make_contracts()["pricing"]
        report = await generate_diagnostic_report(
            agent, incident, MagicMock(),
            contract=contract,
            test_results=None,
            attempted_fixes=["Added try/except"],
        )

        assert report.incident_id == "test123"
        assert report.severity == "high"
        assert report.confidence == 0.85
        assert "NoneType" in report.summary
        assert report.attempted_fixes == ["Added try/except"]

    @pytest.mark.asyncio
    async def test_report_includes_contract_context(self):
        agent = MagicMock()
        agent.assess = AsyncMock(return_value=(
            DiagnosticResult(
                summary="Test",
                error_analysis="Test",
                component_context="Pricing component with calculate_price function",
                recommended_direction="Fix it",
                severity="medium",
                confidence=0.5,
            ),
            200, 100,
        ))

        incident = Incident(
            id="test",
            project_dir="/tmp",
            component_id="pricing",
            signals=[_make_signal()],
            created_at="2024-01-01",
            updated_at="2024-01-01",
        )

        report = await generate_diagnostic_report(
            agent, incident, MagicMock(),
            contract=_make_contracts()["pricing"],
            test_results=TestResults(total=10, passed=8, failed=2),
            attempted_fixes=[],
        )
        assert report.incident_id == "test"

    @pytest.mark.asyncio
    async def test_report_handles_agent_failure(self):
        agent = MagicMock()
        agent.assess = AsyncMock(side_effect=Exception("API timeout"))

        incident = Incident(
            id="test",
            project_dir="/tmp",
            signals=[_make_signal()],
            created_at="2024-01-01",
            updated_at="2024-01-01",
        )

        report = await generate_diagnostic_report(
            agent, incident, MagicMock(),
            contract=None,
            test_results=None,
            attempted_fixes=["Tried X"],
        )
        # Should still produce a report, just with fallback content
        assert report.incident_id == "test"
        assert report.confidence == 0.0
        assert "failed" in report.summary.lower() or "Manual" in report.recommended_direction


def _make_signal(text: str = "ERROR: test") -> Signal:
    return Signal(source="log_file", raw_text=text, timestamp="2024-01-01T00:00:00")
