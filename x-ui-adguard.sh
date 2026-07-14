#!/usr/bin/env bash
# x-ui-adguard.sh — install AdGuard Home behind the existing 3x-ui panel domain
#
# What it does:
#   1. Reads the panel domain from x-ui.db / nginx (same logic as x-ui-patch.sh)
#   2. Downloads the latest AdGuard Home release to /opt/AdGuardHome
#   3. Pre-seeds AdGuardHome.yaml (skips the first-run wizard):
#        - web UI on 127.0.0.1:<random port>  (never exposed directly)
#        - plain DNS on 127.0.0.1:<random port> (localhost only, no port-53
#          conflict with systemd-resolved)
#        - DoH upstreams, allow_unencrypted_doh for the nginx bridge
#   4. Adds nginx locations on the PANEL domain (no separate domain needed):
#        - /dns-query            → DNS-over-HTTPS endpoint (standard path)
#        - /adg-<random>/        → AdGuard Home admin UI
#   5. Prints the admin URL, credentials and the DoH URL for clients
#
# Re-run safe: keeps the existing AdGuardHome.yaml (settings + password),
# reuses the admin path and ports already present in nginx/yaml.
#
# Usage:
#   bash x-ui-adguard.sh                # install / repair
#   bash x-ui-adguard.sh -uninstall y   # remove AdGuard Home + nginx config
#
# NOTE: x-ui-latest.sh and x-ui-patch.sh regenerate the panel vhost and drop
# the AdGuard include line — re-run this script after either of them
# (it restores the include; config and credentials are kept).
set -Eeuo pipefail

XUIDB="/etc/x-ui/x-ui.db"
AGH_DIR="/opt/AdGuardHome"
AGH_YAML="${AGH_DIR}/AdGuardHome.yaml"
AGH_SNIPPET="/etc/nginx/snippets/adguard.conf"
AGH_SERVICE="AdGuardHome"

# ── colour helpers ────────────────────────────────────────────────────────────
red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
blue()  { printf '\033[34m[*] %s\033[0m\n' "$*"; }
die()   { red "ERROR: $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run as root: sudo bash $0"

# ── parse args ────────────────────────────────────────────────────────────────
uninstall=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        -uninstall) uninstall="${2:-}"; shift 2;;
        *) die "Unknown argument: $1 (supported: -uninstall y)";;
    esac
done

