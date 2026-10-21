#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
if [ ! -x .venv/bin/python ]; then python3 -m venv .venv; .venv/bin/pip install -r backend/requirements.lock; fi
if [ ! -d frontend/node_modules ]; then npm ci --prefix frontend; fi
export PYTHONPATH=backend APP_ENV=development DEMO_MODE=false
export DATABASE_URL=sqlite:///./runtime/aftercare.db STORAGE_BACKEND=local STORAGE_DIR=runtime/files
export PUBLIC_ORIGIN=http://127.0.0.1:5173 COOKIE_SECURE=false
.venv/bin/alembic -c backend/alembic.ini upgrade head
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --no-access-log &
api_pid=$!
.venv/bin/python -m app.worker &
worker_pid=$!
cleanup(){ kill "$api_pid" "$worker_pid" 2>/dev/null || true; }
trap cleanup EXIT INT TERM
npm run dev --prefix frontend
