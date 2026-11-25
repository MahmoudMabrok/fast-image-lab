#!/usr/bin/env bash
# Point an existing named Cloudflare tunnel at the Fast Image Lab gateway,
# (re)start it under systemd, and re-verify it over the public hostname.
#
# Expects TUNNEL_HOSTNAME (and optionally TUNNEL_NAME / TUNNEL_ID). Credentials
# already created by `cloudflared tunnel create` are reused; nothing secret is
# written by this script.
set -euo pipefail

PORT="${PORT:-8080}"
HOSTNAME="${TUNNEL_HOSTNAME:?set TUNNEL_HOSTNAME}"
TUNNEL_NAME="${TUNNEL_NAME:-fast-image-lab}"
BIN=/usr/local/bin/cloudflared
CONFIG=/etc/cloudflared/config.yml
UNIT=/etc/systemd/system/cloudflared.service
APP_DIR=/srv/anotherlab
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

TUNNEL_ID="${TUNNEL_ID:-}"
if [ -z "$TUNNEL_ID" ]; then
  TUNNEL_ID=$("$BIN" tunnel list 2>/dev/null \
    | awk -v name="$TUNNEL_NAME" '$2==name {print $1; exit}' || true)
fi
if [ -z "$TUNNEL_ID" ]; then
  echo "no tunnel named '$TUNNEL_NAME'; create one first:" >&2
  echo "  cloudflared tunnel login" >&2
  echo "  cloudflared tunnel create $TUNNEL_NAME" >&2
  echo "  cloudflared tunnel route dns $TUNNEL_NAME $HOSTNAME" >&2
  exit 1
fi

CREDS="${CREDENTIALS_FILE:-/root/.cloudflared/$TUNNEL_ID.json}"
[ -r "$CREDS" ] || { echo "missing credentials: $CREDS" >&2; exit 1; }

log "ingress config"
mkdir -p /etc/cloudflared
cat > "$CONFIG" <<EOF
tunnel: $TUNNEL_ID
credentials-file: $CREDS
ingress:
  - hostname: $HOSTNAME
    service: http://127.0.0.1:$PORT
  - service: http_status:404
EOF
cat "$CONFIG"

log "systemd unit"
install -m 644 "$APP_DIR/systemd/cloudflared.service" "$UNIT"
systemctl daemon-reload
systemctl enable cloudflared.service >/dev/null
systemctl restart cloudflared.service

log "Pointing ANOTHERLAB_PUBLIC_URL at the named host"
URL="https://$HOSTNAME"
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
curl -fsS --max-time 10 "$URL/api/health" | jq '{ready, device, comfy_version, ttl_seconds}' || true
echo
echo "UI:  $URL"
echo "MCP: $URL/mcp"
