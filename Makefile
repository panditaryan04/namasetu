# NamaSetu developer commands. Run `make help` for the list.
# On Windows, use Git Bash plus GNU make (e.g. `winget install ezwinports.make`).

SHELL := bash
BACKEND := backend
FRONTEND := frontend

ifeq ($(OS),Windows_NT)
  VENV_BIN := $(BACKEND)/.venv/Scripts
  PYTHON_BOOT ?= py -3
else
  VENV_BIN := $(BACKEND)/.venv/bin
  PYTHON_BOOT ?= python3
endif
PY := $(abspath $(VENV_BIN))/python

# LOCAL (no-Docker) mode: SQLite + in-process Celery. Overridden when DATABASE_URL is set in the environment.
LOCAL_ENV := DATABASE_URL=sqlite:///./data/local.db REDIS_URL=
DB_ENV := $(if $(DATABASE_URL),,$(LOCAL_ENV))

.DEFAULT_GOAL := help
.PHONY: help up down logs venv install lint format typecheck test test-backend test-frontend \
        test-integration smoke smoke-local dev-local migrate seed embedded index eval eval-db bench

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

# ---------- Docker ----------
up: ## Build and start the full stack (migrates + seeds SAMPLE data on start)
	docker compose up --build -d --wait
	@echo "UI  -> http://localhost:5173"
	@echo "API -> http://localhost:8000/docs"

down: ## Stop the stack
	docker compose down

logs: ## Tail stack logs
	docker compose logs -f --tail=100

smoke: ## Smoke-test a running Docker stack
	$(PY) $(BACKEND)/scripts/smoke_test.py --base-url http://localhost:8000 --frontend-url http://localhost:5173

# ---------- Local (no Docker) ----------
venv:
	test -x $(PY) || $(PYTHON_BOOT) -m venv $(BACKEND)/.venv

install: venv ## Install backend + frontend dependencies locally
	$(PY) -m pip install -q -r $(BACKEND)/requirements-dev.txt
	cd $(FRONTEND) && npm ci

migrate: venv ## Apply database migrations (LOCAL SQLite unless DATABASE_URL is set)
	cd $(BACKEND) && $(DB_ENV) $(PY) -m alembic upgrade head

seed: migrate ## Seed SAMPLE data, idempotent (LOCAL mode reads data/sample/embedded_db.json)
	cd $(BACKEND) && $(DB_ENV) $(PY) -m app.db.seed

index: venv ## Train the ranker (+ build embeddings when SEMANTIC_MODE != off)
	cd $(BACKEND) && $(DB_ENV) $(PY) scripts/build_index.py

embedded: venv ## Regenerate data/sample/embedded_db.json from the sample files
	cd $(BACKEND) && $(PY) scripts/build_embedded_db.py

dev-local: install seed ## Run API (SQLite, eager tasks, MOCK services) + Vite dev server, no Docker
	@echo "LOCAL mode: SQLite + in-process tasks + MOCK ICD-11/ABHA"
	cd $(BACKEND) && $(LOCAL_ENV) $(PY) -m uvicorn app.main:app --port 8000 & \
	cd $(FRONTEND) && npm run dev

smoke-local: seed ## Start the API in LOCAL mode, smoke-test it, then stop it
	cd $(BACKEND) && $(LOCAL_ENV) $(PY) -m uvicorn app.main:app --port 8001 & pid=$$!; \
	$(PY) $(BACKEND)/scripts/smoke_test.py --base-url http://127.0.0.1:8001 --wait 30; rc=$$?; \
	kill $$pid; exit $$rc

# ---------- Quality ----------
lint: ## ruff + black --check + eslint + prettier --check
	cd $(BACKEND) && $(PY) -m ruff check . && $(PY) -m black --check .
	cd $(FRONTEND) && npm run lint && npm run format:check

format: ## Auto-format backend and frontend
	cd $(BACKEND) && $(PY) -m ruff check --fix . && $(PY) -m black .
	cd $(FRONTEND) && npm run format

typecheck: ## mypy (strict) + tsc --noEmit
	cd $(BACKEND) && $(PY) -m mypy app scripts
	cd $(FRONTEND) && npm run typecheck

test: test-backend test-frontend ## All tests

test-backend: ## pytest with coverage (Postgres tests skip unless TEST_POSTGRES_URL is set)
	cd $(BACKEND) && $(PY) -m pytest --cov=app --cov-report=term-missing

test-integration: ## Postgres integration tests against the compose database (run `make up` first)
	cd $(BACKEND) && TEST_POSTGRES_URL=postgresql+psycopg://namasetu:namasetu@127.0.0.1:5432/postgres \
	  $(PY) -m pytest tests/integration/test_postgres.py

test-frontend: ## vitest
	cd $(FRONTEND) && npm test

eval: ## Full evaluation on a fresh SAMPLE database; rewrites docs/eval-results.* and the README block; fails on any safety check
	cd $(BACKEND) && $(PY) scripts/run_eval.py

eval-db: ## Evaluate the configured database and store the run (shown on the Evaluation page)
	cd $(BACKEND) && $(DB_ENV) $(PY) scripts/run_eval.py --use-db --check

bench: ## Latency benchmark of the main API paths (fresh SAMPLE database); rewrites docs/bench.md and the README block
	cd $(BACKEND) && $(PY) scripts/bench.py
