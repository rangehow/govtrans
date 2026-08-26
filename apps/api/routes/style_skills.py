"""Queryable style-Skill registry used by task configuration."""
from fastapi import APIRouter

from services.orchestrator.skills import skill_catalog

router = APIRouter(prefix="/api/style-skills", tags=["style"])


@router.get("")
def list_style_skills():
    return {"skills": skill_catalog()}
