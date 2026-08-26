"""GovTrans API application factory.

Startup order matters: logging redaction first, then key validation
(fail-fast in production), then orchestrator + run recovery.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.api.config import get_settings
from apps.api.security import install_log_redaction, validate_required_keys
from services.orchestrator.engine import Orchestrator
from services.orchestrator.tofu_client import TofuClient
from services.corpus.sync import ScioSyncManager

logger = logging.getLogger("govtrans.api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    install_log_redaction(settings)
    validate_required_keys(settings)

    tofu = TofuClient(
        settings.tofu_base_url,
        api_key=(
            settings.tofu_api_key.get_secret_value()
            if settings.tofu_api_key
            else None
        ),
        timeout=settings.tofu_timeout_seconds,
        max_retries=settings.tofu_max_retries,
        max_concurrency=settings.tofu_max_concurrency,
        # With a configured direct-provider fallback, one explicit admission
        # refusal is enough; waiting here would add 30s before every role.
        admission_timeout=(
            0.0 if settings.direct_llm_fallback_on_overload
            else settings.tofu_admission_timeout_seconds
        ),
    )
    app.state.tofu = tofu
    app.state.orchestrator = Orchestrator(settings, tofu)
    app.state.scio_sync = ScioSyncManager()
    resumed = app.state.orchestrator.resume_active_runs()
    if resumed:
        logger.info("resumed %d active runs after restart", resumed)
    resumed_syncs = app.state.scio_sync.resume_active_jobs()
    if resumed_syncs:
        logger.info("resumed %d active corpus sync jobs after restart", resumed_syncs)
    yield
    await app.state.scio_sync.shutdown()
    await tofu.aclose()


def create_app() -> FastAPI:
    app = FastAPI(title="GovTrans API", version="0.1.0", lifespan=lifespan)
    from apps.api.routes import (
        benchmarks,
        corpus,
        health,
        languages,
        runs,
        style_rules,
        style_skills,
        terms,
    )

    app.include_router(health.router)
    app.include_router(languages.router)
    app.include_router(runs.router)
    app.include_router(terms.router)
    app.include_router(corpus.router)
    app.include_router(benchmarks.router)
    app.include_router(style_rules.router)
    app.include_router(style_skills.router)
    return app


app = create_app()
