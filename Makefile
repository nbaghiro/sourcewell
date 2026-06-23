.PHONY: help up down install dev worker web-install web test test-fast lint fmt typecheck migrate revision check

help:
	@echo "up/down      docker compose (postgres+mailpit)"
	@echo "install      uv sync (backend deps)"
	@echo "dev          run API on :8901 with reload"
	@echo "worker       run the runtime worker"
	@echo "web-install  npm install (frontend deps)"
	@echo "web          run the React app on :8900"
	@echo "test         full test suite (needs 'make up')"
	@echo "test-fast    unit/api tests only (no DB)"
	@echo "lint/fmt     ruff check / format"
	@echo "typecheck    mypy"
	@echo "migrate      alembic upgrade head"
	@echo "check        lint + typecheck + test"

up:
	docker compose up -d

down:
	docker compose down

install:
	cd backend && uv sync

dev:
	cd backend && uv run uvicorn app.main:app --host 0.0.0.0 --port 8901 --reload

worker:
	cd backend && uv run python -m app.runtime.worker

seed:
	cd backend && uv run python -m app.demo.seed

web-install:
	cd frontend && npm install

web:
	cd frontend && npm run dev

test:
	cd backend && uv run pytest -q

test-fast:
	cd backend && uv run pytest -q -m "not db"

lint:
	cd backend && uv run ruff check .

fmt:
	cd backend && uv run ruff format .

typecheck:
	cd backend && uv run mypy app tests

migrate:
	cd backend && uv run alembic upgrade head

revision:
	cd backend && uv run alembic revision --autogenerate -m "$(m)"

check: lint typecheck test
