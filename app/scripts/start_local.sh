#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
: "${ANNOTATOR_DB:?Set ANNOTATOR_DB to the controlled SQLite path}"
: "${ANNOTATOR_DATA_ROOTS:?Set ANNOTATOR_DATA_ROOTS to approved data root(s)}"
if [[ ! -f frontend/dist/index.html ]]; then
  echo 'FRONTEND_BUILD_MISSING' >&2
  exit 2
fi
exec .venv/bin/python -m uvicorn annotator.app:app --host 127.0.0.1 --port "${ANNOTATOR_PORT:-8765}" --workers 1 --no-access-log
