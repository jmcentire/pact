"""Tests for shaping phase — schemas, config, project I/O, pitch utils, agent."""

from __future__ import annotations

import json
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic import ValidationError

from pact.schemas_shaping import (
    Affordance,
    Appetite,
    Breadboard,
    Connection,
    FitCheck,
    Place,
    RabbitHole,
    RabbitHoleStatus,
    RegionMap,
    ShapingPitch,
    ShapingStatus,
)
from pact.config import (
    GlobalConfig,
    ProjectConfig,
    load_global_config,
    load_project_config,
)
from pact.pitch_utils import (
    build_pitch_context_for_handoff,
    extract_pitch_summary,
    format_pitch_summary,
)


# ── Schema Tests ─────────────────────────────────────────────────────


class TestShapingSchemas:
    """Validate Pydantic models for Shape Up shaping phase."""

    def test_minimal_pitch(self):
        """ShapingPitch with only required fields."""
        pitch = ShapingPitch(problem="Users can't log in", appetite=Appetite.small)
        assert pitch.problem == "Users can't log in"
        assert pitch.appetite == Appetite.small
        assert pitch.no_gos == []
        assert pitch.rabbit_holes == []
        assert pitch.solution_breadboard is None
        assert pitch.status == ShapingStatus.raw

    def test_full_pitch(self):
        """ShapingPitch with all optional fields populated."""
        pitch = ShapingPitch(
            problem="Build login system",
            appetite=Appetite.big,
            solution_breadboard=Breadboard(
                places=[Place(name="Login Page")],
                affordances=[Affordance(name="Submit")],
                connections=[Connection(
                    from_place="Login Page",
                    to_place="Dashboard",
                    affordance="Submit",
                )],
            ),
            solution_region_map=RegionMap(
                regions={"Auth": ["Login", "Token"]},
            ),
            rabbit_holes=[
                RabbitHole(description="OAuth complexity"),
            ],
            no_gos=["No LDAP support"],
            fit_check=FitCheck(appetite=Appetite.big, fits=True),
            status=ShapingStatus.shaped,
        )
        assert len(pitch.solution_breadboard.places) == 1
        assert len(pitch.rabbit_holes) == 1
        assert pitch.fit_check.fits is True
        assert pitch.no_gos == ["No LDAP support"]

    def test_pitch_json_roundtrip(self):
        """ShapingPitch serializes and deserializes without loss."""
        pitch = ShapingPitch(
            problem="Test problem",
            appetite=Appetite.small,
            rabbit_holes=[
                RabbitHole(
                    description="Scope creep",
                    status=RabbitHoleStatus.patched,
                    mitigation="Timebox it",
                ),
            ],
            no_gos=["No dark mode"],
        )
        json_str = pitch.model_dump_json()
        restored = ShapingPitch.model_validate_json(json_str)
        assert restored.problem == pitch.problem
        assert restored.appetite == pitch.appetite
        assert len(restored.rabbit_holes) == 1
        assert restored.rabbit_holes[0].status == RabbitHoleStatus.patched
        assert restored.no_gos == ["No dark mode"]

    def test_empty_problem_raises(self):
        """ShapingPitch requires non-empty problem."""
        with pytest.raises(ValidationError):
            ShapingPitch(problem="", appetite=Appetite.small)

    def test_affordance_requires_name(self):
        """Affordance requires non-empty name."""
        with pytest.raises(ValidationError):
            Affordance(name="")

    def test_place_requires_name(self):
        """Place requires non-empty name."""
        with pytest.raises(ValidationError):
            Place(name="")

    def test_appetite_enum_values(self):
        """Appetite enum has exactly small and big."""
        assert set(Appetite) == {Appetite.small, Appetite.big}

    def test_shaping_status_values(self):
        """ShapingStatus enum covers full lifecycle."""
        values = {s.value for s in ShapingStatus}
        assert values == {"raw", "shaped", "reviewed", "accepted", "rejected"}

    def test_rabbit_hole_default_status(self):
        """RabbitHole defaults to needs_review."""
        rh = RabbitHole(description="Some risk")
        assert rh.status == RabbitHoleStatus.needs_review

    def test_breadboard_empty(self):
        """Empty breadboard is valid."""
        bb = Breadboard()
        assert bb.places == []
        assert bb.affordances == []
        assert bb.connections == []


# ── Config Tests ─────────────────────────────────────────────────────


