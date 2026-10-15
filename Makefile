.PHONY: install migrate seed api worker web build test e2e lint

install:
	cd backend && uv venv .venv && uv pip install -e ".[dev]"
	cd frontend && npm ci

migrate:
	cd backend && .venv/bin/alembic upgrade head

seed:
	cd backend && .venv/bin/python -m app.cli seed-sample

api:
	cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

worker:
	cd backend && .venv/bin/python -m app.worker.runner

web:
	cd frontend && npm run dev

build:
	cd frontend && npm run build && rm -rf ../backend/static && cp -r dist ../backend/static

test:
	cd backend && .venv/bin/pytest

e2e:
	cd frontend && npx playwright test

lint:
	cd backend && .venv/bin/ruff check app
	cd frontend && npx tsc -b --noEmit
