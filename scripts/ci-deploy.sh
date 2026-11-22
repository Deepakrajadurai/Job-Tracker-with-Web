#!/usr/bin/env bash
# Called by the pipeline on home-server (self-hosted runner) after the tests passed.
#   backup -> keep the running image as "previous" -> build -> start -> health check -> roll back on failure
set -euo pipefail
cd "$(dirname "$0")/.."

export JOBTRACKER_DATA_DIR="${JOBTRACKER_DATA_DIR:-/mnt/hdd/jobtracker}"
export JOBTRACKER_ENV_FILE="${JOBTRACKER_ENV_FILE:-$JOBTRACKER_DATA_DIR/.env}"
export JOBTRACKER_UID="$(id -u)" JOBTRACKER_GID="$(id -g)"
export APP_VERSION="${APP_VERSION:-$(git rev-parse HEAD 2>/dev/null || echo dev)}"

if [ -f "$JOBTRACKER_ENV_FILE" ]; then
  export COMPOSE_PROFILES="$(grep -E '^COMPOSE_PROFILES=' "$JOBTRACKER_ENV_FILE" | tail -1 | cut -d= -f2- || true)"
fi

if [ ! -f "$JOBTRACKER_ENV_FILE" ]; then
  echo "Missing $JOBTRACKER_ENV_FILE. Run scripts/deploy-home-server.sh once by hand first." >&2
  exit 1
fi

echo "==> Backup of the databases"
if docker ps --format '{{.Names}}' | grep -qx jobtracker; then
  docker exec jobtracker python -m jobtracker.backup /srv/jobtracker/data || echo "Warning: backup failed" >&2
fi

echo "==> Keeping the running image as jobtracker:previous"
if docker image inspect jobtracker:latest >/dev/null 2>&1; then
  docker tag jobtracker:latest jobtracker:previous
fi

echo "==> Building version $APP_VERSION"
docker compose build --pull
docker compose up -d --force-recreate

echo "==> Health check"
for _ in $(seq 1 45); do
  answer=$(curl -fsS http://127.0.0.1:8000/healthz 2>/dev/null || true)
  if echo "$answer" | grep -q "\"version\": *\"$APP_VERSION\""; then
    echo "Deployed $APP_VERSION"
    docker image prune -f >/dev/null
    exit 0
  fi
  sleep 2
done

echo "Health check failed, rolling back to the previous image" >&2
docker logs --tail 50 jobtracker >&2 || true
if docker image inspect jobtracker:previous >/dev/null 2>&1; then
  docker tag jobtracker:previous jobtracker:latest
  docker compose up -d --no-build --force-recreate
fi
exit 1
