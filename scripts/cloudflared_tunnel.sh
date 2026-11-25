#!/usr/bin/env bash
# Publish Fast Image Lab through a Cloudflare quick tunnel.
#   ./scripts/cloudflared_tunnel.sh [machine]
set -euo pipefail

MACHINE="${1:-bonsai}"
cd "$(dirname "$0")/.."

echo "==> Uploading the tunnel unit and installer to $MACHINE"
kube-labs exec "$MACHINE" -- mkdir -p /srv/anotherlab/systemd /srv/anotherlab/scripts
kube-labs cp systemd/anotherlab-tunnel.service "$MACHINE":/srv/anotherlab/systemd/anotherlab-tunnel.service
kube-labs cp scripts/remote_tunnel.sh "$MACHINE":/srv/anotherlab/scripts/remote_tunnel.sh

kube-labs exec "$MACHINE" -- bash /srv/anotherlab/scripts/remote_tunnel.sh
