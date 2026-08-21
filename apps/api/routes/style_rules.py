from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from apps.api.db import SessionLocal
from pipelines.style_distillation.models import StyleRule

router = APIRouter(prefix="/api/style-rules", tags=["style"])


@router.get("")
def list_rules(status: str | None = None):
    with SessionLocal() as session:
        stmt = select(StyleRule).order_by(StyleRule.confidence.desc())
        if status:
            stmt = stmt.where(StyleRule.status == status)
        rows = session.execute(stmt.limit(200)).scalars().all()
        return {"rules": [
            {"id": r.id, "rule": r.rule, "zh_pattern": r.zh_pattern,
             "en_rendering": r.en_rendering, "source_count": r.source_count,
             "domains": r.domains, "confidence": r.confidence, "status": r.status,
             "version": r.version, "examples": r.examples,
             "counterexamples": r.counterexamples}
            for r in rows
        ]}


class ReviewRuleRequest(BaseModel):
    status: str = Field(pattern="^(approved|rejected)$")


@router.post("/{rule_id}/review")
def review_rule(rule_id: str, body: ReviewRuleRequest):
    with SessionLocal() as session:
        rule = session.get(StyleRule, rule_id)
        if not rule:
            raise HTTPException(404, "rule not found")
        rule.status = body.status
        session.commit()
        return {"id": rule_id, "status": rule.status}
