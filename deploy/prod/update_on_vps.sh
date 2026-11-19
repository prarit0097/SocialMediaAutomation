#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/apps/postzyo}"
APP_INTERNAL_PORT="${APP_INTERNAL_PORT:-18010}"
STOP_TIMEOUT_SECONDS="${POSTZYO_STOP_TIMEOUT_SECONDS:-660}"
COMPOSE_FILE="deploy/prod/docker-compose.prod.yml"
export APP_INTERNAL_PORT
compose=(docker compose -f "${COMPOSE_FILE}")

cd "${APP_DIR}"
git pull --rebase

# Prepare the image before taking traffic offline. Quiesce all writers, including
# legacy web dispatch and any drain worker, while the queue schema is migrated.
"${compose[@]}" build
"${compose[@]}" --profile legacy-drain stop --timeout "${STOP_TIMEOUT_SECONDS}" web beat worker worker-insights worker-legacy
"${compose[@]}" run --rm --no-deps web python manage.py migrate --noinput
"${compose[@]}" run --rm --no-deps web python manage.py collectstatic --noinput

"${compose[@]}" up -d

echo "[OK] Updated"
