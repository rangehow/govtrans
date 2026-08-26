from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from apps.api.db import SessionLocal
from pipelines.style_distillation.mine import mine_candidate_rules
from pipelines.style_distillation.models import StyleRule

router = APIRouter(prefix="/api/style-rules", tags=["style"])


class MineRulesRequest(BaseModel):
    min_support: int = Field(default=2, ge=2, le=100)


@router.post("/mine")
def mine_rules(body: MineRulesRequest):
    return mine_candidate_rules(min_support=body.min_support, official_only=True)


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
             "activation_source": r.activation_source,
             "activated_at": r.activated_at.isoformat() if r.activated_at else None,
             "version": r.version, "examples": r.examples,
             "counterexamples": r.counterexamples}
            for r in rows
        ]}


class ReviewRuleRequest(BaseModel):
    status: str = Field(pattern="^(candidate|approved|rejected)$")


@router.post("/{rule_id}/review")
def review_rule(rule_id: str, body: ReviewRuleRequest):
    with SessionLocal() as session:
        rule = session.get(StyleRule, rule_id)
        if not rule:
            raise HTTPException(404, "rule not found")
        rule.status = body.status
        if body.status == "approved":
            rule.activation_source = "human"
            rule.activated_at = datetime.now(timezone.utc)
        else:
            rule.activation_source = None
            rule.activated_at = None
        session.commit()
        return {
            "id": rule_id,
            "status": rule.status,
            "activation_source": rule.activation_source,
        }