class TestShapingConfig:
    """Validate shaping fields in GlobalConfig and ProjectConfig."""

    def test_global_defaults(self):
        """GlobalConfig has correct shaping defaults."""
        cfg = GlobalConfig()
        assert cfg.shaping is False
        assert cfg.shaping_depth == "standard"
        assert cfg.shaping_rigor == "moderate"
        assert cfg.shaping_budget_pct == 0.15

    def test_project_defaults(self):
        """ProjectConfig shaping fields default to None (use global)."""
        cfg = ProjectConfig()
        assert cfg.shaping is None
        assert cfg.shaping_depth is None
        assert cfg.shaping_rigor is None
        assert cfg.shaping_budget_pct is None

    def test_load_global_config_with_shaping(self, tmp_path):
        """load_global_config reads shaping fields from YAML."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "shaping: true\n"
            "shaping_depth: thorough\n"
            "shaping_rigor: strict\n"
            "shaping_budget_pct: 0.25\n"
        )
        cfg = load_global_config(config_file)
        assert cfg.shaping is True
        assert cfg.shaping_depth == "thorough"
        assert cfg.shaping_rigor == "strict"
        assert cfg.shaping_budget_pct == 0.25

    def test_load_project_config_with_shaping(self, tmp_path):
        """load_project_config reads shaping fields from pact.yaml."""
        pact_yaml = tmp_path / "pact.yaml"
        pact_yaml.write_text(
            "budget: 50.00\n"
            "shaping: true\n"
            "shaping_depth: light\n"
        )
        cfg = load_project_config(tmp_path)
        assert cfg.shaping is True
        assert cfg.shaping_depth == "light"
        assert cfg.shaping_rigor is None  # Not specified, stays None

    def test_load_project_config_without_shaping(self, tmp_path):
        """load_project_config works fine without shaping fields."""
        pact_yaml = tmp_path / "pact.yaml"
        pact_yaml.write_text("budget: 10.00\n")
        cfg = load_project_config(tmp_path)
        assert cfg.shaping is None


# ── Project Pitch I/O Tests ──────────────────────────────────────────


class TestProjectPitchIO:
    """Validate pitch save/load on ProjectManager."""

    def test_save_and_load_pitch(self, tmp_path):
        """Pitch roundtrips through ProjectManager."""
        from pact.project import ProjectManager
        pm = ProjectManager(tmp_path)
        pm.init()

        pitch = ShapingPitch(
            problem="Test problem",
            appetite=Appetite.small,
            no_gos=["No X"],
        )
        pm.save_pitch(pitch)

        loaded = pm.load_pitch()
        assert loaded is not None
        assert loaded.problem == "Test problem"
        assert loaded.appetite == Appetite.small
        assert loaded.no_gos == ["No X"]

    def test_load_pitch_no_file(self, tmp_path):
        """load_pitch returns None when no pitch file exists."""
        from pact.project import ProjectManager
        pm = ProjectManager(tmp_path)
        assert pm.load_pitch() is None

    def test_pitch_path(self, tmp_path):
        """pitch_path is in decomposition directory."""
        from pact.project import ProjectManager
        pm = ProjectManager(tmp_path)
        assert pm.pitch_path == tmp_path / ".pact" / "decomposition" / "pitch.json"


# ── Pitch Utils Tests ────────────────────────────────────────────────


class TestPitchUtils:
    """Validate pitch summary extraction and formatting."""

    def _make_pitch(self, **overrides):
        defaults = dict(
            problem="Users cannot log in",
            appetite=Appetite.small,
            no_gos=["No LDAP"],
            rabbit_holes=[RabbitHole(description="OAuth complexity")],
        )
        defaults.update(overrides)
        return ShapingPitch(**defaults)

    def test_extract_summary(self):
        """extract_pitch_summary returns correct counts."""
        pitch = self._make_pitch()
        summary = extract_pitch_summary(pitch)
        assert summary.appetite == "small"
        assert summary.rabbit_hole_count == 1
        assert summary.no_go_count == 1
        assert summary.problem_statement == "Users cannot log in"

    def test_extract_summary_with_breadboard(self):
        """extract_pitch_summary counts breadboard places."""
        pitch = self._make_pitch(
            solution_breadboard=Breadboard(
                places=[Place(name="Login"), Place(name="Dashboard")],
            ),
        )
        summary = extract_pitch_summary(pitch)
        assert summary.breadboard_place_count == 2

    def test_format_summary_nonempty(self):
        """format_pitch_summary returns non-empty string."""
        pitch = self._make_pitch()
        result = format_pitch_summary(pitch)
        assert len(result) > 0
        assert "small" in result
        assert "Rabbit Holes: 1" in result

    def test_handoff_context_contains_appetite(self):
        """build_pitch_context_for_handoff includes appetite."""
        pitch = self._make_pitch()
        ctx = build_pitch_context_for_handoff(pitch)
        assert "Appetite: small" in ctx

    def test_handoff_context_contains_rabbit_holes(self):
        """build_pitch_context_for_handoff includes rabbit holes."""
        pitch = self._make_pitch()
        ctx = build_pitch_context_for_handoff(pitch)
        assert "OAuth complexity" in ctx

    def test_handoff_context_contains_no_gos(self):
        """build_pitch_context_for_handoff includes no-gos."""
        pitch = self._make_pitch()
        ctx = build_pitch_context_for_handoff(pitch)
        assert "No LDAP" in ctx

    def test_handoff_context_contains_breadboard(self):
        """build_pitch_context_for_handoff includes breadboard places."""
        pitch = self._make_pitch(
            solution_breadboard=Breadboard(
                places=[Place(name="Login Page")],
                connections=[Connection(
                    from_place="Login Page",
                    to_place="Dashboard",
                    affordance="Submit",
                )],
            ),
        )
        ctx = build_pitch_context_for_handoff(pitch)
        assert "Login Page" in ctx
        assert "Dashboard" in ctx


# ── Shaper Agent Tests ───────────────────────────────────────────────


class TestShaperAgent:
    """Validate shaper agent logic."""

    def _make_agent_mock(self):
        """Create a mock AgentBase."""
        agent = AsyncMock()
        agent.assess = AsyncMock()
        agent.close = AsyncMock()
        return agent

    def _make_pitch_result(self, **overrides):
        defaults = dict(
            problem="Generated problem",
            appetite=Appetite.small,
            no_gos=["No X"],
        )
        defaults.update(overrides)
        return ShapingPitch(**defaults)

    @pytest.mark.asyncio
    async def test_shape_light(self):
        """Light depth: 1 LLM call, core fields only."""
        from pact.agents.shaper import Shaper

        agent = self._make_agent_mock()
        result_pitch = self._make_pitch_result()
        agent.assess.return_value = (result_pitch, 100, 200)

        shaper = Shaper(agent=agent, shaping_depth="light")
        result = await shaper.shape(
            task="Build login",
            sops="Follow OAuth",
            budget_used=5.0,
            budget_total=100.0,
        )

        assert result.problem == "Generated problem"
        assert result.appetite == Appetite.big  # 95% remaining > 50% threshold
        assert agent.assess.call_count == 1

    @pytest.mark.asyncio
    async def test_shape_standard(self):
        """Standard depth: 1 LLM call with breadboards."""
        from pact.agents.shaper import Shaper

        result_pitch = self._make_pitch_result(
            solution_breadboard=Breadboard(places=[Place(name="Login")]),
            rabbit_holes=[RabbitHole(description="Risk")],
        )
        agent = self._make_agent_mock()
        agent.assess.return_value = (result_pitch, 100, 200)

        shaper = Shaper(agent=agent, shaping_depth="standard")
        result = await shaper.shape(
            task="Build login",
            sops="SOPs",
            budget_used=5.0,
            budget_total=100.0,
        )

        assert result.solution_breadboard is not None
        assert result.rabbit_holes is not None
        assert agent.assess.call_count == 1

    @pytest.mark.asyncio
    async def test_shape_thorough(self):
        """Thorough depth: 2 LLM calls."""
        from pact.agents.shaper import Shaper

        call1 = self._make_pitch_result(
            solution_breadboard=Breadboard(places=[Place(name="Login")]),
        )
        call2 = self._make_pitch_result(
            solution_breadboard=Breadboard(places=[Place(name="Login")]),
            solution_region_map=RegionMap(regions={"Auth": ["Login"]}),
            fit_check=FitCheck(appetite=Appetite.big, fits=True),
        )
        agent = self._make_agent_mock()
        agent.assess.side_effect = [(call1, 100, 200), (call2, 100, 200)]

        shaper = Shaper(
            agent=agent, shaping_depth="thorough", shaping_budget_pct=0.50,
        )
        result = await shaper.shape(
            task="Build platform",
            sops="SOPs",
            budget_used=5.0,
            budget_total=100.0,
        )

        assert result.solution_region_map is not None
        assert result.fit_check is not None
        assert agent.assess.call_count == 2

    @pytest.mark.asyncio
    async def test_shape_empty_task_raises(self):
        """Empty task raises ValueError."""
        from pact.agents.shaper import Shaper

        agent = self._make_agent_mock()
        shaper = Shaper(agent=agent)

        with pytest.raises(ValueError, match="non-empty"):
            await shaper.shape(task="", sops="SOPs")

    @pytest.mark.asyncio
    async def test_shape_budget_exceeded(self):
        """Budget cap exceeded raises BudgetExceededError."""
        from pact.agents.shaper import BudgetExceededError, Shaper

        agent = self._make_agent_mock()
        shaper = Shaper(agent=agent, shaping_budget_pct=0.15)

        with pytest.raises(BudgetExceededError):
            await shaper.shape(
                task="Task",
                sops="SOPs",
                budget_used=20.0,
                budget_total=100.0,
            )

    @pytest.mark.asyncio
    async def test_shape_strict_llm_failure(self):
        """Strict rigor: LLM failure raises ShapingLLMError."""
        from pact.agents.shaper import Shaper, ShapingLLMError

        agent = self._make_agent_mock()
        agent.assess.side_effect = RuntimeError("API down")

        shaper = Shaper(agent=agent, shaping_rigor="strict")

        with pytest.raises(ShapingLLMError):
            await shaper.shape(
                task="Task",
                sops="SOPs",
                budget_used=2.0,
                budget_total=100.0,
            )

    @pytest.mark.asyncio
    async def test_shape_moderate_llm_failure_returns_partial(self):
        """Moderate rigor: LLM failure returns partial pitch."""
        from pact.agents.shaper import Shaper

        agent = self._make_agent_mock()
        agent.assess.side_effect = RuntimeError("API down")

        shaper = Shaper(agent=agent, shaping_rigor="moderate")
        result = await shaper.shape(
            task="Build feature",
            sops="SOPs",
            budget_used=2.0,
            budget_total=100.0,
        )

        assert result.status == ShapingStatus.raw
        assert result.problem == "Build feature"[:500]

    @pytest.mark.asyncio
    async def test_appetite_maps_to_big(self):
        """High remaining budget maps to big appetite."""
        from pact.agents.shaper import Shaper

        result_pitch = self._make_pitch_result()
        agent = self._make_agent_mock()
        agent.assess.return_value = (result_pitch, 100, 200)

        shaper = Shaper(agent=agent, shaping_depth="light", appetite_threshold=0.5)
        result = await shaper.shape(
            task="Task",
            sops="SOPs",
            budget_used=10.0,
            budget_total=100.0,
        )
        assert result.appetite == Appetite.big  # 90% remaining > 50% threshold

    @pytest.mark.asyncio
    async def test_appetite_maps_to_small(self):
        """Low remaining budget maps to small appetite."""
        from pact.agents.shaper import Shaper

        result_pitch = self._make_pitch_result()
        agent = self._make_agent_mock()
        agent.assess.return_value = (result_pitch, 100, 200)

        shaper = Shaper(
            agent=agent, shaping_depth="light",
            appetite_threshold=0.5, shaping_budget_pct=1.0,
        )
        result = await shaper.shape(
            task="Task",
            sops="SOPs",
            budget_used=70.0,
            budget_total=100.0,
        )
        assert result.appetite == Appetite.small  # 30% remaining < 50% threshold


# ── Handoff Brief Integration Tests ──────────────────────────────────


class TestHandoffBriefShaping:
    """Validate pitch_context integration in handoff brief."""

    def test_handoff_brief_with_pitch_context(self):
        """render_handoff_brief includes SHAPING CONTEXT section."""
        from pact.interface_stub import render_handoff_brief
        from pact.schemas import ComponentContract

        contract = ComponentContract(
            component_id="test",
            name="Test Component",
            description="A test",
        )
        brief = render_handoff_brief(
            component_id="test",
            contract=contract,
            contracts={"test": contract},
            pitch_context="Appetite: small\nNo-Gos:\n  - No LDAP",
        )
        assert "SHAPING CONTEXT" in brief
        assert "No LDAP" in brief

    def test_handoff_brief_without_pitch_context(self):
        """render_handoff_brief omits SHAPING CONTEXT when empty."""
        from pact.interface_stub import render_handoff_brief
        from pact.schemas import ComponentContract

        contract = ComponentContract(
            component_id="test",
            name="Test Component",
            description="A test",
        )
        brief = render_handoff_brief(
            component_id="test",
            contract=contract,
            contracts={"test": contract},
        )
        assert "SHAPING CONTEXT" not in brief


# ── Lifecycle Tests ──────────────────────────────────────────────────


class TestShapingLifecycle:
    """Validate shape phase in lifecycle state machine."""

    def test_phase_order_includes_shape(self):
        """Shape phase exists between interview and decompose."""
        from pact.lifecycle import advance_phase
        from pact.schemas import RunState

        state = RunState(id="x", project_dir="/tmp", phase="interview")
        advance_phase(state)
        assert state.phase == "shape"

        advance_phase(state)
        assert state.phase == "decompose"

    def test_shape_in_runstate_literal(self):
        """RunState accepts 'shape' as a valid phase."""
        from pact.schemas import RunState

        state = RunState(id="x", project_dir="/tmp", phase="shape")
        assert state.phase == "shape"
