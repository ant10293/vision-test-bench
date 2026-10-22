#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export LEARNLOOP_AUTO_SEED="${LEARNLOOP_AUTO_SEED:-1}"
export LEARNLOOP_WORKER_ENABLED="${LEARNLOOP_WORKER_ENABLED:-1}"
if [[ ! -x backend/.venv/bin/uvicorn ]]; then
  echo "Create the backend environment first: /usr/local/bin/python3.12 -m venv backend/.venv && backend/.venv/bin/pip install -e './backend[dev]'"
  exit 1
fi
if [[ ! -d frontend/node_modules ]]; then
  npm install --prefix frontend
fi
backend/.venv/bin/uvicorn app.main:app --app-dir backend --host 127.0.0.1 --port 8000 --reload &
API_PID=$!
trap 'kill $API_PID' EXIT
npm --prefix frontend run dev -- --host 127.0.0.1 --port 5173
