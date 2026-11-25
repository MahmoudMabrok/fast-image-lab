#!/usr/bin/env bash
# Install and run a Cloudflare quick tunnel for the AnotherLab gateway.
#
# A quick tunnel needs no Cloudflare account: cloudflared asks the edge for a
# random https://<name>.trycloudflare.com hostname and proxies it to
# 127.0.0.1:8080. The hostname changes whenever the service restarts, so this
# script also writes it into ANOTHERLAB_PUBLIC_URL and restarts the gateway.
#
# For a stable hostname, create a named tunnel with `cloudflared tunnel login`
# and `cloudflared tunnel create`, then point its config at 127.0.0.1:8080.
set -euo pipefail

PORT="${PORT:-8080}"
BIN=/usr/local/bin/cloudflared
UNIT=/etc/systemd/system/anotherlab-tunnel.service
APP_DIR="/srv/anotherlab"
ENV_FILE=/etc/anotherlab.env

log() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

log "cloudflared"
if [ ! -x "$BIN" ]; then
  case "$(uname -m)" in
    x86_64|amd64) ARCH=amd64 ;;
    aarch64|arm64) ARCH=arm64 ;;
    *) echo "unsupported architecture: $(uname -m)" >&2; exit 1 ;;
  esac
  curl -fsSL -o "$BIN" \
    "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-$ARCH"
  chmod 755 "$BIN"
fi
"$BIN" --version

log "systemd unit"
install -m 644 "$APP_DIR/systemd/anotherlab-tunnel.service" "$UNIT"
systemctl daemon-reload
systemctl enable anotherlab-tunnel.service >/dev/null

# Only accept a URL emitted after this restart, not one from a previous run.
SINCE=$(date '+%Y-%m-%d %H:%M:%S')
systemctl restart anotherlab-tunnel.service

log "Waiting for the tunnel URL"
URL=""
for _ in $(seq 1 45); do
  URL=$(journalctl -u anotherlab-tunnel.service --since "$SINCE" --no-pager 2>/dev/null \
    | grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' | tail -n 1 || true)
  [ -n "$URL" ] && break
  sleep 2
done

if [ -z "$URL" ]; then
  echo "cloudflared did not publish a URL; last log lines:" >&2
  journalctl -u anotherlab-tunnel.service -n 40 --no-pager >&2
  exit 1
fi

log "Public URL"
echo "$URL"

log "Pointing ANOTHERLAB_PUBLIC_URL at the tunnel"
touch "$ENV_FILE"
if grep -q '^ANOTHERLAB_PUBLIC_URL=' "$ENV_FILE"; then
  sed -i "s|^ANOTHERLAB_PUBLIC_URL=.*|ANOTHERLAB_PUBLIC_URL=$URL|" "$ENV_FILE"
else
  echo "ANOTHERLAB_PUBLIC_URL=$URL" >> "$ENV_FILE"
fi
systemctl restart anotherlab.service

log "Checking the tunnel end to end"
for _ in $(seq 1 30); do
  if curl -fsS --max-time 10 "$URL/api/health" | grep -q '"ready"'; then
    break
  fi
  sleep 2
done
curl -fsS --max-time 10 "$URL/api/health" | jq '{ready, device, comfy_version}' || true
echo
echo "UI:  $URL"
echo "MCP: $URL/mcp"
