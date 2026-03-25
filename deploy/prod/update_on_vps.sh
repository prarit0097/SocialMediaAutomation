#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/apps/postzyo}"
APP_INTERNAL_PORT="${APP_INTERNAL_PORT:-18010}"
COMPOSE_FILE="deploy/prod/docker-compose.prod.yml"

cd "${APP_DIR}"
git pull --rebase
APP_INTERNAL_PORT="${APP_INTERNAL_PORT}" docker compose -f "${COMPOSE_FILE}" up -d --build
APP_INTERNAL_PORT="${APP_INTERNAL_PORT}" docker compose -f "${COMPOSE_FILE}" exec -T web python manage.py migrate
APP_INTERNAL_PORT="${APP_INTERNAL_PORT}" docker compose -f "${COMPOSE_FILE}" exec -T web python manage.py collectstatic --noinput

echo "[OK] Updated"
