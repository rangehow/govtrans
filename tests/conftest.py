"""Test bootstrap. Sets hermetic env BEFORE any app import (engine and
settings are created at import time), and creates the schema in a temp
SQLite file.
"""
from __future__ import annotations

import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="govtrans-test-")
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_tmp}/test.db")
os.environ.setdefault("GOVTRANS_ALLOW_MISSING_KEYS", "true")
os.environ.setdefault("GOVTRANS_ENV", "test")

from apps.api.db import Base, engine  # noqa: E402

# Import all model modules so Base.metadata is complete before create_all.
from services.orchestrator import models as _m1  # noqa: E402,F401
from services.retrieval import models as _m2  # noqa: E402,F401
from services.terminology import models as _m3  # noqa: E402,F401
from services.corpus import models as _m4  # noqa: E402,F401
from evaluation import models as _m5  # noqa: E402,F401
from pipelines.style_distillation import models as _m6  # noqa: E402,F401

Base.metadata.create_all(engine)
