#!/usr/bin/env bash
# Rebuild the late-chat-service Docker image and replace the running
# container. Idempotent. Used by the deploy webhook on this repo.
#
# late-chat-service runs as a container (not a systemd service) because
# the image bundles ffmpeg, which is easier to ship that way.
set -euo pipefail

REPO="${REPO:-/root/late-chat-service}"
APP_DIR="$REPO"
IMAGE="late-chat-service:dev"
CONTAINER="late-chat-service"
HOST_PORT="9100"
HOST_GATEWAY="host.docker.internal"

log() { echo "[deploy] $*"; }

if [ ! -d "$APP_DIR" ]; then
  echo "App dir not found: $APP_DIR" >&2
  exit 1
fi

log "loading env"
[ -f /root/.env.backup ] || { echo "missing /root/.env.backup" >&2; exit 1; }
[ -f /root/.env.auth ]    || { echo "missing /root/.env.auth" >&2; exit 1; }
# shellcheck disable=SC1091
SSO_SECRET="$(grep '^SSO_BRIDGE_SECRET=' /root/.env.backup | cut -d= -f2-)"
# shellcheck disable=SC1091
LATE_SECRET="$(grep '^LATE_AUTH_SECRET=' /root/.env.auth | cut -d= -f2-)"

if [ -z "$SSO_SECRET" ] || [ -z "$LATE_SECRET" ]; then
  echo "SSO_BRIDGE_SECRET or LATE_AUTH_SECRET missing" >&2
  exit 1
fi

log "ensuring data and attachment dirs"
mkdir -p /data/late-chat-service /var/lib/late-attachments

log "building image $IMAGE"
cd "$APP_DIR"
docker build -t "$IMAGE" . > /tmp/late-chat-service-build.log 2>&1 || {
  tail -20 /tmp/late-chat-service-build.log >&2
  exit 1
}

log "replacing container"
if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER}$"; then
  docker stop "$CONTAINER" >/dev/null 2>&1 || true
  docker rm "$CONTAINER" >/dev/null 2>&1 || true
fi

docker run -d \
  --name "$CONTAINER" \
  --restart unless-stopped \
  -p "${HOST_PORT}:9100" \
  --add-host="${HOST_GATEWAY}:host-gateway" \
  -e "SSO_BRIDGE_SECRET=$SSO_SECRET" \
  -e "SQLITE_PATH=/data/late-chat-service/chat.db" \
  -e "ATTACHMENT_DIR=/var/lib/late-attachments" \
  -e "LATE_AUTH_URL=http://${HOST_GATEWAY}:9300" \
  -e "LATE_AUTH_SECRET=$LATE_SECRET" \
  -e "KV_WEBHOOK_URL=" \
  -e "KV_WEBHOOK_SECRET=" \
  -e "SHARED_INTERNAL_SECRET=$SSO_SECRET" \
  -v /data/late-chat-service:/data/late-chat-service \
  -v /var/lib/late-attachments:/var/lib/late-attachments \
  -v "$APP_DIR":/app \
  "$IMAGE" >/dev/null

sleep 2
log "waiting for /healthz"
for i in {1..30}; do
  if curl -fsS "http://127.0.0.1:${HOST_PORT}/healthz" >/dev/null 2>&1; then
    log "late-chat-service is healthy on :${HOST_PORT}"
    exit 0
  fi
  sleep 1
done

log "ERROR: late-chat-service did not become healthy within 30s"
docker logs "$CONTAINER" --tail 20 >&2
exit 1
