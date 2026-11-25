#!/usr/bin/env bash
# Upload the repo to a kube-labs machine and install/restart the services.
#   ./scripts/deploy.sh [machine]
set -euo pipefail

MACHINE="${1:-bonsai}"
cd "$(dirname "$0")/.."

echo "==> Uploading to $MACHINE:/srv/anotherlab"
kube-labs exec "$MACHINE" -- mkdir -p /srv/anotherlab
tar -czf - \
  --exclude .git --exclude .venv --exclude __pycache__ --exclude data \
  --exclude '*.pyc' . \
  | kube-labs exec "$MACHINE" -- tar -xzf - -C /srv/anotherlab

echo "==> Installing"
kube-labs exec "$MACHINE" -- bash /srv/anotherlab/scripts/remote_deploy.sh
