"""Arbiter integration — HTTP client for the Arbiter trust gate.

Phase 8.5: POST access_graph.json to Arbiter /register endpoint.
Handles HUMAN_GATE responses and soak requirements.
"""

from __future__ import annotations

import json
import logging
import os
from urllib.request import Request, urlopen
from urllib.error import URLError

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class ArbiterResponse(BaseModel):
    """Response from Arbiter /register endpoint."""
    human_gate_required: bool = False
    soak_requirements: dict = {}
    blast_radius: dict = {}
    trust_summary: dict = {}
    raw: dict = {}


def resolve_arbiter_endpoint(config_endpoint: str = "") -> str:
    """Resolve Arbiter endpoint from config or environment."""
    return config_endpoint or os.environ.get("ARBITER_ENDPOINT", "")


async def register_with_arbiter(
    endpoint: str,
    access_graph: dict,
) -> ArbiterResponse:
    """POST access_graph.json to Arbiter /register endpoint.

    Returns ArbiterResponse with gate decisions.
    """
    url = f"{endpoint.rstrip('/')}/register"
    body = json.dumps(access_graph).encode()
    req = Request(url, data=body, headers={"Content-Type": "application/json"})

    try:
        with urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
    except URLError as e:
        logger.error("Arbiter registration failed: %s", e)
        return ArbiterResponse(raw={"error": str(e)})
    except Exception as e:
        logger.error("Arbiter registration error: %s", e)
        return ArbiterResponse(raw={"error": str(e)})

    return ArbiterResponse(
        human_gate_required=data.get("HUMAN_GATE") == "required",
        soak_requirements=data.get("soak_requirements", {}),
        blast_radius=data.get("blast_radius", {}),
        trust_summary=data.get("trust_summary", {}),
        raw=data,
    )
