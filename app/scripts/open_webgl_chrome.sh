#!/usr/bin/env bash
set -euo pipefail
: "${ANNOTATOR_PILOT_DIR:?Set ANNOTATOR_PILOT_DIR to the controlled pilot directory}"
umask 077
profile="${ANNOTATOR_PILOT_DIR}/chrome_profile"
mkdir -p "$profile"
gl_args=(--enable-webgl --ignore-gpu-blocklist)
if [[ "${ANNOTATOR_SOFTWARE_GL:-0}" == "1" ]]; then
  gl_args=(--enable-webgl --use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader)
fi
exec google-chrome \
  --user-data-dir="$profile" \
  "${gl_args[@]}" \
  --disable-application-cache --disk-cache-size=1 \
  --new-window http://127.0.0.1:8767/
