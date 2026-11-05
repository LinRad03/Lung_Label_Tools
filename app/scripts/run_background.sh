#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p runtime
if [[ -f runtime/service.pid ]]; then
  old="$(cat runtime/service.pid)"
  if [[ "$old" =~ ^[0-9]+$ ]] && kill -0 "$old" 2>/dev/null; then
    echo 'SERVICE_ALREADY_RUNNING'
    exit 1
  fi
fi
nohup bash scripts/start_local.sh >runtime/service.log 2>&1 &
pid=$!
sleep 1
if ! kill -0 "$pid" 2>/dev/null; then
  echo 'SERVICE_START_FAILED' >&2
  exit 1
fi
printf '%s\n' "$pid" >runtime/service.pid
echo 'SERVICE_STARTED_LOCALHOST'
