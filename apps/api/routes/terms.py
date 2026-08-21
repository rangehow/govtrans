from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.terminology import service as term_service

router = APIRouter(prefix="/api/terms", tags=["terminology"])


@router.get("")
def search_terms(q: str = "", top_k: int = 10):
    return {"terms": term_service.term_search(q, top_k) if q else []}


class CreateTermRequest(BaseModel):
    source_term: str
    preferred_target: str
    domain: str | None = None
    context: str | None = None


@router.post("", status_code=201)
def create_term(body: CreateTermRequest):
    term_id = term_service.term_create(
        body.source_term, body.preferred_target, domain=body.domain, context=body.context
    )
    return {"id": term_id}


@router.post("/{term_id}/deprecate")
def deprecate_term(term_id: str):
    if not term_service.term_deprecate(term_id):
        raise HTTPException(404, "term not found")
    return {"status": "deprecated"}
