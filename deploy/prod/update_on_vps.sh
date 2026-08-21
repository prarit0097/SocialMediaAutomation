#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/apps/postzyo}"
APP_INTERNAL_PORT="${APP_INTERNAL_PORT:-18010}"
COMPOSE_FILE="deploy/prod/docker-compose.prod.yml"

cd "${APP_DIR}"
git pull --rebase

# Build first, then publish static assets INTO the shared volume before the new
# containers start serving. Production uses ManifestStaticFilesStorage, so a page cannot
# render until staticfiles.json exists and matches the new build; collecting after
# `up -d` (the previous order) leaves a window where every request 500s.
APP_INTERNAL_PORT="${APP_INTERNAL_PORT}" docker compose -f "${COMPOSE_FILE}" build
APP_INTERNAL_PORT="${APP_INTERNAL_PORT}" docker compose -f "${COMPOSE_FILE}" run --rm --no-deps web python manage.py collectstatic --noinput

APP_INTERNAL_PORT="${APP_INTERNAL_PORT}" docker compose -f "${COMPOSE_FILE}" up -d
APP_INTERNAL_PORT="${APP_INTERNAL_PORT}" docker compose -f "${COMPOSE_FILE}" exec -T web python manage.py migrate

echo "[OK] Updated"