# ── uninstall ─────────────────────────────────────────────────────────────────
if [[ "$uninstall" == "y" ]]; then
    blue "Uninstalling AdGuard Home..."
    systemctl stop "$AGH_SERVICE" 2>/dev/null || true
    [[ -x "${AGH_DIR}/AdGuardHome" ]] && "${AGH_DIR}/AdGuardHome" -s uninstall 2>/dev/null || true
    rm -rf "$AGH_DIR" "$AGH_SNIPPET"
    for f in /etc/nginx/sites-available/*; do
        [[ -f "$f" ]] || continue
        sed -i '\|snippets/adguard.conf|d' "$f"
    done
    if nginx -t &>/dev/null; then systemctl reload nginx; fi
    green "AdGuard Home removed."
    exit 0
fi

[[ -f "$XUIDB" ]] || die "x-ui.db not found at $XUIDB — install 3x-ui first"
command -v sqlite3 &>/dev/null || apt-get install -y -q sqlite3
db() { sqlite3 "$XUIDB" "$1"; }

# ── helpers ───────────────────────────────────────────────────────────────────
rand_str()  { tr -dc 'a-zA-Z0-9' </dev/urandom | head -c "$1" || true; }
free_port() {
    local p
    while true; do
        p=$(( ((RANDOM<<15)|RANDOM) % 49152 + 10000 ))
        ss -Hln "sport = :$p" 2>/dev/null | grep -q . || { echo "$p"; return; }
    done
}

# ── detect panel domain ───────────────────────────────────────────────────────
blue "Detecting panel domain..."
domain=""
web_cert=$(db "SELECT value FROM settings WHERE key='webCertFile';")
if [[ "$web_cert" =~ ^/root/cert/([^/]+)/ || "$web_cert" =~ /etc/letsencrypt/live/([^/]+)/ ]]; then
    domain="${BASH_REMATCH[1]}"
fi
if [[ -z "$domain" ]]; then
    for f in /etc/nginx/sites-available/*; do
        [[ -f "$f" ]] || continue
        case "$(basename "$f")" in 80.conf|00-maps.conf) continue;; esac
        if grep -q 'listen 7443' "$f" 2>/dev/null; then
            domain=$(awk '/server_name/{print $2; exit}' "$f" | tr -d ';')
            break
        fi
    done
fi
[[ -n "$domain" ]] || die "Could not determine panel domain (no webCertFile, no vhost with 'listen 7443')"

# Panel vhost file the include line goes into
vhost="/etc/nginx/sites-available/${domain}"
if [[ ! -f "$vhost" ]] || ! grep -q 'listen 7443' "$vhost"; then
    vhost=""
    for f in /etc/nginx/sites-available/*; do
        [[ -f "$f" ]] || continue
        grep -q 'listen 7443' "$f" 2>/dev/null && { vhost="$f"; break; }
    done
fi
[[ -n "$vhost" ]] || die "Panel vhost (listen 7443) not found in /etc/nginx/sites-available"
printf "    domain = %s\n    vhost  = %s\n" "$domain" "$vhost"

# ── detect or generate admin path ─────────────────────────────────────────────
agh_path=""
if [[ -f "$AGH_SNIPPET" ]]; then
    agh_path=$(grep -oP 'location /\Kadg-[a-zA-Z0-9]+' "$AGH_SNIPPET" | head -1 || true)
fi
if [[ -n "$agh_path" ]]; then
    blue "Admin path reused: /${agh_path}/"
else
    agh_path="adg-$(rand_str 12)"
    blue "Admin path generated: /${agh_path}/"
fi

# ── detect or generate ports ──────────────────────────────────────────────────
agh_web_port=""
if [[ -f "$AGH_YAML" ]]; then
    agh_web_port=$(grep -oP '^\s*address:\s*127\.0\.0\.1:\K\d+' "$AGH_YAML" | head -1 || true)
fi
if [[ -n "$agh_web_port" ]]; then
    blue "Web port reused: $agh_web_port"
else
    agh_web_port=$(free_port)
    blue "Web port generated: $agh_web_port"
fi
agh_dns_port=$(free_port)

# ── install packages ──────────────────────────────────────────────────────────
blue "Installing packages..."
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -q curl tar ca-certificates apache2-utils

# ── download AdGuard Home ─────────────────────────────────────────────────────
case "$(uname -m)" in
    x86_64)  agh_arch="amd64";;
    aarch64) agh_arch="arm64";;
    armv7l)  agh_arch="armv7";;
    *) die "Unsupported architecture: $(uname -m)";;
esac

if [[ -x "${AGH_DIR}/AdGuardHome" ]]; then
    blue "AdGuard Home binary already present — keeping it (it self-updates from the UI)."
else
    blue "Downloading AdGuard Home (linux_${agh_arch})..."
    curl -fsSL "https://github.com/AdguardTeam/AdGuardHome/releases/latest/download/AdGuardHome_linux_${agh_arch}.tar.gz" \
        | tar -xz -C /opt
    [[ -x "${AGH_DIR}/AdGuardHome" ]] || die "Download/extract failed — ${AGH_DIR}/AdGuardHome missing"
fi

# ── seed config (first install only — never clobber an existing config) ──────
new_credentials=0
agh_user="admin"
agh_pass=""
if [[ -f "$AGH_YAML" ]]; then
    blue "Existing AdGuardHome.yaml found — keeping settings and credentials."
else
    blue "Generating AdGuardHome.yaml..."
    new_credentials=1
    agh_pass=$(rand_str 20)
    agh_hash=$(htpasswd -nbB x "$agh_pass" | cut -d: -f2)
    [[ "$agh_hash" == \$2* ]] || die "bcrypt hash generation failed (htpasswd)"

    systemctl stop "$AGH_SERVICE" 2>/dev/null || true
    cat > "$AGH_YAML" <<EOF
http:
  address: 127.0.0.1:${agh_web_port}
users:
  - name: ${agh_user}
    password: ${agh_hash}
auth_attempts: 5
block_auth_min: 15
theme: auto
dns:
  bind_hosts:
    - 127.0.0.1
  port: ${agh_dns_port}
  upstream_dns:
    - https://dns.cloudflare.com/dns-query
    - https://dns.google/dns-query
    - https://dns.quad9.net/dns-query
  bootstrap_dns:
    - 1.1.1.1
    - 8.8.8.8
    - 9.9.9.9
  trusted_proxies:
    - 127.0.0.0/8
tls:
  enabled: false
  allow_unencrypted_doh: true
filters:
  - enabled: true
    url: https://adguardteam.github.io/HostlistsRegistry/assets/filter_1.txt
    name: AdGuard DNS filter
    id: 1
schema_version: 28
EOF
    chmod 600 "$AGH_YAML"
fi

# ── systemd service ───────────────────────────────────────────────────────────
if systemctl list-unit-files --type=service 2>/dev/null | grep -q "^${AGH_SERVICE}\.service"; then
    blue "Restarting AdGuard Home service..."
    systemctl restart "$AGH_SERVICE"
else
    blue "Installing AdGuard Home service..."
    "${AGH_DIR}/AdGuardHome" -s install
fi

blue "Waiting for AdGuard Home to come up..."
agh_up=0
for _ in $(seq 1 20); do
    if curl -fso /dev/null "http://127.0.0.1:${agh_web_port}/"; then agh_up=1; break; fi
    sleep 0.5
done
[[ $agh_up -eq 1 ]] || die "AdGuard Home did not start on 127.0.0.1:${agh_web_port} — check: journalctl -u ${AGH_SERVICE}"

# ── nginx snippet ─────────────────────────────────────────────────────────────
blue "Writing nginx snippet..."
mkdir -p /etc/nginx/snippets
cat > "$AGH_SNIPPET" <<EOF
    # AdGuard Home — DNS-over-HTTPS (standard path, no auth: DoH clients
    # can't log in; the endpoint only answers DNS wireformat)
    location /dns-query {
        proxy_pass http://127.0.0.1:${agh_web_port};
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_buffering off;
        # server-level proxy_intercept_errors would rewrite DoH error bodies
        proxy_intercept_errors off;
        access_log off;
    }

    # AdGuard Home admin UI (random path). The UI uses relative URLs, so a
    # trailing-slash proxy_pass works; Location headers and the session
    # cookie (Path=/) still need rewriting to the sub-path.
    location /${agh_path}/ {
        proxy_pass http://127.0.0.1:${agh_web_port}/;
        proxy_redirect / /${agh_path}/;
        proxy_cookie_path / /${agh_path}/;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_read_timeout 300s;
        proxy_intercept_errors off;
        add_header X-Robots-Tag "noindex, nofollow" always;
    }
    location = /${agh_path} { return 302 /${agh_path}/; }
EOF

# Include the snippet in the panel vhost (before the shared includes so it
# sits inside the server block; prefix locations are order-independent)
if ! grep -q 'snippets/adguard.conf' "$vhost"; then
    if grep -q 'include /etc/nginx/snippets/includes.conf;' "$vhost"; then
        sed -i 's|^\(\s*\)include /etc/nginx/snippets/includes.conf;|\1include /etc/nginx/snippets/adguard.conf;\n\1include /etc/nginx/snippets/includes.conf;|' "$vhost"
    else
        # No shared snippet (unexpected layout) — insert before the closing brace
        sed -i '$ s|^}$|    include /etc/nginx/snippets/adguard.conf;\n}|' "$vhost"
    fi
    blue "Include added to ${vhost}"
else
    blue "Include already present in ${vhost}"
fi

blue "Testing nginx config..."
if nginx -t 2>&1 | grep -q successful; then
    systemctl reload nginx
    green "nginx reloaded OK."
else
    red "nginx config test failed — check errors above."
    nginx -t
    exit 1
fi

# ── verify DoH end-to-end through nginx-side port ─────────────────────────────
# RFC 8484 example query (www.example.com A) against the local AGH listener
doh_status=$(curl -so /dev/null -w '%{http_code}' \
    -H 'Accept: application/dns-message' \
    "http://127.0.0.1:${agh_web_port}/dns-query?dns=AAABAAABAAAAAAAAA3d3dwdleGFtcGxlA2NvbQAAAQAB" || true)
if [[ "$doh_status" == "200" ]]; then
    green "DoH endpoint answers (HTTP 200)."
else
    red "DoH self-test returned HTTP ${doh_status} (expected 200) — check AdGuard Home logs."
fi

# ─────────────────────────────────────────────────────────────────────────────
# RESULTS
# ─────────────────────────────────────────────────────────────────────────────
echo
green "══════════════════════════════════════════════"
green " AdGuard Home installed"
green "══════════════════════════════════════════════"
printf "\n  Admin UI:  https://%s/%s/\n" "$domain" "$agh_path"
if [[ $new_credentials -eq 1 ]]; then
    printf "  Login:     %s\n" "$agh_user"
    printf "  Password:  %s\n" "$agh_pass"
    echo
    red   "  Save the password now — it is stored only as a bcrypt hash in ${AGH_YAML}."
else
    printf "  Login:     unchanged (existing credentials kept)\n"
fi
printf "\n  DNS-over-HTTPS for clients:\n"
printf "    https://%s/dns-query\n" "$domain"
echo
blue "Re-run this script after x-ui-latest.sh / x-ui-patch.sh — they regenerate"
blue "the panel vhost and drop the AdGuard include (settings are kept)."
echo
