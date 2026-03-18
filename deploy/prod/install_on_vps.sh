#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/opt/postzyo"
DOMAIN="${1:-postzyo.com}"
APP_INTERNAL_PORT="${APP_INTERNAL_PORT:-18010}"

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

if [ ! -f .env ]; then
  cp .env.example .env
  echo "[INFO] .env created from .env.example. Please edit it before first run."
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

cat >/etc/nginx/sites-available/postzyo <<NGINX
server {
    server_name ${DOMAIN} www.${DOMAIN};

    location / {
        proxy_pass http://127.0.0.1:${APP_INTERNAL_PORT};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
    }
}
NGINX

ln -sf /etc/nginx/sites-available/postzyo /etc/nginx/sites-enabled/postzyo
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx

APP_INTERNAL_PORT="${APP_INTERNAL_PORT}" docker compose -f deploy/prod/docker-compose.prod.yml up -d --build

sleep 5
APP_INTERNAL_PORT="${APP_INTERNAL_PORT}" docker compose -f deploy/prod/docker-compose.prod.yml exec -T web python manage.py migrate
APP_INTERNAL_PORT="${APP_INTERNAL_PORT}" docker compose -f deploy/prod/docker-compose.prod.yml exec -T web python manage.py collectstatic --noinput

certbot --nginx -d "${DOMAIN}" -d "www.${DOMAIN}" --non-interactive --agree-tos -m admin@${DOMAIN} --redirect || true

echo "[OK] Deployment done. Check: https://${DOMAIN}/"
