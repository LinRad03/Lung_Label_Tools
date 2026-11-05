#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
pidfile=runtime/service.pid
[[ -f "$pidfile" ]] || { echo 'NO_PID_FILE'; exit 1; }
pid="$(cat "$pidfile")"
[[ "$pid" =~ ^[0-9]+$ ]] || { echo 'INVALID_PID_FILE' >&2; exit 1; }
[[ -r "/proc/$pid/cmdline" ]] || { echo 'PROCESS_NOT_FOUND' >&2; exit 1; }
if ! tr '\0' ' ' <"/proc/$pid/cmdline" | grep -q 'uvicorn annotator.app:app'; then
  echo 'PID_IDENTITY_MISMATCH' >&2
  exit 1
fi
kill -TERM "$pid"
echo 'SERVICE_STOP_REQUESTED'
