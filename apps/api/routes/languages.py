"""Language catalog and pair capability endpoints."""

from fastapi import APIRouter, HTTPException

from services.languages import language_catalog_payload, language_pair_payload


router = APIRouter(prefix="/api/languages", tags=["languages"])


@router.get("")
def list_languages():
    return language_catalog_payload()


@router.get("/capabilities")
def get_language_pair_capabilities(source: str, target: str):
    try:
        return language_pair_payload(source, target)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
