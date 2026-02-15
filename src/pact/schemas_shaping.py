"""Pydantic v2 models for the Shape Up shaping phase.

Defines enums, models, and utilities for the shaping workflow:
Affordance, Place, Connection, Breadboard, RabbitHole, RegionMap,
FitCheck, ShapingPitch.

All models are optional — shaping is off by default.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ── Enums ────────────────────────────────────────────────────────────


class Appetite(StrEnum):
    """Time budget for a shaped pitch: 'small' (~1-2 weeks) or 'big' (~6 weeks)."""
    small = "small"
    big = "big"


class ShapingStatus(StrEnum):
    """Lifecycle status of a shaping pitch."""
    raw = "raw"
    shaped = "shaped"
    reviewed = "reviewed"
    accepted = "accepted"
    rejected = "rejected"


class RabbitHoleStatus(StrEnum):
    """Resolution status of an identified risk."""
    patched = "patched"
    out_of_bounds = "out_of_bounds"
    needs_review = "needs_review"


# ── Models ───────────────────────────────────────────────────────────


class Affordance(BaseModel):
    """A capability or action available in the breadboard UI sketch."""
    model_config = ConfigDict(populate_by_name=True)
    name: str = Field(..., min_length=1)
    description: Optional[str] = None


class Place(BaseModel):
    """A location or screen in the breadboard UI sketch."""
    model_config = ConfigDict(populate_by_name=True)
    name: str = Field(..., min_length=1)
    description: Optional[str] = None


class Connection(BaseModel):
    """A directional link between two places via an affordance."""
    model_config = ConfigDict(populate_by_name=True)
    from_place: str
    to_place: str
    affordance: str
    description: Optional[str] = None


class Breadboard(BaseModel):
    """A fat-marker UI sketch: places, affordances, connections."""
    model_config = ConfigDict(populate_by_name=True)
    places: list[Place] = Field(default_factory=list)
    affordances: list[Affordance] = Field(default_factory=list)
    connections: list[Connection] = Field(default_factory=list)


class RabbitHole(BaseModel):
    """An identified risk that could derail the project."""
    model_config = ConfigDict(populate_by_name=True)
    description: str
    status: RabbitHoleStatus = RabbitHoleStatus.needs_review
    mitigation: Optional[str] = None


class RegionMap(BaseModel):
    """High-level map grouping elements into named regions."""
    model_config = ConfigDict(populate_by_name=True)
    regions: dict[str, list[str]] = Field(default_factory=dict)
    annotations: Optional[str] = None


class FitCheck(BaseModel):
    """Assessment of whether the solution fits the appetite."""
    model_config = ConfigDict(populate_by_name=True)
    appetite: Appetite
    fits: bool
    notes: Optional[str] = None


class ShapingPitch(BaseModel):
    """Top-level shaping artifact: problem, solution shape, risks, boundaries.

    Supports incremental construction — only problem and appetite are required.
    """
    model_config = ConfigDict(populate_by_name=True)
    problem: str = Field(..., min_length=1)
    appetite: Appetite
    solution_breadboard: Optional[Breadboard] = None
    solution_region_map: Optional[RegionMap] = None
    rabbit_holes: list[RabbitHole] = Field(default_factory=list)
    no_gos: list[str] = Field(default_factory=list)
    fit_check: Optional[FitCheck] = None
    status: ShapingStatus = ShapingStatus.raw
