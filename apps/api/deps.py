"""Dependency injection for the API app."""
from __future__ import annotations

from fastapi import Request

from services.orchestrator.engine import Orchestrator


def get_orchestrator(request: Request) -> Orchestrator:
    return request.app.state.orchestrator
