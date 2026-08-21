.PHONY: scan test test-unit dev-api dev-web migrate

# Secret scan — must pass before every commit (also wired into CI later).
scan:
	python scripts/scan_secrets.py

# PYTEST_DISABLE_PLUGIN_AUTOLOAD: hermetic, reproducible test runs.
test-unit:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -m unit -q

test:
	PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/ -q

migrate:
	python -m alembic upgrade head

dev-api:
	python -m uvicorn apps.api.main:app --reload --port 8100

dev-web:
	cd apps/web && npm run dev
