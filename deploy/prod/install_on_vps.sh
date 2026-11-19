#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/apps/postzyo"
DOMAIN="${1:-postzyo.com}"
APP_INTERNAL_PORT="${APP_INTERNAL_PORT:-18010}"
STOP_TIMEOUT_SECONDS="${POSTZYO_STOP_TIMEOUT_SECONDS:-660}"
COMPOSE_FILE="deploy/prod/docker-compose.prod.yml"
ACME_ROOT="/var/lib/postzyo-acme"
TLS_DIR="/etc/letsencrypt/live/${DOMAIN}"
SITE_CONFIG="/etc/nginx/sites-available/postzyo"
ENABLED_SITE="/etc/nginx/sites-enabled/postzyo"
export APP_INTERNAL_PORT
compose=(docker compose -f "${COMPOSE_FILE}")

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
fi

if ! docker compose version >/dev/null 2>&1; then
  apt-get update
  apt-get install -y docker-compose-plugin
fi

apt-get update
apt-get install -y nginx certbot python3-certbot-nginx git

mkdir -p "${APP_DIR}"
if [ ! -d "${APP_DIR}/.git" ]; then
  git clone https://github.com/prarit0097/SocialMediaAutomation.git "${APP_DIR}"
else
  git -C "${APP_DIR}" pull --rebase
fi

cd "${APP_DIR}"

render_proxy_config() {
  POSTZYO_TLS_DIR="${TLS_DIR}" POSTZYO_ACME_ROOT="${ACME_ROOT}" \
    bash deploy/nginx/render_host_proxy.sh "$1" "${DOMAIN}" "${APP_INTERNAL_PORT}"
}

if [ -e "${SITE_CONFIG}" ] \
  && ! cmp -s "${SITE_CONFIG}" <(render_proxy_config bootstrap) \
  && ! cmp -s "${SITE_CONFIG}" <(render_proxy_config production); then
  echo "[ERROR] Existing Nginx configuration detected. Review a rendered proxy configuration separately." >&2
  exit 1
fi

if [ ! -f .env ]; then
  cp .env.example .env
  echo "[INFO] .env created from .env.example. Set the production environment and rerun the installer."
  exit 1
fi

if ! grep -q "^POSTGRES_PASSWORD=" .env; then
  echo "POSTGRES_PASSWORD=change-this-strong-db-password" >> .env
fi
if ! grep -q "^APP_INTERNAL_PORT=" .env; then
  echo "APP_INTERNAL_PORT=${APP_INTERNAL_PORT}" >> .env
fi

if ! grep -q "^DATABASE_URL=" .env; then
  echo "DATABASE_URL=postgres://social_admin:change-this-strong-db-password@db:5432/social_automation" >> .env
fi
if ! grep -q "^REDIS_URL=" .env; then
  echo "REDIS_URL=redis://redis:6379/0" >> .env
fi
if ! grep -q "^CACHE_BACKEND=" .env; then
  echo "CACHE_BACKEND=redis" >> .env
fi

start_app() {
  local db_ready=false
  "${compose[@]}" build
  "${compose[@]}" up -d db redis
  for _ in {1..30}; do
    if "${compose[@]}" exec -T db sh -c 'pg_isready -h 127.0.0.1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
      db_ready=true
      break
    fi
    sleep 2
  done
  if [ "${db_ready}" = false ]; then
    echo "[ERROR] PostgreSQL did not become ready." >&2
    return 1
  fi

  "${compose[@]}" --profile legacy-drain stop --timeout "${STOP_TIMEOUT_SECONDS}" web beat worker worker-insights worker-legacy
  "${compose[@]}" run --rm --no-deps web python manage.py migrate --noinput
  "${compose[@]}" run --rm --no-deps web python manage.py collectstatic --noinput
  "${compose[@]}" up -d
}

start_app

apply_proxy_config() {
  local mode="$1" scratch
  scratch=$(mktemp -d)
  if ! render_proxy_config "${mode}" >"${scratch}/candidate"; then
    rm -f "${scratch}/candidate"
    rmdir "${scratch}"
    return 1
  fi
  if [ -f "${SITE_CONFIG}" ]; then
    cp "${SITE_CONFIG}" "${scratch}/previous"
  fi
  install -m 0644 "${scratch}/candidate" "${SITE_CONFIG}"
  ln -sfn "${SITE_CONFIG}" "${ENABLED_SITE}"
  if ! nginx -t || ! systemctl reload-or-restart nginx; then
    if [ -f "${scratch}/previous" ]; then
      install -m 0644 "${scratch}/previous" "${SITE_CONFIG}"
    else
      rm -f "${SITE_CONFIG}" "${ENABLED_SITE}"
    fi
    rm -f "${scratch}/candidate" "${scratch}/previous"
    rmdir "${scratch}"
    return 1
  fi
  rm -f "${scratch}/candidate" "${scratch}/previous"
  rmdir "${scratch}"
}

install -d -m 0755 "${ACME_ROOT}"
if [ ! -r "${TLS_DIR}/fullchain.pem" ] || [ ! -r "${TLS_DIR}/privkey.pem" ]; then
  if [ -e "${SITE_CONFIG}" ] && ! cmp -s "${SITE_CONFIG}" <(render_proxy_config bootstrap); then
    echo "[ERROR] Existing proxy detected. Verify its certificate lineage before applying the new proxy configuration." >&2
    exit 1
  fi
  apply_proxy_config bootstrap
  certbot certonly --webroot -w "${ACME_ROOT}" --cert-name "${DOMAIN}" \
    -d "${DOMAIN}" -d "www.${DOMAIN}" --non-interactive --agree-tos \
    -m "${CERTBOT_EMAIL:-admin@${DOMAIN}}" --deploy-hook 'systemctl reload nginx'
fi

if ! openssl x509 -in "${TLS_DIR}/fullchain.pem" -noout -checkend 0; then
  echo "[ERROR] Verify the certificate expiration before applying the proxy configuration." >&2
  exit 1
fi
for site_name in "${DOMAIN}" "www.${DOMAIN}"; do
  if ! openssl x509 -in "${TLS_DIR}/fullchain.pem" -noout -checkhost "${site_name}" \
    | grep -Fq 'does match certificate'; then
    echo "[ERROR] The existing certificate is invalid for ${site_name}. Verify it before applying the proxy configuration." >&2
    exit 1
  fi
done
apply_proxy_config production

echo "[OK] Deployment done. Check: https://${DOMAIN}/"
