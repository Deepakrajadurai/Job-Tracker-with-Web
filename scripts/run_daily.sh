#!/usr/bin/env bash
# Wrapper for cron. Loads secrets from .env (if present) and runs every profile.
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .env ]; then set -a; . ./.env; set +a; fi
if [ -d .venv ]; then . .venv/bin/activate; fi
# Runs each file in profiles/. Falls back to config.yaml when there are no profiles.
exec python -m jobtracker.run --all-profiles
