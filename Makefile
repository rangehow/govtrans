.PHONY: scan test test-unit dev-api dev-web migrate deploy-init deploy-up deploy-doctor deploy-backup deploy-package

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

deploy-init:
	./scripts/deploy.sh init

deploy-up:
	./scripts/deploy.sh up

deploy-doctor:
	./scripts/deploy.sh doctor

deploy-backup:
	./scripts/deploy.sh backup

deploy-package:
	./scripts/package_deployment.sh
