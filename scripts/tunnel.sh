#!/usr/bin/env bash
# Open a path from this laptop to the gateway running on a kube-labs machine.
# Prefers the platform's public URL; falls back to a local port-forward.
set -euo pipefail

MACHINE="${1:-bonsai}"
PORT="${2:-8080}"

if URL=$(kube-labs url "$MACHINE" "$PORT" 2>/dev/null); then
  echo "$URL"
  exit 0
fi

echo "No public URL available; forwarding localhost:$PORT -> $MACHINE:$PORT" >&2
echo "Open http://127.0.0.1:$PORT once the tunnel is up." >&2
exec kube-labs port-forward "$MACHINE" "$PORT:$PORT"
