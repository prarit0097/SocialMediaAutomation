#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/postzyo}"
APP_INTERNAL_PORT="${APP_INTERNAL_PORT:-18010}"

cd "${APP_DIR}"
git pull --rebase
APP_INTERNAL_PORT="${APP_INTERNAL_PORT}" docker compose -f deploy/prod/docker-compose.prod.yml up -d --build
APP_INTERNAL_PORT="${APP_INTERNAL_PORT}" docker compose -f deploy/prod/docker-compose.prod.yml exec -T web python manage.py migrate
APP_INTERNAL_PORT="${APP_INTERNAL_PORT}" docker compose -f deploy/prod/docker-compose.prod.yml exec -T web python manage.py collectstatic --noinput

echo "[OK] Updated"
