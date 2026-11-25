#!/usr/bin/env bash
# Internal helper: submit a generation via the local gateway API, poll to a
# terminal state, and print the final job JSON + the image byte count.
#   _poll_job.sh <width> <height> <steps> <prompt>
set -euo pipefail
BASE="${BASE:-http://127.0.0.1:8080}"
W="$1"; H="$2"; S="$3"; P="$4"
OUT="${OUT:-/tmp/anotherlab_out.png}"

JOB=$(curl -sf -H 'Content-Type: application/json' \
  -d "{\"prompt\":\"$P\",\"width\":$W,\"height\":$H,\"steps\":$S,\"cfg\":1}" \
  "$BASE/api/generate")
ID=$(echo "$JOB" | jq -r .id)
START=$(date +%s)
echo "job $ID (${W}x${H}, ${S} steps)"
for _ in $(seq 1 1500); do
  sleep 4
  JOB=$(curl -sf "$BASE/api/generations/$ID" 2>/dev/null || echo '{"status":"poll-error"}')
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
echo "status=$STATUS elapsed=${ELAPSED}s"
echo "$JOB" | jq '{status,error,seed,width,height,steps,expires_in}'
if [ "$STATUS" = "succeeded" ]; then
  curl -sf "$BASE/api/images/$ID" -o "$OUT"
  echo "image: $OUT ($(wc -c < "$OUT" | tr -d ' ') bytes)"
fi
