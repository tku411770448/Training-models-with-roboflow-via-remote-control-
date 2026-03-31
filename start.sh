#!/usr/bin/env bash
set -euo pipefail

PORT="${PORT:-8000}"
HOST="${HOST:-127.0.0.1}"

cp -n .env.example .env 2>/dev/null || true

APP_UID_CURRENT="$(id -u)"
APP_GID_CURRENT="$(id -g)"
export APP_UID="${APP_UID:-$APP_UID_CURRENT}"
export APP_GID="${APP_GID:-$APP_GID_CURRENT}"
mkdir -p backend/jobs/projects

echo "[info] Using APP_UID=${APP_UID} APP_GID=${APP_GID}"
echo "[1/2] Starting container in detached mode..."
docker compose up -d --build

echo "[2/2] Checking container status..."
docker compose ps

echo
echo "✅ Website URL: http://${HOST}:${PORT}"
echo "If you use another machine/IP, replace ${HOST} with your host IP."
echo "Logs: docker compose logs -f web"
echo "Stop: docker compose down"
