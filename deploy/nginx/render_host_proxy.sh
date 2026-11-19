#!/usr/bin/env bash
set -euo pipefail

mode="${1:?Usage: render_host_proxy.sh production|staging|bootstrap DOMAIN UPSTREAM_PORT}"
domain="${2:?Set the canonical hostname}"
port="${3:?Set the loopback upstream port}"
tls_dir="${POSTZYO_TLS_DIR:-/etc/letsencrypt/live/${domain}}"
acme_root="${POSTZYO_ACME_ROOT:-/var/lib/postzyo-acme}"
staging_password_file="${POSTZYO_STAGING_HTPASSWD:-}"

if [[ ! "${domain}" =~ ^[a-z0-9]+([.-][a-z0-9]+)+$ || ! "${port}" =~ ^[1-9][0-9]{0,4}$ ]] || (( port > 65535 )); then
  echo "Invalid hostname or upstream port" >&2
  exit 1
fi

names="${domain} www.${domain}"
case "${mode}" in
  production|bootstrap) ;;
  staging)
    if [[ ! "${staging_password_file}" =~ ^/[-a-zA-Z0-9_./]+$ || ! -s "${staging_password_file}" ]]; then
      echo "Staging requires a populated POSTZYO_STAGING_HTPASSWD file" >&2
      exit 1
    fi
    names="${domain}"
    ;;
  *) echo "Unknown proxy mode: ${mode}" >&2; exit 1 ;;
esac

if [[ ! "${tls_dir}" =~ ^[-a-zA-Z0-9_./:]+$ || ! "${acme_root}" =~ ^[-a-zA-Z0-9_./:]+$ ]]; then
  echo "Invalid TLS or ACME path" >&2
  exit 1
fi

http_response="return 308 https://${domain}\$request_uri;"
if [[ "${mode}" == bootstrap ]]; then
  http_response="return 503;"
fi

cat <<NGINX
server {
    listen 80;
    server_name ${names};

    location ^~ /.well-known/acme-challenge/ {
        root ${acme_root};
        try_files \$uri =404;
    }

    location / {
        ${http_response}
    }
}
NGINX

if [[ "${mode}" == bootstrap ]]; then
  exit 0
fi

if [[ "${mode}" == production ]]; then
  cat <<NGINX

server {
    listen 443 ssl;
    server_name www.${domain};
    ssl_certificate ${tls_dir}/fullchain.pem;
    ssl_certificate_key ${tls_dir}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;

    return 308 https://${domain}\$request_uri;
}
NGINX
fi

cat <<NGINX

server {
    listen 443 ssl;
    server_name ${domain};
    ssl_certificate ${tls_dir}/fullchain.pem;
    ssl_certificate_key ${tls_dir}/privkey.pem;
    ssl_protocols TLSv1.2 TLSv1.3;
    client_max_body_size 100m;
NGINX

if [[ "${mode}" == staging ]]; then
  cat <<NGINX
    auth_basic "Postzyo staging";
    auth_basic_user_file ${staging_password_file};
    add_header X-Robots-Tag "noindex, nofollow" always;
NGINX
fi

cat <<NGINX

    location / {
        proxy_pass http://127.0.0.1:${port};
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
NGINX

if [[ "${mode}" == staging ]]; then
  echo '        proxy_set_header Authorization "";'
fi

cat <<'NGINX'
    }
}
NGINX
