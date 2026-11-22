#!/usr/bin/env bash
# Deploys or updates the Job Tracker on the home server. Run it from the project folder:
#   bash scripts/deploy-home-server.sh
set -euo pipefail
cd "$(dirname "$0")/.."

DATA_DIR="${JOBTRACKER_DATA_DIR:-/mnt/hdd/jobtracker}"

if ! mountpoint -q /mnt/hdd; then
  echo "Warning: /mnt/hdd is not mounted. Data would land on the system disk." >&2
  read -r -p "Continue anyway? [y/N] " answer
  [[ "$answer" == [yY] ]] || exit 1
fi

echo "Creating data folders in $DATA_DIR"
sudo mkdir -p "$DATA_DIR/data" "$DATA_DIR/profiles" "$DATA_DIR/logs"
sudo chown -R "$(id -u):$(id -g)" "$DATA_DIR"

ENV_FILE="$DATA_DIR/.env"
if [ ! -f "$ENV_FILE" ] && [ -f .env ]; then
  echo "Moving .env from the project folder to $ENV_FILE (next to the data, safe from code updates)"
  mv .env "$ENV_FILE"
fi
if [ ! -f "$ENV_FILE" ]; then
  cp .env.example "$ENV_FILE"
  secret=$(python3 -c "import secrets; print(secrets.token_urlsafe(48))")
  sed -i "s|^JOBTRACKER_SECRET_KEY=.*|JOBTRACKER_SECRET_KEY=$secret|" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "Created $ENV_FILE with a new JOBTRACKER_SECRET_KEY."
  echo "Fill in the other values with:  nano $ENV_FILE   then run this script again."
  exit 0
fi

export JOBTRACKER_UID="$(id -u)" JOBTRACKER_GID="$(id -g)" JOBTRACKER_DATA_DIR="$DATA_DIR"
export JOBTRACKER_ENV_FILE="$ENV_FILE" APP_VERSION="${APP_VERSION:-$(git rev-parse HEAD 2>/dev/null || echo manual)}"
export COMPOSE_PROFILES="$(grep -E '^COMPOSE_PROFILES=' "$ENV_FILE" | tail -1 | cut -d= -f2- || true)"
docker compose up -d --build

echo
echo "Waiting for the app to answer..."
for _ in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8000/healthz >/dev/null 2>&1; then
    echo "Job Tracker is running on 127.0.0.1:8000"
    docker logs jobtracker 2>&1 | grep -E "Single sign-on|Not configured" || true
    echo
    echo "If not done yet, publish it to your tailnet with HTTPS:"
    echo "  sudo tailscale serve --bg --https=443 http://127.0.0.1:8000"
    echo "  tailscale serve status        # shows the https://home-server.<tailnet>.ts.net address"
    exit 0
  fi
  sleep 2
done
echo "The app did not start. See: docker logs jobtracker" >&2
exit 1
