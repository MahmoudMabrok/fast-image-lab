#!/usr/bin/env bash
# Submit one generation, follow it to completion, and report the latency.
#   ./scripts/trial.sh [base_url] [prompt]
set -euo pipefail

BASE="${1:-http://127.0.0.1:8080}"
PROMPT="${2:-A miniature bonsai tree in a black ceramic pot, morning light through a shoji screen, soft shadows, quiet zen mood}"
KEY="${ANOTHERLAB_API_KEY:-}"

auth=()
if [ -n "$KEY" ]; then auth=(-H "Authorization: Bearer $KEY"); fi

command -v jq >/dev/null || { echo "jq is required" >&2; exit 1; }

echo "==> health"
curl -sf "${auth[@]}" "$BASE/api/health" | jq '{ready, device, device_type, ttl_seconds, counts}'

echo
echo "==> submitting"
START=$(date +%s)
JOB=$(curl -sf "${auth[@]}" -H 'Content-Type: application/json' \
  -d "$(jq -n --arg p "$PROMPT" '{prompt:$p,width:512,height:512,steps:4,cfg:1}')" \
  "$BASE/api/generate")
ID=$(echo "$JOB" | jq -r .id)
echo "job $ID"

for _ in $(seq 1 1800); do
  sleep 2
  JOB=$(curl -sf "${auth[@]}" "$BASE/api/generations/$ID")
  STATUS=$(echo "$JOB" | jq -r .status)
  PCT=$(echo "$JOB" | jq -r '(.progress * 100 | floor)')
  ETA=$(echo "$JOB" | jq -r '.eta_seconds // 0')
  printf '\r  %-9s %3s%%  ~%ss left   ' "$STATUS" "$PCT" "$ETA"
  case "$STATUS" in
    succeeded|failed|cancelled|expired) break ;;
  esac
done
echo

ELAPSED=$(( $(date +%s) - START ))
STATUS=$(echo "$JOB" | jq -r .status)
if [ "$STATUS" != "succeeded" ]; then
  echo "job ended as $STATUS" >&2
  echo "$JOB" | jq '{status, error}' >&2
  exit 1
fi

OUT="${OUT:-out.png}"
curl -sf "${auth[@]}" "$BASE/api/images/$ID" -o "$OUT"
SIZE=$(wc -c < "$OUT" | tr -d ' ')
echo "==> done in ${ELAPSED}s"
echo "    $OUT ($SIZE bytes)"
echo "$JOB" | jq '{width, height, steps, seed, expires_in, image_url}'
