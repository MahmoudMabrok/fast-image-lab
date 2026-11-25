#!/usr/bin/env bash
# Install or repair the named Cloudflare tunnel that publishes Fast Image Lab.
#   ./scripts/named_tunnel.sh [machine] [hostname]
#
# The tunnel itself must already exist on the machine:
#   cloudflared tunnel login
#   cloudflared tunnel create fast-image-lab
# and its DNS route must point at it:
#   cloudflared tunnel route dns fast-image-lab <hostname>
set -euo pipefail

MACHINE="${1:-bonsai}"
HOSTNAME="${2:-${TUNNEL_HOSTNAME:-fast-image-lab.mo3ta.top}}"
PORT="${PORT:-8080}"
TUNNEL_NAME="${TUNNEL_NAME:-fast-image-lab}"
cd "$(dirname "$0")/.."

echo "==> Uploading the tunnel unit and installer to $MACHINE"
kube-labs exec "$MACHINE" -- mkdir -p /srv/anotherlab/systemd /srv/anotherlab/scripts
kube-labs cp systemd/cloudflared.service "$MACHINE":/srv/anotherlab/systemd/cloudflared.service
kube-labs cp scripts/remote_named_tunnel.sh "$MACHINE":/srv/anotherlab/scripts/remote_named_tunnel.sh

kube-labs exec "$MACHINE" -- env \
  "PORT=$PORT" \
  "TUNNEL_HOSTNAME=$HOSTNAME" \
  "TUNNEL_NAME=$TUNNEL_NAME" \
  bash /srv/anotherlab/scripts/remote_named_tunnel.sh
