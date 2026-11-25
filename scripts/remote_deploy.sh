#!/usr/bin/env bash
# Install the gateway on the machine and (re)start both services.
# Expects the repo to already be at /srv/anotherlab (see scripts/deploy.sh).
set -euo pipefail

APP_DIR="${APP_DIR:-/srv/anotherlab}"
VENV="$APP_DIR/.venv"
ROOT="${ROOT:-/srv}"

log() { printf '\n\033[1;33m==> %s\033[0m\n' "$*"; }

log "Python venv for the gateway"
[ -d "$VENV" ] || python3 -m venv "$VENV"
# shellcheck disable=SC1091
. "$VENV/bin/activate"
pip install --no-cache-dir --quiet --upgrade pip
pip install --no-cache-dir --quiet -r "$APP_DIR/requirements.txt"

log "Data directory"
mkdir -p "$APP_DIR/data/images"

log "Environment file"
if [ ! -f /etc/anotherlab.env ]; then
  install -m 600 "$APP_DIR/.env.example" /etc/anotherlab.env
  echo "  wrote /etc/anotherlab.env from .env.example"
else
  echo "  keeping existing /etc/anotherlab.env"
fi

if [ -n "${PUBLIC_URL:-}" ]; then
  sed -i "s|^ANOTHERLAB_PUBLIC_URL=.*|ANOTHERLAB_PUBLIC_URL=$PUBLIC_URL|" /etc/anotherlab.env
fi
if [ -n "${API_KEY:-}" ]; then
  sed -i "s|^ANOTHERLAB_API_KEY=.*|ANOTHERLAB_API_KEY=$API_KEY|" /etc/anotherlab.env
fi

log "systemd units"
install -m 644 "$APP_DIR/systemd/comfyui.service" /etc/systemd/system/comfyui.service
install -m 644 "$APP_DIR/systemd/anotherlab.service" /etc/systemd/system/anotherlab.service
systemctl daemon-reload
systemctl enable --now comfyui.service
systemctl enable --now anotherlab.service
systemctl restart anotherlab.service

log "Waiting for the gateway"
for _ in $(seq 1 45); do
  if curl -sf http://127.0.0.1:8080/api/health >/dev/null 2>&1; then
    break
  fi
  sleep 2
done
curl -s http://127.0.0.1:8080/api/health | jq '{ready, device, device_type, counts, ttl_seconds}' || true

log "Service state"
systemctl --no-pager --lines=0 status comfyui.service anotherlab.service | sed -n '1,20p' || true
