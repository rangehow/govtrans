from fastapi import APIRouter

from apps.api.config import get_settings

router = APIRouter(tags=["meta"])


@router.get("/healthz")
def healthz():
    settings = get_settings()
    return {
        "status": "ok",
        "env": settings.govtrans_env,
        "llm_configured": bool(
            settings.dashscope_api_key and settings.dashscope_api_key.get_secret_value()
        ),
        "pipeline_version": settings.pipeline_version,
    }
