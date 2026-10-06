#!/bin/bash
# 3x-ui Auto Nginx - based on x-ui-pro-refactor by mozaroc (github.com/mozaroc/3x-ui-pro)
[[ $EUID -ne 0 ]] && { echo "Run as root: sudo bash $0"; exit 1; }

# ─── Output helpers ──────────────────────────────────────────────────────────
msg_ok()  { printf '\e[1;32m%s\e[0m\n' "$1"; }
msg_err() { echo -e "\e[1;41m $1 \e[0m"; }
msg_inf() { echo -e "\e[1;34m$1\e[0m"; }
msg_warn() { printf '\e[1;33m%s\e[0m\n' "$1"; }

echo
msg_inf '============================================================'
msg_inf '  3x-ui Auto Nginx'
msg_inf '  Automated 3x-ui / Xray deployment'
msg_inf '============================================================'
echo

# ─── Pre-flight checks ───────────────────────────────────────────────────────
check_os() {
    local os_id os_version
    os_id=$(grep -oP '(?<=^ID=).+' /etc/os-release 2>/dev/null | tr -d '"')
    os_version=$(grep -oP '(?<=^VERSION_ID=").+(?=")' /etc/os-release 2>/dev/null)

    case "${os_id}" in
        ubuntu)
            [[ "$os_version" == "24.04" || "$os_version" == "26.04" ]] && return 0
            ;;
        debian)
            [[ "$os_version" == "13" ]] && return 0
            ;;
    esac

    msg_err "Unsupported OS: ${os_id} ${os_version}"
    echo -e "\nThis script supports:\n  Ubuntu 24.04 / 26.04\n  Debian 13"
    echo -e "\nPlease reinstall your server with one of the supported OS versions and try again."
    exit 1
}

check_cpu() {
    local cpuinfo="${1:-/proc/cpuinfo}" arch feature_field cpu_model cpu_flags
    CPU_SUPPORT_LEVEL="info"
    CPU_SUPPORT_TEXT="Compatible (acceleration not assessed)"
    arch=$(uname -m 2>/dev/null) || return 0
    case "$arch" in
        x86_64|i386|i486|i586|i686) feature_field="flags" ;;
        aarch64|arm64) feature_field="Features" ;;
        *) return 0 ;;
    esac
    cpu_model=$(grep -m1 -E '^[[:space:]]*model name[[:space:]]*:' "$cpuinfo" 2>/dev/null) || cpu_model=""
    cpu_flags=$(grep -m1 -E "^[[:space:]]*${feature_field}[[:space:]]*:" "$cpuinfo" 2>/dev/null) || return 0
    cpu_flags="${cpu_flags#*:}"
    [[ -n "${cpu_flags//[[:space:]]/}" ]] || return 0

    if [[ " $cpu_flags " =~ [[:space:]]aes[[:space:]] ]]; then
        CPU_SUPPORT_LEVEL="ok"
        CPU_SUPPORT_TEXT="Compatible (hardware AES available)"
    else
        CPU_SUPPORT_LEVEL="warn"
        CPU_SUPPORT_TEXT="Compatible — hardware AES unavailable"
        msg_warn "Hardware AES unavailable. Xray can run, but cryptographic performance may be lower."
        if [[ "${cpu_model,,}" == *qemu* ]]; then
            msg_warn "Generic QEMU/KVM CPU detected: host-passthrough / AES exposure is recommended when available."
        fi
        msg_warn "Continuing installation."
    fi
}

check_os
check_cpu

# ─── Constants ───────────────────────────────────────────────────────────────
XUIDB="/etc/x-ui/x-ui.db"
PROJECT_REF="${XUI_AUTO_REF:-main}"
configure_project_source() {
    if [[ "$PROJECT_REF" != main && ! "$PROJECT_REF" =~ ^[a-fA-F0-9]{40}$ &&
          ! "$PROJECT_REF" =~ ^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]]; then
        msg_err 'Invalid XUI_AUTO_REF: use main, an exact 40-hex commit SHA, or a stable vMAJOR.MINOR.PATCH tag.'
        return 1
    fi
    GITHUB_RAW="https://raw.githubusercontent.com/xPROMSx/3x-ui-auto-nginx/${PROJECT_REF}"
}
configure_project_source || exit 1
FAKE_SITE_COUNT=50
LEGACY_CERTBOT_CRON='@monthly certbot renew --non-interactive --pre-hook "systemctl stop nginx" --post-hook "systemctl start nginx" > /dev/null 2>&1'

# ─── Default argument values ─────────────────────────────────────────────────
domain=""
reality_domain=""
UNINSTALL="x"
INSTALL="y"
AUTODOMAIN="n"
CFALLOW="n"
PANEL_VERSION=""
INSTALL_AGH=n

# ─── Stop & clean previous install (called from main, after domain validation) ─
clean_previous_install() {
    systemctl stop x-ui 2>/dev/null || true
    rm -rf /etc/systemd/system/x-ui.service
    rm -rf /usr/local/x-ui
    rm -rf /etc/x-ui
    rm -rf /etc/nginx/sites-enabled/*
    rm -rf /etc/nginx/sites-available/*
    rm -rf /etc/nginx/stream-enabled/*
}

# ─── Port / path generators ──────────────────────────────────────────────────
get_port() {
    echo $(( ((RANDOM<<15)|RANDOM) % 49152 + 10000 ))
}

gen_random_string() {
    local length="$1"
    head -c 4096 /dev/urandom | tr -dc 'a-zA-Z0-9' | head -c "$length"
    echo
}

# Matches the panel's host group_id format (16 lowercase alphanumerics)
gen_group_id() {
    head -c 4096 /dev/urandom | tr -dc 'a-z0-9' | head -c 16
    echo
}

check_free() {
    nc -z 127.0.0.1 "$1" &>/dev/null
    return $?
}

make_port() {
    while true; do
        local PORT
        PORT=$(get_port)
        if ! check_free "$PORT"; then
            echo "$PORT"
            break
        fi
    done
}

# ─── Generate ports & paths (done once at startup) ───────────────────────────
sub_port=$(make_port)
panel_port=$(make_port)
ws_port=$(make_port)
trojan_port=$(make_port)

sub_path=$(gen_random_string 10)
json_path=$(gen_random_string 10)
panel_path=$(gen_random_string 10)
ws_path=$(gen_random_string 10)
trojan_path=$(gen_random_string 10)
xhttp_path=$(gen_random_string 10)
config_username=$(gen_random_string 10)
config_password=$(gen_random_string 10)
diag_path="/net-$(gen_random_string 12)/"
diag_token=$(gen_random_string 16)
mtr_backend_port=$(make_port)

# ─── Argument parsing ────────────────────────────────────────────────────────
while [ "$#" -gt 0 ]; do
    case "$1" in
        -install)          INSTALL="$2";           shift 2 ;;
        -subdomain)        domain="$2";            shift 2 ;;
        -reality_domain)   reality_domain="$2";    shift 2 ;;
        -ONLY_CF_IP_ALLOW) CFALLOW="$2";           shift 2 ;;
        -version)
            PANEL_VERSION="${2:-}"
            [[ -n "$PANEL_VERSION" ]] || { msg_err "-version requires a stable release tag."; exit 1; }
            shift 2 ;;
        -uninstall)        UNINSTALL="$2";         shift 2 ;;
        *)                 shift 1 ;;
    esac
done

# Only stable release versions at or above the supported security baseline.
_validate_panel_version() {
    local version="${1#v}"
    if [[ ! "$version" =~ ^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$ ]] ||
       [[ "$(printf '%s\n' 3.8.0 "$version" | sort -V | head -n1)" != "3.8.0" ]]; then
        msg_err "Unsupported 3x-ui version: $1. Use a stable release >= v3.8.0."
        return 1
    fi
}

# Reject unsafe/invalid -version values before uninstall or rebuild actions.
if [[ -n "$PANEL_VERSION" ]]; then
    _validate_panel_version "$PANEL_VERSION" || exit 1
fi

# ─── Detect package manager ───────────────────────────────────────────────────
Pak=$(type apt &>/dev/null && echo "apt" || echo "yum")

# ─────────────────────────────────────────────────────────────────────────────
# UNINSTALL
# ─────────────────────────────────────────────────────────────────────────────
detect_existing_installation() {
    EXISTING_INSTALL_CATEGORIES=()
    if [[ -e /etc/x-ui || -e /usr/local/x-ui || -e /usr/bin/x-ui ||
          -e /etc/systemd/system/x-ui.service || -e /lib/systemd/system/x-ui.service ]] ||
       systemctl is-active --quiet x-ui 2>/dev/null || systemctl is-enabled --quiet x-ui 2>/dev/null; then
        EXISTING_INSTALL_CATEGORIES+=("3x-ui installation")
    fi
    if [[ -e /usr/local/lib/3x-ui-pro || -e /usr/local/bin/x-ui-backup ||
          -e /etc/systemd/system/mtr-backend.service ]] ||
       systemctl is-active --quiet mtr-backend 2>/dev/null || systemctl is-enabled --quiet mtr-backend 2>/dev/null; then
        EXISTING_INSTALL_CATEGORIES+=("Project runtime / diagnostics")
    fi
    if [[ -d /etc/nginx ]] || compgen -G '/etc/nginx/sites-enabled/*' >/dev/null ||
       compgen -G '/etc/nginx/sites-available/*' >/dev/null || compgen -G '/etc/nginx/stream-enabled/*' >/dev/null ||
       compgen -G '/etc/nginx/snippets/*' >/dev/null ||
       [[ "$(dpkg-query -W -f='${Status}' nginx-common 2>/dev/null)" == 'install ok installed' ]]; then
        EXISTING_INSTALL_CATEGORIES+=("nginx configuration / package")
    fi
    if [[ -d /root/cert ]] || compgen -G '/etc/letsencrypt/live/*' >/dev/null; then
        EXISTING_INSTALL_CATEGORIES+=("TLS certificates")
    fi
    if command -v crontab >/dev/null && LC_ALL=C crontab -l 2>/dev/null | grep -Fxq -- "$LEGACY_CERTBOT_CRON"; then
        EXISTING_INSTALL_CATEGORIES+=("Legacy project certificate renewal")
    fi
    if [[ -e /opt/AdGuardHome || -e /etc/systemd/system/AdGuardHome.service ||
          -e /etc/nginx/snippets/adguard.conf || -e /etc/nginx/snippets/x-ui-auto-optional/adguard.conf ]] ||
       systemctl is-active --quiet AdGuardHome 2>/dev/null || systemctl is-enabled --quiet AdGuardHome 2>/dev/null ||
       grep -RqE 'location.*(/adg-|/dns-query)' /etc/nginx/sites-available /etc/nginx/snippets 2>/dev/null; then
        EXISTING_INSTALL_CATEGORIES+=("AdGuard Home")
    fi
    return 0
}

confirm_destructive_reinstall() {
    local answer category
    detect_existing_installation
    (( ${#EXISTING_INSTALL_CATEGORIES[@]} )) || return 0
    msg_warn '============================================================'
    msg_warn '  Existing installation detected'
    msg_warn '============================================================'
    for category in "${EXISTING_INSTALL_CATEGORIES[@]}"; do msg_warn " [!] $category"; done
    printf '\nContinuing will remove the current 3x-ui deployment and replace\nnginx configuration. Copy a backup off-host before continuing.\n\n'
    printf 'Type YES to remove the existing deployment and continue: '
    if ! IFS= read -r answer || [[ "$answer" != YES ]]; then
        msg_err 'Cancelled. Existing deployment was not changed.'
        return 1
    fi
}

remove_legacy_certbot_cron() {
    command -v crontab >/dev/null || return 0
    local directory
    directory=$(mktemp -d) || return 1
    if ! LC_ALL=C crontab -l > "$directory/current" 2> "$directory/error"; then
        if grep -qi 'no crontab for' "$directory/error"; then rm -rf "$directory"; return 0; fi
        rm -rf "$directory"; return 1
    fi
    if grep -Fxq -- "$LEGACY_CERTBOT_CRON" "$directory/current"; then
        awk -v legacy="$LEGACY_CERTBOT_CRON" '$0 != legacy' "$directory/current" > "$directory/new" || { rm -rf "$directory"; return 1; }
        crontab - < "$directory/new" && crontab -l > "$directory/verified" &&
            cmp -s "$directory/new" "$directory/verified" || { rm -rf "$directory"; return 1; }
    fi
    rm -rf "$directory"
}

cleanup_adguard() {
    # Only the upstream-generated project unit is owned here; never run an old binary.
    if systemctl cat AdGuardHome >/dev/null 2>&1 || systemctl is-active --quiet AdGuardHome 2>/dev/null; then
        local fragment
        fragment=$(systemctl show -p FragmentPath --value AdGuardHome) || return 1
        [[ "$fragment" == /etc/systemd/system/AdGuardHome.service ]] || return 1
        systemctl stop AdGuardHome && systemctl disable AdGuardHome || return 1
        systemctl is-active --quiet AdGuardHome && return 1
        rm -f /etc/systemd/system/AdGuardHome.service || return 1
        systemctl daemon-reload || return 1
    fi
    rm -rf /opt/AdGuardHome || return 1
    rm -f /etc/nginx/snippets/adguard.conf /etc/nginx/snippets/x-ui-auto-optional/adguard.conf || return 1
    rm -f /usr/local/lib/3x-ui-pro/managed-adguard.sh
}

uninstall_xui() {
    cleanup_adguard || { msg_err "Cannot clean AdGuard Home; core deployment was not removed."; return 1; }
    remove_legacy_certbot_cron || { msg_err "Cannot remove the exact legacy certificate renewal job."; return 1; }
    rm -f /etc/letsencrypt/renewal-hooks/deploy/3x-ui-auto-nginx || return 1
    printf 'y\n' | x-ui uninstall 2>/dev/null || true
    rm -rf /etc/x-ui/ /usr/local/x-ui/
    rm -f  /usr/bin/x-ui
    $Pak -y remove nginx nginx-common nginx-core nginx-full
    $Pak -y purge  nginx nginx-common nginx-core nginx-full
    $Pak -y autoclean
    rm -rf /var/www/html/ /var/www/diagnostics/ /var/www/subpage/ /etc/nginx/ /usr/share/nginx/
    systemctl stop mtr-backend 2>/dev/null || true
    systemctl disable mtr-backend 2>/dev/null || true
    rm -f /etc/systemd/system/mtr-backend.service
    rm -rf /usr/local/lib/3x-ui-pro/
    systemctl daemon-reload 2>/dev/null || true
}

if [[ ${UNINSTALL} == *"y"* ]]; then
    confirm_destructive_reinstall || exit 1
    uninstall_xui || exit 1
    msg_ok "3x-ui Auto Nginx completely uninstalled."
    exit 0
fi

# ─────────────────────────────────────────────────────────────────────────────
# GET SERVER IP
# ─────────────────────────────────────────────────────────────────────────────
IP4_REGEX="^[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}\.[0-9]{1,3}$"
IP6_REGEX="([a-f0-9:]+:+)+[a-f0-9]+"

get_server_ip() {
    IP4=$(ip route get 8.8.8.8 2>&1 | grep -Po -- 'src \K\S*')
    IP6=$(ip route get 2620:fe::fe 2>&1 | grep -Po -- 'src \K\S*')
    [[ $IP4 =~ $IP4_REGEX ]] || IP4=$(curl -4 -fsS --connect-timeout 5 --max-time 10 https://ipv4.icanhazip.com | tr -d '[:space:]')
    [[ $IP6 =~ $IP6_REGEX ]] || IP6=$(curl -6 -fsS --connect-timeout 5 --max-time 10 https://ipv6.icanhazip.com 2>/dev/null | tr -d '[:space:]')
    [[ $IP4 =~ $IP4_REGEX ]] || IP4=""
    [[ $IP6 =~ $IP6_REGEX ]] || IP6=""
}

# Early IP fetch for auto-domain
IP4=$(ip route get 8.8.8.8 2>&1 | grep -Po -- 'src \K\S*')
[[ $IP4 =~ $IP4_REGEX ]] || IP4=$(curl -4 -fsS --connect-timeout 5 --max-time 10 https://ipv4.icanhazip.com | tr -d '[:space:]')
[[ $IP4 =~ $IP4_REGEX ]] || IP4=""


# ─────────────────────────────────────────────────────────────────────────────
# DOMAIN VALIDATION
# ─────────────────────────────────────────────────────────────────────────────
validate_domains() {
    while true; do
        [[ -n "$domain" ]] && break
        echo -en "3x-ui panel domain (panel.example.com): " && read -r domain
    done
    domain=$(echo "$domain" | tr -d '[:space:]')
    SubDomain=$(echo "$domain"   | sed 's/^[^ ]* \|\..*//g')
    MainDomain=$(echo "$domain"  | sed 's/.*\.\([^.]*\..*\)$/\1/')
    [[ "${SubDomain}.${MainDomain}" != "${domain}" ]] && MainDomain=${domain}

    while true; do
        [[ -n "$reality_domain" ]] && break
        echo -en "REALITY domain (reality.example.com): " && read -r reality_domain
    done
    reality_domain=$(echo "$reality_domain" | tr -d '[:space:]')
    RealitySubDomain=$(echo "$reality_domain" | sed 's/^[^ ]* \|\..*//g')
    RealityMainDomain=$(echo "$reality_domain" | sed 's/.*\.\([^.]*\..*\)$/\1/')
    [[ "${RealitySubDomain}.${RealityMainDomain}" != "${reality_domain}" ]] && RealityMainDomain=${reality_domain}

    if [[ "$domain" == "$reality_domain" ]]; then
        msg_err "Panel domain and REALITY domain must be different! Got: ${domain}"
        exit 1
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# INSTALL PACKAGES
# ─────────────────────────────────────────────────────────────────────────────
install_packages() {
    if [[ ${INSTALL} == *"y"* ]]; then
        local version
        version=$(grep -oP '(?<=VERSION_ID=")[0-9]+' /etc/os-release)
        [[ "$version" == "20" || "$version" == "22" ]] && echo "System: Ubuntu $version"

        $Pak -y update || return 1
        $Pak -y install curl wget jq bash sudo nginx-full certbot sqlite3 ufw netcat-openbsd mtr python3 python3-configobj python3-cryptography libcap2-bin openssl procps iproute2 tar gzip tzdata ca-certificates || return 1
        systemctl daemon-reload && systemctl enable --now nginx || return 1
    fi

    apt-get install -yqq --no-install-recommends ca-certificates || return 1
    local binary
    for binary in openssl sysctl ip ss tar gzip curl wget jq bash sudo nginx certbot sqlite3 ufw nc mtr python3 setcap; do
        command -v "$binary" >/dev/null || { msg_err "Required binary is missing: $binary"; return 1; }
    done
    python3 -c 'import configobj, cryptography' || { msg_err 'Required Python modules are unavailable: configobj, cryptography.'; return 1; }
}

# ─────────────────────────────────────────────────────────────────────────────
# SSL CERTIFICATES
# ─────────────────────────────────────────────────────────────────────────────
prepare_acme_webroot() {
    install -d -o root -g root -m 0755 /var/www/acme /var/www/acme/.well-known /var/www/acme/.well-known/acme-challenge
}

setup_acme_http() {
    prepare_acme_webroot || return 1
    mkdir -p /etc/nginx/sites-available /etc/nginx/sites-enabled || return 1
    cat > /etc/nginx/sites-available/80.conf <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${domain} ${reality_domain};
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/acme;
        default_type text/plain;
        try_files \$uri =404;
    }
    location / { return 301 https://\$host\$request_uri; }
}
EOF
    [[ $? == 0 ]] || return 1
    rm -f /etc/nginx/sites-enabled/default || return 1
    ln -sf /etc/nginx/sites-available/80.conf /etc/nginx/sites-enabled/80.conf || return 1
    nginx -t && systemctl enable --now nginx && systemctl reload nginx && systemctl is-active --quiet nginx
}

get_ssl_certs() {
    if [[ ${AUTODOMAIN} == *"y"* ]]; then
        local resolve_ok=true
        for d in "$domain" "$reality_domain"; do
            local a
            a=$(getent ahostsv4 "$d" 2>/dev/null | awk 'NR==1{print $1}')
            if [[ "$a" != "$IP4" ]]; then
                msg_err "Auto-domain $d does not resolve to $IP4. Fix DNS and retry."
                resolve_ok=false
            fi
        done
        [[ $resolve_ok == false ]] && return 1
    fi
    # Rebuild only our hook after runtime configuration; preserve all lineages.
    rm -f /etc/letsencrypt/renewal-hooks/deploy/3x-ui-auto-nginx || return 1
    local d
    for d in "$domain" "$reality_domain"; do
        if [[ -e "/etc/letsencrypt/live/$d" || -e "/etc/letsencrypt/renewal/$d.conf" ]]; then
            [[ -d "/etc/letsencrypt/live/$d" && -f "/etc/letsencrypt/renewal/$d.conf" ]] || {
                msg_err "Incomplete certificate lineage: $d. Resolve it before rebuilding."; return 1;
            }
            check_certificate_identity "$d" || return 1
            if ! check_webroot_lineage "$d" 2>/dev/null; then
                certbot reconfigure --cert-name "$d" --webroot --webroot-path /var/www/acme \
                    --pre-hook '' --post-hook '' --non-interactive || return 1
            fi
        elif compgen -G "/etc/letsencrypt/live/$d-*" >/dev/null || compgen -G "/etc/letsencrypt/renewal/$d-*.conf" >/dev/null; then
            msg_err "Ambiguous certificate lineage for $d. Resolve it before rebuilding."; return 1
        else
            certbot certonly --webroot --webroot-path /var/www/acme --cert-name "$d" -d "$d" \
                --non-interactive --agree-tos --register-unsafely-without-email || return 1
        fi
        check_webroot_lineage "$d" || return 1
    done
    mkdir -p "/root/cert/${domain}" || return 1
    chmod 755 /root/cert "/root/cert/${domain}" || return 1
    ln -sf "/etc/letsencrypt/live/${domain}/fullchain.pem" "/root/cert/${domain}/fullchain.pem" || return 1
    ln -sf "/etc/letsencrypt/live/${domain}/privkey.pem" "/root/cert/${domain}/privkey.pem"
}
# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURE NGINX
# ─────────────────────────────────────────────────────────────────────────────
configure_nginx() {
    install -d -o root -g root -m 0755 /etc/nginx/snippets/x-ui-auto-optional || return 1
    mkdir -p /etc/nginx/stream-enabled /etc/nginx/snippets

    # nginx >= 1.25.1 deprecates "listen ... http2" in favor of "http2 on;";
    # older versions (Ubuntu 24.04) don't know the new directive
    local ngx_ver http2_listen="" http2_on=""
    ngx_ver=$(nginx -v 2>&1 | grep -oP '[0-9]+\.[0-9]+\.[0-9]+' || echo 0)
    if [[ "$(printf '%s\n' 1.25.1 "$ngx_ver" | sort -V | head -1)" == "1.25.1" ]]; then
        http2_on="http2 on;"
    else
        http2_listen=" http2"
    fi

    # SNI-based stream: reality → 8443, domain → 7443
    cat > /etc/nginx/stream-enabled/stream.conf <<EOF
map \$ssl_preread_server_name \$sni_name {
    hostnames;
    ${reality_domain}    xray;
    ${domain}            www;
    default              xray;
}

upstream xray { server 127.0.0.1:8443; }
upstream www  { server 127.0.0.1:7443; }

server {
    proxy_protocol on;
    set_real_ip_from unix:;
    listen     443;
    listen     [::]:443;
    proxy_pass \$sni_name;
    ssl_preread on;
}
EOF

    grep -xqFR "stream { include /etc/nginx/stream-enabled/*.conf; }" /etc/nginx/* \
        || echo "stream { include /etc/nginx/stream-enabled/*.conf; }" >> /etc/nginx/nginx.conf
    grep -xqFR "load_module modules/ngx_stream_module.so;" /etc/nginx/* \
        || sed -i '1s/^/load_module \/usr\/lib\/nginx\/modules\/ngx_stream_module.so; /' /etc/nginx/nginx.conf
    grep -xqFR "worker_rlimit_nofile 16384;" /etc/nginx/* \
        || echo "worker_rlimit_nofile 16384;" >> /etc/nginx/nginx.conf
    sed -i "/worker_connections/c\worker_connections 4096;" /etc/nginx/nginx.conf

    # Shared proxy locations for xray inbounds (included by both vhosts)
    cat > /etc/nginx/snippets/includes.conf <<EOF
    #Subscription — prefix location covers all sub-paths (assets, JS, etc.)
    location /${sub_path}/ {
        if (\$hack = 1) { return 404; }
        proxy_redirect off;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_pass https://127.0.0.1:${sub_port};
    }
    location = /${sub_path} {
        if (\$hack = 1) { return 404; }
        proxy_redirect off;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_pass https://127.0.0.1:${sub_port};
    }
    # Regex takes priority over prefix: catches subscription IDs (one-level deep)
    # and routes Clash/Mihomo clients to dynamic clash.yaml generator
    location ~ ^/${sub_path}/(?<clash_sub_id>[^/]+)$ {
        if (\$hack = 1) { return 404; }
        if (\$serve_clash_yaml = 1) { rewrite ^ /__clash_api?sub_id=\$clash_sub_id last; }
        proxy_redirect off;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_pass https://127.0.0.1:${sub_port};
    }
    location /assets  { proxy_pass https://127.0.0.1:${sub_port}; }
    location /assets/ { proxy_pass https://127.0.0.1:${sub_port}; }

    #Subscription (json)
    location /${json_path} {
        if (\$hack = 1) { return 404; }
        proxy_redirect off;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_pass https://127.0.0.1:${sub_port};
    }
    location /${json_path}/ {
        if (\$hack = 1) { return 404; }
        proxy_redirect off;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_pass https://127.0.0.1:${sub_port};
    }

    #XHTTP
    location ^~ /${xhttp_path}/ {
        client_max_body_size  0;
        client_body_timeout   1h;
        grpc_read_timeout     1h;
        grpc_send_timeout     1h;
        grpc_set_header Connection        "";
        grpc_set_header Host              \$host;
        grpc_set_header X-Real-IP         \$remote_addr;
        grpc_set_header X-Forwarded-For   \$remote_addr;
        grpc_pass unix:/dev/shm/uds2023.sock;
    }

    # Installer-managed WS: exact path, fixed backend port
    location = /${ws_port}/${ws_path} {
        if (\$hack = 1) { return 404; }
        client_max_body_size 0;
        client_body_timeout 1d;
        proxy_read_timeout 1d;
        proxy_http_version 1.1;
        proxy_buffering off;
        proxy_request_buffering off;
        proxy_socket_keepalive on;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_pass http://127.0.0.1:${ws_port};
    }

    # Xray custom gRPC serviceName is the full method path (no /Tun suffix)
    location = /${trojan_port}/${trojan_path} {
        if (\$hack = 1) { return 404; }
        client_max_body_size 0;
        client_body_timeout 1d;
        grpc_read_timeout 1d;
        grpc_socket_keepalive on;
        grpc_set_header Host \$host;
        grpc_pass grpc://127.0.0.1:${trojan_port};
    }

    location / { try_files \$uri \$uri/ =404; }
EOF

    # HTTP-level maps. The clash maps are consumed by the shared includes.conf
    # snippet, which is included by BOTH vhosts, so they live in their own
    # always-loaded file — never inside a single vhost, or the other vhost's
    # include would reference an undefined var ("unknown ... variable").
    cat > /etc/nginx/sites-available/00-maps.conf <<EOF
# Detect Clash/Mihomo clients by User-Agent
map \$http_user_agent \$is_clash_ua {
    ~*(clash|clashx|clashn|mihomo|stash|surfboard)  1;
    default                                          0;
}
# Serve clash.yaml only when: Clash UA AND no ?provider=1 query param
# (proxy-provider refresh requests add ?provider=1 and must get the real sub)
map "\$is_clash_ua:\$arg_provider" \$serve_clash_yaml {
    "1:"    1;
    default 0;
}
EOF

    # Main domain vhost (TLS termination at 7443, proxy_protocol)
    cat > "/etc/nginx/sites-available/${domain}" <<EOF
# Rate limiting zones (http context)
limit_req_zone  \$binary_remote_addr zone=diag_api:10m  rate=6r/m;
limit_req_zone  \$binary_remote_addr zone=diag_page:10m rate=30r/m;
limit_conn_zone \$binary_remote_addr zone=per_ip:10m;

# Diagnostics access: cookie issued by the SSO bridge after panel login
map \$cookie_diag_key \$diag_auth {
    "${diag_token}" 1;
    default          0;
}

server {
    server_tokens off;
    server_name ${domain};
    listen 127.0.0.1:7443 ssl${http2_listen} proxy_protocol;
    ${http2_on}
    index index.html index.htm index.php;
    root /var/www/html/;
    real_ip_header proxy_protocol;
    set_real_ip_from 127.0.0.1;
    # This vhost listens on 7443 behind the SNI stream (public port 443). Without
    # this, nginx bakes :7443 into redirect Location headers (return/error_page),
    # so browsers get sent to an unreachable port. Keep redirects relative.
    absolute_redirect off;
    # Larger h2 preread window improves single-stream upload throughput
    http2_max_concurrent_streams 256;
    http2_body_preread_size 128k;
    client_body_buffer_size 512k;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!eNULL:!MD5:!DES:!RC4:!ADH:!SSLv3:!EXP:!PSK:!DSS;
    ssl_certificate     /etc/letsencrypt/live/${domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${domain}/privkey.pem;
    if (\$host !~* ^(.+\.)?${domain}\$)            { return 444; }
    if (\$scheme ~* https)                          { set \$safe 1; }
    if (\$ssl_server_name !~* ^(.+\.)?${domain}\$) { set \$safe "\${safe}0"; }
    if (\$safe = 10)                                { return 444; }
    if (\$request_uri ~ "(\"|'|\`|~|,|:|;|%|\\$|&&|\?\?|0x00|0X00|\||\\|\{|\}|\[|\]|<|>|\.\.\.|\.\.\/|\/\/\/)") { set \$hack 1; }
    error_page 400 401 402 403 500 501 502 503 504 =404 /404;
    proxy_intercept_errors on;

    location /${panel_path}/ {
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_pass https://127.0.0.1:${panel_port};
    }
    location /${panel_path} {
        proxy_http_version 1.1;
        proxy_set_header Upgrade \$http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto https;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
        proxy_pass https://127.0.0.1:${panel_port};
    }

    # ── Diagnostics SSO bridge ───────────────────────────────────────────────
    # Lives under the panel path so the browser attaches the 3x-ui session
    # cookie (its Path is scoped to the panel base path). Valid panel session
    # → issue the diag cookie and redirect; otherwise → panel login page.
    # NOTE: auth_request runs in the access phase; a plain "return" here would
    # skip it (rewrite phase), hence the try_files → named-location hop.
    location = /${panel_path}/diag {
        auth_request /__diag_auth;
        # Named location (not "=302 /uri") so the deny path emits a real Location
        # header; an internal-redirect error_page returns a 302 with no Location.
        error_page 401 403 = @diag_login;
        try_files /__nonexistent @diag_sso_ok;
    }
    location @diag_login {
        return 302 /${panel_path}/;
    }
    location @diag_sso_ok {
        add_header Set-Cookie "diag_key=${diag_token}; Path=${diag_path}; Secure; HttpOnly; SameSite=Lax; Max-Age=604800";
        return 302 ${diag_path};
    }
    location = /__diag_auth {
        internal;
        proxy_pass https://127.0.0.1:${panel_port}/${panel_path}/panel/;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        # 3x-ui answers AJAX requests with 401 instead of a login redirect
        proxy_set_header X-Requested-With XMLHttpRequest;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        # auth_request emits a raw 500 to the browser if the subrequest returns
        # anything other than 2xx / 401 / 403 (a login 302, or a 502 when the
        # panel's HTTPS cert is missing). Coerce every such status to a 401 deny
        # so the main location redirects to the panel login instead of 500ing.
        # 401/403 must be listed too, else the server-level "error_page 401 =404"
        # hijacks a genuine deny into a 404 (which auth_request then 500s on).
        proxy_intercept_errors on;
        error_page 300 301 302 303 304 305 307 308 400 401 402 403 404 405 500 501 502 503 504 =401 @diag_denied;
    }
    location @diag_denied { return 401; }

    # ── Network diagnostics page ─────────────────────────────────────────────
    # No diag cookie yet → bounce through the SSO bridge, which checks the panel
    # session and mints the cookie, so a bookmarked diag link "just works" once
    # you're logged into the panel. (Only the HTML page redirects; the API/asset
    # sub-locations below stay 404 without the cookie.)
    location ^~ ${diag_path} {
        if (\$diag_auth = 0) { return 302 /${panel_path}/diag; }
        limit_req  zone=diag_page burst=10 nodelay;
        limit_conn per_ip 5;
        alias /var/www/diagnostics/;
        index index.html;
        try_files \$uri \$uri/ /index.html;
        add_header Set-Cookie "diag_key=${diag_token}; Path=${diag_path}; Secure; HttpOnly; SameSite=Lax; Max-Age=604800" always;
        add_header Cache-Control "no-store" always;
        add_header X-Robots-Tag "noindex, nofollow" always;
    }

    # ── Diagnostics MTR API ──────────────────────────────────────────────────
    location ^~ ${diag_path}api/mtr {
        if (\$diag_auth = 0) { return 404; }
        limit_req  zone=diag_api burst=2 nodelay;
        limit_conn per_ip 2;
        proxy_pass         http://127.0.0.1:${mtr_backend_port}/api/mtr;
        proxy_http_version 1.1;
        proxy_set_header   X-Real-IP       \$remote_addr;
        proxy_set_header   X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
        proxy_send_timeout 120s;
        # Let the backend's JSON error bodies through; the server-level
        # "proxy_intercept_errors on" would otherwise rewrite a 500 into an HTML
        # 404 and break the frontend's response.json() parse.
        proxy_intercept_errors off;
    }

    # ── LibreSpeed upload sink ───────────────────────────────────────────────
    # No limit_req: librespeed fires many short POSTs (parallel streams).
    # proxy_request_buffering off = client sees true network backpressure.
    location ^~ ${diag_path}api/st/up {
        if (\$diag_auth = 0) { return 404; }
        access_log              off;
        limit_conn              per_ip 8;
        proxy_pass              http://127.0.0.1:${mtr_backend_port}/api/st/up;
        proxy_http_version      1.1;
        proxy_set_header        X-Real-IP       \$remote_addr;
        proxy_request_buffering off;
        client_max_body_size    64m;
        proxy_read_timeout      60s;
        proxy_send_timeout      60s;
        add_header              Cache-Control "no-store" always;
    }

    # ── LibreSpeed ping endpoint (answered by nginx, no backend hop) ─────────
    location = ${diag_path}api/st/ping {
        if (\$diag_auth = 0) { return 404; }
        access_log off;
        limit_conn per_ip 8;
        add_header Cache-Control "no-store" always;
        default_type text/plain;
        return 200 "";
    }

    # ── LibreSpeed client IP ─────────────────────────────────────────────────
    location = ${diag_path}api/st/getip {
        if (\$diag_auth = 0) { return 404; }
        proxy_pass          http://127.0.0.1:${mtr_backend_port}/api/st/getip;
        proxy_http_version  1.1;
        proxy_set_header    X-Real-IP \$remote_addr;
        add_header          Cache-Control "no-store" always;
    }

    # ── Download test files ──────────────────────────────────────────────────
    location ^~ ${diag_path}testfiles/ {
        if (\$diag_auth = 0) { return 404; }
        alias      /var/www/diagnostics/testfiles/;
        access_log off;
        add_header Cache-Control "no-store, no-cache, must-revalidate" always;
        add_header Content-Disposition "attachment" always;
    }

    # ── Clash YAML generator — internal, proxied here by rewrite from sub_path ────
    location = /__clash_api {
        internal;
        proxy_pass          http://127.0.0.1:${mtr_backend_port}/api/clash\$is_args\$args;
        proxy_http_version  1.1;
        proxy_set_header    X-Real-IP \$remote_addr;
    }

    include /etc/nginx/snippets/x-ui-auto-optional/*.conf;
    include /etc/nginx/snippets/includes.conf;
}
EOF

    # Reality domain vhost (plain TLS at 9443, no proxy_protocol)
    cat > "/etc/nginx/sites-available/${reality_domain}" <<EOF
server {
    server_tokens off;
    server_name ${reality_domain};
    listen 127.0.0.1:9443 ssl${http2_listen};
    ${http2_on}
    index index.html index.htm index.php;
    root /var/www/html/;
    ssl_protocols TLSv1.2 TLSv1.3;
    ssl_ciphers HIGH:!aNULL:!eNULL:!MD5:!DES:!RC4:!ADH:!SSLv3:!EXP:!PSK:!DSS;
    ssl_certificate     /etc/letsencrypt/live/${reality_domain}/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/${reality_domain}/privkey.pem;
    if (\$host !~* ^(.+\.)?${reality_domain}\$)            { return 444; }
    if (\$scheme ~* https)                                  { set \$safe 1; }
    if (\$ssl_server_name !~* ^(.+\.)?${reality_domain}\$) { set \$safe "\${safe}0"; }
    if (\$safe = 10)                                        { return 444; }
    if (\$request_uri ~ "(\"|'|\`|~|,|:|;|%|\\$|&&|\?\?|0x00|0X00|\||\\|\{|\}|\[|\]|<|>|\.\.\.|\.\.\/|\/\/\/)") { set \$hack 1; }
    error_page 400 401 402 403 500 501 502 503 504 =404 /404;
    proxy_intercept_errors on;

    location /${panel_path}/ {
        proxy_redirect off;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_pass http://127.0.0.1:${panel_port};
    }
    location /${panel_path} {
        proxy_redirect off;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_pass http://127.0.0.1:${panel_port};
    }

    include /etc/nginx/snippets/includes.conf;
}
EOF

    # Activate configs
    if [[ -f "/etc/nginx/sites-available/${domain}" ]]; then
        rm -f /etc/nginx/sites-enabled/default /etc/nginx/sites-available/default
        ln -sf "/etc/nginx/sites-available/00-maps.conf"       /etc/nginx/sites-enabled/
        ln -sf "/etc/nginx/sites-available/${domain}"          /etc/nginx/sites-enabled/
        ln -sf "/etc/nginx/sites-available/${reality_domain}"  /etc/nginx/sites-enabled/
        ln -sf "/etc/nginx/sites-available/80.conf"            /etc/nginx/sites-enabled/
    else
        msg_err "${domain} nginx config not found!" && exit 1
    fi

    if [[ $(nginx -t 2>&1 | grep -o 'successful') != "successful" ]]; then
        msg_err "nginx config check failed!" && exit 1
    fi

    systemctl reload nginx || exit 1
}

# ─────────────────────────────────────────────────────────────────────────────
# INSTALL PANEL (3x-ui)
# ─────────────────────────────────────────────────────────────────────────────
_arch() {
    case "$(uname -m)" in
        x86_64|x64|amd64)          echo 'amd64'  ;;
        i*86|x86)                  echo '386'    ;;
        armv8*|armv8|arm64|aarch64) echo 'arm64' ;;
        armv7*|armv7|arm)          echo 'armv7'  ;;
        armv6*|armv6)              echo 'armv6'  ;;
        armv5*|armv5)              echo 'armv5'  ;;
        s390x)                     echo 's390x'  ;;
        *) echo "Unsupported CPU architecture!" && exit 1 ;;
    esac
}

_panel_initial_config() {
    /usr/local/x-ui/x-ui setting -username "$config_username" -password "$config_password" \
        -port "$panel_port" -webBasePath "$panel_path" || return 1
    /usr/local/x-ui/x-ui migrate || return 1
    # The upstream CLI can report individual setting errors with exit code 0.
    local initialized
    initialized=$(sqlite3 "$XUIDB" "SELECT
        (SELECT username FROM users ORDER BY id LIMIT 1),
        COALESCE((SELECT value FROM settings WHERE key='webListen'), ''),
        (SELECT value FROM settings WHERE key='webPort'),
        (SELECT value FROM settings WHERE key='webBasePath');") || return 1
    [[ "$initialized" == "$config_username||$panel_port|/$panel_path/" ]] || {
        msg_err "3x-ui initial settings were not saved correctly."
        return 1
    }
}

# Verify the release sidecar before extracting any root-owned executable.
_download_panel_archive() {
    local url="$1" archive="$2" checksum="${2}.sha256" expected actual
    if ! curl -fLsS --connect-timeout 15 --max-time 300 "$url" -o "$archive" ||
       ! curl -fLsS --connect-timeout 15 --max-time 60 "${url}.sha256" -o "$checksum"; then
        rm -f "$archive" "$checksum"
        msg_err "Failed to download the 3x-ui release or checksum."
        return 1
    fi
    expected=$(awk 'NR == 1 {print $1}' "$checksum")
    actual=$(sha256sum "$archive") || {
        rm -f "$archive" "$checksum"
        return 1
    }
    actual="${actual%% *}"
    rm -f "$checksum"
    if [[ ! "$expected" =~ ^[0-9a-f]{64}$ || "$expected" != "$actual" ]]; then
        rm -f "$archive"
        msg_err "3x-ui release checksum verification failed."
        return 1
    fi
}

install_panel() {
    local tag_version archive
    apt-get update && apt-get install -y -q wget curl tar tzdata

    cd /usr/local/

    if [[ -n "$PANEL_VERSION" ]]; then
        tag_version="v${PANEL_VERSION#v}"
        if ! curl -fsLo /dev/null "https://api.github.com/repos/MHSanaei/3x-ui/releases/tags/${tag_version}" \
           && ! curl -4 -fsLo /dev/null "https://api.github.com/repos/MHSanaei/3x-ui/releases/tags/${tag_version}"; then
            echo "3x-ui release ${tag_version} not found." && exit 1
        fi
    else
        tag_version=$(curl -Ls "https://api.github.com/repos/MHSanaei/3x-ui/releases/latest" \
            | grep -m1 '"tag_name":' | sed -E 's/.*"tag_name": *"([^"]+)".*/\1/')
        if [[ ! "$tag_version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            tag_version=$(curl -4 -Ls "https://api.github.com/repos/MHSanaei/3x-ui/releases/latest" \
                | grep -m1 '"tag_name":' | sed -E 's/.*"tag_name": *"([^"]+)".*/\1/')
        fi
        if [[ ! "$tag_version" =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
            echo "Failed to fetch 3x-ui version." && exit 1
        fi
    fi

    _validate_panel_version "$tag_version" || return 1
    echo "Installing 3x-ui ${tag_version} ..."
    archive=$(mktemp /usr/local/x-ui-release.XXXXXX.tar.gz) || return 1
    _download_panel_archive \
        "https://github.com/MHSanaei/3x-ui/releases/download/${tag_version}/x-ui-linux-$(_arch).tar.gz" \
        "$archive" || return 1

    [[ -d /usr/local/x-ui/ ]] && systemctl stop x-ui 2>/dev/null; rm -rf /usr/local/x-ui/
    if ! tar zxvf "$archive" -C /usr/local; then
        rm -f "$archive"
        return 1
    fi
    rm -f "$archive"

    cd x-ui
    chmod +x x-ui x-ui.sh

    if [[ $(_arch) == "armv5" || $(_arch) == "armv6" || $(_arch) == "armv7" ]]; then
        mv bin/xray-linux-$(_arch) bin/xray-linux-arm
        chmod +x bin/xray-linux-arm
    fi
    chmod +x bin/xray-linux-$(_arch)

    install -m 0755 x-ui.sh /usr/bin/x-ui || return 1

    _panel_initial_config || return 1

    cp -f x-ui.service.debian /etc/systemd/system/x-ui.service
    systemctl daemon-reload

    msg_ok "3x-ui ${tag_version} installed."
}

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURE X-UI DATABASE
# ─────────────────────────────────────────────────────────────────────────────
configure_xui_db() {
    if [[ ! -f $XUIDB ]]; then
        msg_err "x-ui.db not found — panel may not be installed." && exit 1
    fi

    x-ui stop 2>/dev/null || true

    local output private_key public_key trojan_pass emoji_flag xray_bin
    # install_panel renames armv5/6/7 binaries to xray-linux-arm
    xray_bin="/usr/local/x-ui/bin/xray-linux-$(_arch)"
    [[ -f "$xray_bin" ]] || xray_bin="/usr/local/x-ui/bin/xray-linux-arm"
    output=$("$xray_bin" x25519)
    private_key=$(echo "$output" | grep "^PrivateKey:" | awk '{print $2}')
    public_key=$(echo "$output"  | grep "^Password"   | awk '{print $3}')
    trojan_pass=$(gen_random_string 10)
    # Per-host group_id: without it the panel cannot edit or delete the host.
    # The column only exists since 3x-ui v3.5.0 (pinnable via -version), so
    # probe the migrated schema and skip it on older releases.
    local gid_col="" gid_reality="" gid_ws="" gid_xhttp="" gid_trojan="" gid_hysteria=""
    if sqlite3 "$XUIDB" "PRAGMA table_info(hosts);" | grep -qw "group_id"; then
        gid_col='"group_id",'
        gid_reality="'$(gen_group_id)',"
        gid_ws="'$(gen_group_id)',"
        gid_xhttp="'$(gen_group_id)',"
        gid_trojan="'$(gen_group_id)',"
        gid_hysteria="'$(gen_group_id)',"
    fi
    emoji_flag=$(LC_ALL=en_US.UTF-8 curl -s --max-time 10 https://ipwho.is/ | jq -r '.flag.emoji' 2>/dev/null)
    [[ -z "$emoji_flag" || "$emoji_flag" == "null" ]] && emoji_flag="🌐"

    local sub_uri="https://${domain}/${sub_path}/"
    local json_uri="https://${domain}/${json_path}?name="

    # Prepare short IDs for REALITY
    local shor
    shor=($(openssl rand -hex 8) $(openssl rand -hex 8) $(openssl rand -hex 8) $(openssl rand -hex 8) \
           $(openssl rand -hex 8) $(openssl rand -hex 8) $(openssl rand -hex 8) $(openssl rand -hex 8))

    sqlite3 $XUIDB <<EOF
DELETE FROM "settings" WHERE "key" IN ("webCertFile","webKeyFile","webListen","subListen");

INSERT INTO "settings" ("key","value") VALUES ("subPort",             '${sub_port}');
UPDATE "settings" SET "value" = '/${sub_path}/' WHERE "key" = 'subPath';
INSERT INTO "settings" ("key","value") VALUES ("subURI",              '${sub_uri}');
UPDATE "settings" SET "value" = '/${json_path}/' WHERE "key" = 'subJsonPath';
INSERT INTO "settings" ("key","value") VALUES ("subJsonURI",          '${json_uri}');
INSERT INTO "settings" ("key","value") VALUES ("subClashEnable",      'false');
INSERT INTO "settings" ("key","value") VALUES ("subEnableRouting",    'false');
INSERT INTO "settings" ("key","value") VALUES ("subEnable",           'true');
INSERT INTO "settings" ("key","value") VALUES ("webListen",           '');
INSERT INTO "settings" ("key","value") VALUES ("subListen",           '127.0.0.1');
INSERT INTO "settings" ("key","value") VALUES ("webDomain",           '');
INSERT INTO "settings" ("key","value") VALUES ("webCertFile",         '');
INSERT INTO "settings" ("key","value") VALUES ("webKeyFile",          '');
INSERT INTO "settings" ("key","value") VALUES ("sessionMaxAge",       '60');
INSERT INTO "settings" ("key","value") VALUES ("pageSize",            '50');
INSERT INTO "settings" ("key","value") VALUES ("expireDiff",          '0');
INSERT INTO "settings" ("key","value") VALUES ("trafficDiff",         '0');
INSERT INTO "settings" ("key","value") VALUES ("remarkModel",         '-ieo');
INSERT INTO "settings" ("key","value") VALUES ("tgBotEnable",         'false');
INSERT INTO "settings" ("key","value") VALUES ("tgBotToken",          '');
INSERT INTO "settings" ("key","value") VALUES ("tgBotProxy",          '');
INSERT INTO "settings" ("key","value") VALUES ("tgBotAPIServer",      '');
INSERT INTO "settings" ("key","value") VALUES ("tgBotChatId",         '');
INSERT INTO "settings" ("key","value") VALUES ("tgRunTime",           '@daily');
INSERT INTO "settings" ("key","value") VALUES ("tgBotBackup",         'false');
INSERT INTO "settings" ("key","value") VALUES ("tgBotLoginNotify",    'true');
INSERT INTO "settings" ("key","value") VALUES ("tgCpu",               '80');
INSERT INTO "settings" ("key","value") VALUES ("tgLang",              'en-US');
INSERT INTO "settings" ("key","value") VALUES ("timeLocation",        'Europe/Moscow');
INSERT INTO "settings" ("key","value") VALUES ("secretEnable",        'false');
INSERT INTO "settings" ("key","value") VALUES ("subDomain",           '');
INSERT INTO "settings" ("key","value") VALUES ("subCertFile",         '');
INSERT INTO "settings" ("key","value") VALUES ("subKeyFile",          '');
INSERT INTO "settings" ("key","value") VALUES ("subUpdates",          '12');
INSERT INTO "settings" ("key","value") VALUES ("subEncrypt",          'true');
INSERT INTO "settings" ("key","value") VALUES ("subShowInfo",         'true');
INSERT INTO "settings" ("key","value") VALUES ("subJsonFragment",     '');
INSERT INTO "settings" ("key","value") VALUES ("subJsonNoises",       '');
INSERT INTO "settings" ("key","value") VALUES ("subJsonMux",          '');
INSERT INTO "settings" ("key","value") VALUES ("subJsonRules",        '');
INSERT INTO "settings" ("key","value") VALUES ("datepicker",          'gregorian');

INSERT INTO "inbounds"
    ("user_id","up","down","total","remark","enable","expiry_time","listen","port","protocol","settings","stream_settings","tag","sniffing")
VALUES (
    '1','0','0','0','${emoji_flag} reality','1','0','127.0.0.1','8443','vless',
    '{
  "clients": [],
  "decryption": "none",
  "fallbacks": []
}',
    '{
  "network": "tcp",
  "security": "reality",
  "realitySettings": {
    "show": false,
    "xver": 0,
    "target": "127.0.0.1:9443",
    "serverNames": ["${reality_domain}"],
    "privateKey": "${private_key}",
    "minClient": "",
    "maxClient": "",
    "maxTimediff": 0,
    "shortIds": [
      "${shor[0]}","${shor[1]}","${shor[2]}","${shor[3]}",
      "${shor[4]}","${shor[5]}","${shor[6]}","${shor[7]}"
    ],
    "settings": {
      "publicKey": "${public_key}",
      "fingerprint": "firefox",
      "serverName": "",
      "spiderX": "/"
    }
  },
  "tcpSettings": {
    "acceptProxyProtocol": true,
    "header": {"type":"none"}
  }
}',
    'inbound-8443',
    '{"enabled":true,"destOverride":["http","tls","quic","fakedns"],"metadataOnly":false,"routeOnly":false}'
);

INSERT INTO "inbounds"
    ("user_id","up","down","total","remark","enable","expiry_time","listen","port","protocol","settings","stream_settings","tag","sniffing")
VALUES (
    '1','0','0','0','${emoji_flag} ws','0','0','127.0.0.1','${ws_port}','vless',
    '{
  "clients": [],
  "decryption": "none",
  "fallbacks": []
}',
    '{
  "network": "ws",
  "security": "none",
  "wsSettings": {
    "acceptProxyProtocol": false,
    "path": "/${ws_port}/${ws_path}",
    "host": "${domain}",
    "headers": {}
  }
}',
    'inbound-${ws_port}',
    '{"enabled":true,"destOverride":["http","tls","quic","fakedns"],"metadataOnly":false,"routeOnly":false}'
);

INSERT INTO "inbounds"
    ("user_id","up","down","total","remark","enable","expiry_time","listen","port","protocol","settings","stream_settings","tag","sniffing")
VALUES (
    '1','0','0','0','${emoji_flag} xhttp','1','0','/dev/shm/uds2023.sock,0666','0','vless',
    '{
  "clients": [],
  "decryption": "none",
  "fallbacks": []
}',
    '{
  "network": "xhttp",
  "security": "none",
  "xhttpSettings": {
    "path": "/${xhttp_path}",
    "mode": "stream-up"
  },
  "sockopt": {
    "trustedXForwardedFor": ["X-Forwarded-For"]
  }
}',
    'inbound-/dev/shm/uds2023.sock,0666:0|',
    '{"enabled":true,"destOverride":["http","tls","quic","fakedns"],"metadataOnly":false,"routeOnly":false}'
);

INSERT INTO "inbounds"
    ("user_id","up","down","total","remark","enable","expiry_time","listen","port","protocol","settings","stream_settings","tag","sniffing")
VALUES (
    '1','0','0','0','${emoji_flag} trojan-grpc','0','0','127.0.0.1','${trojan_port}','trojan',
    '{
  "clients": [],
  "fallbacks": []
}',
    '{
  "network": "grpc",
  "security": "none",
  "grpcSettings": {
    "serviceName": "/${trojan_port}/${trojan_path}",
    "authority": "${domain}",
    "multiMode": false
  }
}',
    'inbound-${trojan_port}',
    '{"enabled":true,"destOverride":["http","tls","quic","fakedns"],"metadataOnly":false,"routeOnly":false}'
);

INSERT INTO "inbounds"
    ("user_id","up","down","total","remark","enable","expiry_time","listen","port","protocol","settings","stream_settings","tag","sniffing")
VALUES (
    '1','0','0','0','${emoji_flag} hysteria2','1','0','','443','hysteria',
    '{"version":2,"clients":[]}',
    '{
  "network": "hysteria",
  "security": "tls",
  "hysteriaSettings": {
    "version": 2,
    "udpIdleTimeout": 60
  },
  "tlsSettings": {
    "serverName": "${domain}",
    "alpn": ["h3"],
    "certificates": [{
      "certificateFile": "/root/cert/${domain}/fullchain.pem",
      "keyFile": "/root/cert/${domain}/privkey.pem",
      "ocspStapling": 0,
      "oneTimeLoading": false,
      "usage": "encipherment",
      "buildChain": false
    }]
  }
}',
    'inbound-443-udp',
    '{"enabled":true,"destOverride":["http","tls","quic","fakedns"],"metadataOnly":false,"routeOnly":false}'
);

-- Hosts supersede the legacy externalProxy arrays: one host per inbound,
-- rendered as the share-link endpoint at subscription time.
-- REALITY and Hysteria keep their own TLS params (security=same);
-- WS/XHTTP/Trojan front through nginx at :443 with TLS.
INSERT INTO "hosts" ("inbound_id",${gid_col}"sort_order","remark","address","port","security","fingerprint","alpn")
VALUES
    ((SELECT id FROM inbounds WHERE tag='inbound-8443'),           ${gid_reality} 0, 'reality', '${domain}', 443, 'same', '',        '[]'),
    ((SELECT id FROM inbounds WHERE tag='inbound-${ws_port}'),     ${gid_ws}      0, 'ws',      '${domain}', 443, 'tls',  'firefox', '["http/1.1"]'),
    ((SELECT id FROM inbounds WHERE tag='inbound-/dev/shm/uds2023.sock,0666:0|'), ${gid_xhttp} 0, 'xhttp', '${domain}', 443, 'tls', 'firefox', '["h2","http/1.1"]'),
    ((SELECT id FROM inbounds WHERE tag='inbound-${trojan_port}'), ${gid_trojan}  0, 'trojan',  '${domain}', 443, 'tls',  'firefox', '["h2","http/1.1"]'),
    ((SELECT id FROM inbounds WHERE tag='inbound-443-udp'),        ${gid_hysteria} 0, 'hysteria2', '${domain}', 443, 'same', '', '[]');
EOF
    [[ $? -eq 0 ]] || return 1

    /usr/local/x-ui/x-ui setting \
        -username  "${config_username}" \
        -password  "${config_password}" \
        -port      "${panel_port}"      \
        -webBasePath "${panel_path}" || return 1

    /usr/local/x-ui/x-ui cert \
        -webCert    "/root/cert/${domain}/fullchain.pem" \
        -webCertKey "/root/cert/${domain}/privkey.pem" || return 1
}

# ─────────────────────────────────────────────────────────────────────────────
# INSTALL FAKE SITE
# ─────────────────────────────────────────────────────────────────────────────
install_clash_sub() {
    local clash_dir="/var/www/subpage"
    mkdir -p "${clash_dir}"
    if curl -fsSL "${GITHUB_RAW}/assets/clash/clash.yaml" -o "${clash_dir}/clash.yaml.tpl"; then
        # Substitute domain and sub_path; leave ${EMAIL} for mtr-backend to fill per-request
        sed -i "s|\${DOMAIN}|${domain}|g"     "${clash_dir}/clash.yaml.tpl"
        sed -i "s|\${SUB_PATH}|${sub_path}|g" "${clash_dir}/clash.yaml.tpl"
        chown -R www-data:www-data "${clash_dir}" 2>/dev/null || true
        chmod 644 "${clash_dir}/clash.yaml.tpl"
        msg_ok "Clash subscription template installed."
    else
        msg_err "Failed to download clash.yaml from GitHub."
    fi
}

install_fake_site() {
    local idx=$(( (RANDOM % FAKE_SITE_COUNT) + 1 ))
    local site_id
    site_id=$(printf "site-%02d" "$idx")
    local url="${GITHUB_RAW}/assets/fake-sites/${site_id}/index.html"

    mkdir -p /var/www/html
    if curl -fsSL "$url" -o /var/www/html/index.html; then
        chown -R www-data:www-data /var/www/html 2>/dev/null || true
        chmod 644 /var/www/html/index.html
        msg_ok "Fake cover site '${site_id}' installed."
    else
        msg_err "Failed to download fake site ${site_id} from GitHub."
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# INSTALL NETWORK DIAGNOSTICS PAGE
# ─────────────────────────────────────────────────────────────────────────────
install_diagnostics() {
    local diag_webroot="/var/www/diagnostics"
    local backend_script="/usr/local/lib/3x-ui-pro/mtr-backend.py"

    # Diagnostics HTML page
    mkdir -p "${diag_webroot}"
    curl -fsSL "${GITHUB_RAW}/assets/diagnostics/index.html" -o "${diag_webroot}/index.html"
    sed -i \
        -e "s|__DIAG_PATH__|${diag_path}|g" \
        -e "s|__SERVER_DOMAIN__|${domain}|g" \
        -e "s|__SERVER_IP__|${IP4}|g" \
        "${diag_webroot}/index.html"

    # LibreSpeed engine (speed test frontend, LGPL — github.com/librespeed/speedtest)
    curl -fsSL "${GITHUB_RAW}/assets/diagnostics/librespeed/speedtest.js" \
        -o "${diag_webroot}/speedtest.js"
    curl -fsSL "${GITHUB_RAW}/assets/diagnostics/librespeed/speedtest_worker.js" \
        -o "${diag_webroot}/speedtest_worker.js"

    # Test download files
    local testfiles="${diag_webroot}/testfiles"
    mkdir -p "${testfiles}"
    [[ -f "${testfiles}/test-15k.bin"  ]] || dd if=/dev/zero bs=1024    count=15   of="${testfiles}/test-15k.bin"  status=none
    [[ -f "${testfiles}/test-17k.bin"  ]] || dd if=/dev/zero bs=1024    count=17   of="${testfiles}/test-17k.bin"  status=none
    [[ -f "${testfiles}/test-100m.bin" ]] || dd if=/dev/zero bs=1048576 count=100  of="${testfiles}/test-100m.bin" status=none
    [[ -f "${testfiles}/test-1g.bin"   ]] || dd if=/dev/zero bs=1048576 count=1024 of="${testfiles}/test-1g.bin"   status=none
    rm -f "${testfiles}/test-512m.bin"   # only used by the old single-stream speed test
    chown -R www-data:www-data "${diag_webroot}" 2>/dev/null || true

    # MTR backend Python script
    mkdir -p "$(dirname "${backend_script}")"
    curl -fsSL "${GITHUB_RAW}/assets/diagnostics/mtr-backend.py" -o "${backend_script}"
    chmod 755 "${backend_script}"

    # Grant mtr raw socket capability (runs as restricted user, no root needed)
    # mtr-packet is the helper that actually opens the raw socket
    command -v setcap &>/dev/null && setcap cap_net_raw+ep "$(command -v mtr)"        2>/dev/null || true
    command -v setcap &>/dev/null && setcap cap_net_raw+ep "$(command -v mtr-packet)" 2>/dev/null || true

    # Dedicated system user for mtr-backend
    id mtr-backend &>/dev/null || \
        useradd --system --no-create-home --shell /usr/sbin/nologin mtr-backend

    # Systemd service for mtr-backend
    cat > /etc/systemd/system/mtr-backend.service <<EOF
[Unit]
Description=3x-ui Auto Nginx MTR diagnostics backend
After=network.target

[Service]
Type=simple
User=mtr-backend
Group=mtr-backend
ExecStart=/usr/bin/python3 ${backend_script} --port ${mtr_backend_port}
Restart=on-failure
RestartSec=5s
NoNewPrivileges=yes
PrivateTmp=yes
ProtectSystem=strict
ProtectHome=yes
ProtectKernelTunables=yes
ProtectKernelModules=yes
ProtectControlGroups=yes
RestrictAddressFamilies=AF_INET AF_INET6 AF_NETLINK
RestrictNamespaces=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
RestrictRealtime=yes
RestrictSUIDSGID=yes
RemoveIPC=yes
# mtr-packet opens raw ICMP sockets. NoNewPrivileges=yes strips the file
# capability off the mtr binary, so grant CAP_NET_RAW the systemd-native way
# (ambient caps survive NoNewPrivileges). Empty here = mtr fails with
# "Failure to open IPv4 sockets: Permission denied".
AmbientCapabilities=CAP_NET_RAW
CapabilityBoundingSet=CAP_NET_RAW
StandardOutput=journal
StandardError=journal
SyslogIdentifier=mtr-backend

[Install]
WantedBy=multi-user.target
EOF

    systemctl daemon-reload
    systemctl enable mtr-backend
    systemctl restart mtr-backend

    msg_ok "Network diagnostics installed at https://${domain}/${panel_path}/diag (panel login required)"
}

# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM TUNING (BBR + kernel params)
# ─────────────────────────────────────────────────────────────────────────────
tune_system() {
    local managed_file=/etc/sysctl.d/99-3x-ui-pro.conf
    local params=(
        "net.core.default_qdisc=fq"
        "net.ipv4.tcp_congestion_control=bbr"
        "fs.file-max=2097152"
        "net.ipv4.tcp_timestamps=1"
        "net.ipv4.tcp_sack=1"
        "net.ipv4.tcp_window_scaling=1"
        "net.core.rmem_max=16777216"
        "net.core.wmem_max=16777216"
        "net.ipv4.tcp_rmem=4096 87380 16777216"
        "net.ipv4.tcp_wmem=4096 65536 16777216"
    )
    mkdir -p /etc/sysctl.d &&
        printf '%s\n' "${params[@]}" > "$managed_file" &&
        chown root:root "$managed_file" &&
        chmod 0644 "$managed_file" &&
        sysctl -p "$managed_file"
}

install_backup_tool() {
    local temporary
    mkdir -p /usr/local/bin || return 1
    temporary=$(mktemp /usr/local/bin/.x-ui-backup.XXXXXX) || return 1
    if ! curl -fsSL "${GITHUB_RAW}/assets/backup/x-ui-backup.sh" -o "$temporary" ||
       ! bash -n "$temporary" || [[ ! -s "$temporary" ]]; then
        rm -f "$temporary"
        msg_err "Failed to download a valid backup tool."
        return 1
    fi
    if ! chown root:root "$temporary" || ! chmod 0755 "$temporary" ||
       ! mv -fT "$temporary" /usr/local/bin/x-ui-backup; then
        rm -f "$temporary"
        msg_err "Failed to install the backup tool."
        return 1
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# CERTIFICATE RENEWAL
# ─────────────────────────────────────────────────────────────────────────────

check_no_legacy_certbot_cron() {
    command -v crontab >/dev/null || return 0
    local directory
    directory=$(mktemp -d) || return 1
    if ! LC_ALL=C crontab -l > "$directory/current" 2> "$directory/error"; then
        if grep -qi 'no crontab for' "$directory/error"; then rm -rf "$directory"; return 0; fi
        rm -rf "$directory"; return 1
    fi
    if grep -Fxq -- "$LEGACY_CERTBOT_CRON" "$directory/current"; then rm -rf "$directory"; return 1; fi
    rm -rf "$directory"
}

check_certificate_identity() {
    python3 - "$1" <<'PYIDENTITY'
import sys
from pathlib import Path
from cryptography import x509
try:
    domain = sys.argv[1]
    live = Path('/etc/letsencrypt/live') / domain
    cert = x509.load_pem_x509_certificate((live / 'fullchain.pem').read_bytes())
    names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    if set(names) != {domain} or not (live / 'privkey.pem').is_file():
        raise ValueError('expected a separate exact-domain certificate and key')
except (OSError, ValueError, x509.ExtensionNotFound) as exc:
    print('[FAIL] Existing certificate identity mismatch: ' + str(exc), file=sys.stderr)
    sys.exit(1)
PYIDENTITY
}

check_webroot_lineage() {
    python3 - "$1" <<'PY'
import sys
from pathlib import Path
from configobj import ConfigObj, ConfigObjError
from cryptography import x509
try:
    domain = sys.argv[1]
    live = Path('/etc/letsencrypt/live') / domain
    conf = Path('/etc/letsencrypt/renewal') / (domain + '.conf')
    cfg = ConfigObj(str(conf), encoding='utf-8', file_error=True)
    params = cfg['renewalparams']
    if params.get('authenticator') != 'webroot':
        raise ValueError('authenticator must be webroot')
    for name in ('fullchain', 'privkey'):
        path = live / (name + '.pem')
        if cfg.get(name) != str(path) or not path.is_file() or not path.stat().st_size:
            raise ValueError('missing or unexpected certificate/key path')
    cert = x509.load_pem_x509_certificate((live / 'fullchain.pem').read_bytes())
    names = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
    if set(names) != {domain}:
        raise ValueError('expected a separate exact-domain lineage')
    mapping = params.get('webroot_map', {})
    roots = params.get('webroot_path', [])
    if isinstance(roots, str):
        roots = [roots]
    effective = mapping.get(domain) if domain in mapping else (roots[-1] if roots else None)
    if effective != '/var/www/acme':
        raise ValueError('unexpected persisted webroot')
    if any(params.get(key) for key in ('pre_hook', 'post_hook', 'renew_hook', 'deploy_hook')):
        raise ValueError('unexpected per-lineage hooks; use the project deploy directory hook')
except (OSError, ValueError, KeyError, ConfigObjError, x509.ExtensionNotFound) as exc:
    print('[FAIL] Invalid webroot renewal lineage: ' + str(exc), file=sys.stderr)
    sys.exit(1)
PY
}

check_acme_http() {
    local domain=$1 reality_domain=$2 token body code failed=0
    token=$(openssl rand -hex 16) || return 1
    printf '%s' "$token" > "/var/www/acme/.well-known/acme-challenge/$token" || { rm -f "/var/www/acme/.well-known/acme-challenge/$token"; return 1; }
    chmod 0644 "/var/www/acme/.well-known/acme-challenge/$token" || { rm -f "/var/www/acme/.well-known/acme-challenge/$token"; return 1; }
    local d
    for d in "$domain" "$reality_domain"; do
        body=$(curl --noproxy '*' -fsS --connect-timeout 5 --max-time 10 -H "Host: $d" \
            "http://127.0.0.1/.well-known/acme-challenge/$token") || failed=1
        [[ "$body" == "$token" ]] || failed=1
        code=$(curl --noproxy '*' -sS --connect-timeout 5 --max-time 10 -o /dev/null -w '%{http_code} %{redirect_url}' \
            -H "Host: $d" http://127.0.0.1/) || failed=1
        [[ "$code" == "301 https://$d/" ]] || failed=1
    done
    rm -f "/var/www/acme/.well-known/acme-challenge/$token" || return 1
    (( ! failed ))
}

check_certificate_renewal() {
    local domain=$1 reality_domain=$2 hook=/etc/letsencrypt/renewal-hooks/deploy/3x-ui-auto-nginx
    check_webroot_lineage "$domain" && check_webroot_lineage "$reality_domain" || return 1
    [[ -f "$hook" && ! -L "$hook" && -x "$hook" && "$(stat -c '%u:%g:%a' "$hook")" == '0:0:755' ]] || return 1
    bash -n "$hook" || return 1
    # Verify restored/generated hook domains without executing arbitrary hook code.
    python3 - "$hook" "$domain" "$reality_domain" <<'PY'
import shlex, sys
values = {}
for line in open(sys.argv[1]):
    for key in ('PANEL_DOMAIN', 'REALITY_DOMAIN'):
        if line.startswith(key + '='):
            parts = shlex.split(line.strip())
            if len(parts) == 1:
                values[key] = parts[0].split('=', 1)[1]
if [values.get('PANEL_DOMAIN'), values.get('REALITY_DOMAIN')] != sys.argv[2:]:
    sys.exit('Deploy hook domains do not match this installation')
PY
    [[ $? == 0 ]] || return 1
    systemctl is-enabled --quiet certbot.timer && systemctl is-active --quiet certbot.timer || return 1
    check_no_legacy_certbot_cron && nginx -t && check_acme_http "$domain" "$reality_domain"
}

setup_certificate_renewal() {
    local hook=/etc/letsencrypt/renewal-hooks/deploy/3x-ui-auto-nginx
    remove_legacy_certbot_cron || return 1
    install -d -o root -g root -m 0755 /etc/letsencrypt/renewal-hooks/deploy || return 1
    {
        printf '#!/usr/bin/env bash\nset -Eeuo pipefail\n'
        printf 'PANEL_DOMAIN=%q\nREALITY_DOMAIN=%q\n' "$domain" "$reality_domain"
        cat <<'HOOK'
panel=0
project=0
read -r -a renewed_domains <<< "${RENEWED_DOMAINS:-}"
for renewed in "${renewed_domains[@]}"; do
    if [[ "$renewed" == "$PANEL_DOMAIN" ]]; then panel=1; project=1; fi
    if [[ "$renewed" == "$REALITY_DOMAIN" ]]; then project=1; fi
done
case "${RENEWED_LINEAGE:-}" in
    "/etc/letsencrypt/live/$PANEL_DOMAIN") panel=1; project=1 ;;
    "/etc/letsencrypt/live/$REALITY_DOMAIN") project=1 ;;
esac
(( project )) || exit 0
nginx -t
systemctl reload nginx
systemctl is-active --quiet nginx
if (( panel )); then
    systemctl restart x-ui
    systemctl is-active --quiet x-ui
fi
HOOK
    } > "$hook" || return 1
    chown root:root "$hook" && chmod 0755 "$hook" && bash -n "$hook" || return 1
    systemctl enable --now certbot.timer || return 1
    check_certificate_renewal "$domain" "$reality_domain"
}

check_installation() {
    local service attempt
    for service in x-ui nginx mtr-backend; do
        systemctl is-active --quiet "$service" || { msg_err "$service is not active."; return 1; }
    done
    check_certificate_renewal "$domain" "$reality_domain" || return 1
    nginx -t || return 1
    for attempt in {1..10}; do
        [[ -S /dev/shm/uds2023.sock ]] && return 0
        sleep 0.5
    done
    msg_err "XHTTP Unix socket is missing."
    return 1
}

# ─────────────────────────────────────────────────────────────────────────────
# FIREWALL
# ─────────────────────────────────────────────────────────────────────────────
setup_firewall() {
    local status port client_ip client_port server_ip extra ssh_config
    local -a ssh_ports=()
    status=$(LC_ALL=C ufw status) || return 1
    case "$status" in
        "Status: active"*|"Status: inactive"*) ;;
        *) msg_err "Cannot determine UFW status." >&2; return 1 ;;
    esac

    for port in 80/tcp 443/tcp 443/udp; do
        ufw allow "$port" || return 1
    done
    [[ "$status" == "Status: active"* ]] && return 0

    read -r client_ip client_port server_ip port extra <<< "${SSH_CONNECTION:-}"
    if [[ -n "$client_ip" && -n "$client_port" && -n "$server_ip" && -z "$extra" && "$port" =~ ^[0-9]{1,5}$ ]] \
       && (( 10#$port >= 1 && 10#$port <= 65535 )); then
        ssh_ports=("$port")
    else
        ssh_config=$(sshd -T 2>/dev/null) || ssh_config=""
        mapfile -t ssh_ports < <(awk '$1 == "port" {print $2}' <<< "$ssh_config" | sort -u)
    fi

    local ssh_allowed=false
    for port in "${ssh_ports[@]}"; do
        if [[ "$port" =~ ^[0-9]{1,5}$ ]] && (( 10#$port >= 1 && 10#$port <= 65535 )); then
            ufw allow "$((10#$port))/tcp" || return 1
            ssh_allowed=true
        fi
    done
    if [[ "$ssh_allowed" == true ]]; then
        ufw --force enable || return 1
    else
        echo "WARNING: UFW is inactive and the SSH port could not be detected. Application rules were added, but UFW was not enabled automatically." >&2
    fi
}

# ─────────────────────────────────────────────────────────────────────────────
# SHOW RESULTS
# ─────────────────────────────────────────────────────────────────────────────
select_adguard() {
    INSTALL_AGH=n
    local answer
    while true; do
        printf 'Install AdGuard Home with DNS-over-HTTPS? [y/N]: '
        if ! IFS= read -r answer; then return 0; fi
        case "$answer" in
            ''|n|N) return 0 ;;
            y|Y)
                case "$(uname -m)" in
                    x86_64|aarch64|arm64) INSTALL_AGH=y; return 0 ;;
                    *) msg_err 'AdGuard Home supports only amd64/arm64; existing deployment was not changed.'; return 1 ;;
                esac ;;
            *) printf 'Please enter y or n.\n' ;;
        esac
    done
}

adguard_admin_probe() {
    # Install-only: Backup/Restore health never needs the plaintext password.
    (
        local directory result
        directory=$(mktemp -d) || exit 1
        trap 'rm -rf -- "$directory"' EXIT
        chmod 0700 "$directory" || exit 1
        umask 077
        printf '%s' "$AGH_PASSWORD" | python3 -c 'import json,sys; json.dump({"name":"admin","password":sys.stdin.read()}, sys.stdout)' > "$directory/login.json" || exit 1
        chmod 0600 "$directory/login.json" || exit 1
        result=$(curl --noproxy '*' -fsS --connect-timeout 5 --max-time 15 \
            --resolve "${domain}:443:127.0.0.1" -H 'Content-Type: application/json' \
            --data-binary "@$directory/login.json" -c "$directory/cookies" \
            -o "$directory/login-response" -w '%{http_code}' \
            "https://${domain}/${AGH_PATH}/control/login") || exit 1
        [[ "$result" == 200 ]] || exit 1
        # Require nginx's cookie-path rewrite, not merely a successful login HTTP code.
        python3 - "$directory/cookies" "$domain" "/${AGH_PATH}/" <<'PY'
import pathlib, sys
cookie_file, domain, path = sys.argv[1:]
cookies = []
for line in pathlib.Path(cookie_file).read_text().splitlines():
    if line.startswith('#HttpOnly_'):
        line = line[len('#HttpOnly_'):]
    elif line.startswith('#'):
        continue
    fields = line.split('\t')
    if len(fields) == 7:
        cookies.append(fields)
if not any(c[0].lower() == domain.lower() and c[2] == path and c[5] == 'agh_session' and c[6] for c in cookies):
    raise ValueError('Missing usable AGH session cookie at the managed admin prefix')
PY
        [[ $? == 0 ]] || exit 1
        result=$(curl --noproxy '*' -fsS --connect-timeout 5 --max-time 15 \
            --resolve "${domain}:443:127.0.0.1" -b "$directory/cookies" \
            -o "$directory/status.json" -w '%{http_code}' \
            "https://${domain}/${AGH_PATH}/control/status") || exit 1
        [[ "$result" == 200 ]] || exit 1
        python3 - "$directory/status.json" "$AGH_WEB_PORT" "$AGH_DNS_PORT" <<'PY'
import json, pathlib, sys
status = json.loads(pathlib.Path(sys.argv[1]).read_text())
if not isinstance(status, dict) or status.get('version') != 'v0.107.79' or status.get('running') is not True:
    raise ValueError('Invalid authenticated AGH status')
for key, expected in (('http_port', sys.argv[2]), ('dns_port', sys.argv[3])):
    if type(status.get(key)) is not int or status[key] != int(expected):
        raise ValueError('Authenticated AGH status does not match managed ports')
PY
    )
}

adguard_stage() {
    # The shared helper is conditional and uses the same validated project ref.
    curl -fsSL "${GITHUB_RAW}/assets/adguard/managed.sh" -o "$AGH_TEMP/helper" &&
        bash -n "$AGH_TEMP/helper" || return 1
    install -o root -g root -m 0600 "$AGH_TEMP/helper" /usr/local/lib/3x-ui-pro/managed-adguard.sh || return 1
    . /usr/local/lib/3x-ui-pro/managed-adguard.sh
    agh_release "$(uname -m)" || return 1
    apt-get install -y --no-install-recommends apache2-utils || return 1
    curl -fsSL --connect-timeout 15 --max-time 180 \
        "https://github.com/AdguardTeam/AdGuardHome/releases/download/${AGH_VERSION}/AdGuardHome_linux_${AGH_ARCH}.tar.gz" \
        -o "$AGH_TEMP/archive" || return 1
    [[ "$(sha256sum "$AGH_TEMP/archive" | awk '{print $1}')" == "$AGH_SHA" ]] || return 1
    mkdir "$AGH_TEMP/extracted" || return 1
    python3 - "$AGH_TEMP/archive" "$AGH_TEMP/extracted" <<'PY'
import pathlib, sys, tarfile
with tarfile.open(sys.argv[1], 'r:gz') as tar:
    names = set()
    for item in tar:
        name = item.name.removeprefix('./').rstrip('/')
        if not (name == 'AdGuardHome' or name in {'AdGuardHome/' + n for n in (
            'AdGuardHome', 'CHANGELOG.md', 'AdGuardHome.sig', 'LICENSE.txt', 'README.md')}):
            raise ValueError('Invalid AGH unexpected release archive member')
        if not (name not in names):
            raise ValueError('Invalid AGH duplicate release archive entry')
        names.add(name)
        if not (item.isdir() if name == 'AdGuardHome' else item.isfile()):
            raise ValueError('Invalid AGH release archive member type')
    if not ('AdGuardHome/AdGuardHome' in names):
        raise ValueError('Invalid AGH required release binary')
    tar.extractall(sys.argv[2], filter='data')
PY
    [[ $? == 0 ]] || return 1
    [[ -f "$AGH_TEMP/extracted/AdGuardHome/AdGuardHome" && ! -L "$AGH_TEMP/extracted/AdGuardHome/AdGuardHome" ]] || return 1
    chmod 0755 "$AGH_TEMP/extracted/AdGuardHome/AdGuardHome" || return 1
    [[ "$("$AGH_TEMP/extracted/AdGuardHome/AdGuardHome" --version)" == "AdGuard Home, version $AGH_VERSION" ]] || return 1
    mv "$AGH_TEMP/extracted/AdGuardHome" /opt/AdGuardHome || return 1
    chown -R root:root /opt/AdGuardHome && chmod 0700 /opt/AdGuardHome || return 1
    AGH_PASSWORD=$(gen_random_string 32) || return 1
    AGH_PATH="adg-$(gen_random_string 12)" || return 1
    [[ "$AGH_PASSWORD" =~ ^[A-Za-z0-9]{32}$ && "$AGH_PATH" =~ ^adg-[A-Za-z0-9]{12}$ ]] || return 1
    local hash ports
    hash=$(printf '%s\n' "$AGH_PASSWORD" | htpasswd -niB -C 12 admin) || return 1
    hash=${hash#admin:}
    [[ "$hash" =~ ^\$2[aby]\$12\$[./A-Za-z0-9]{53}$ ]] || return 1
    # Binding both TCP and UDP sockets avoids the core generator's TCP-only probe.
    ports=$(python3 - <<'PY'
import secrets, socket
sockets = []
try:
    for _ in range(100):
        port = 10000 + secrets.randbelow(55536)
        try:
            tcp, udp = socket.socket(), socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            tcp.bind(('127.0.0.1', port)); udp.bind(('127.0.0.1', port))
        except OSError:
            tcp.close(); udp.close(); continue
        sockets.extend([tcp, udp]); print(port)
        if len(sockets) == 4: break
    if not (len(sockets) == 4):
        raise ValueError('Invalid AGH allocation of two TCP/UDP ports')
finally:
    for s in sockets: s.close()
PY
    ) || return 1
    local -a selected
    mapfile -t selected <<< "$ports"
    AGH_WEB_PORT=${selected[0]} AGH_DNS_PORT=${selected[1]} AGH_DOMAIN=$domain
    cat > /opt/AdGuardHome/AdGuardHome.yaml <<EOFAGH
http:
  address: 127.0.0.1:${AGH_WEB_PORT}
  doh:
    insecure_enabled: true
    routes:
      - GET /dns-query
      - POST /dns-query
users:
  - name: admin
    password: '${hash}'
auth_attempts: 5
block_auth_min: 15
dns:
  bind_hosts:
    - 127.0.0.1
  port: ${AGH_DNS_PORT}
  upstream_dns:
    - https://dns.cloudflare.com/dns-query
    - https://dns.google/dns-query
    - https://dns.quad9.net/dns-query
  bootstrap_dns:
    - 1.1.1.1
    - 8.8.8.8
    - 9.9.9.9
  trusted_proxies:
    - 127.0.0.1/32
filtering:
  protection_enabled: true
  filtering_enabled: true
filters:
  - enabled: true
    url: https://adguardteam.github.io/HostlistsRegistry/assets/filter_1.txt
    name: AdGuard DNS filter
    id: 1
tls:
  enabled: false
schema_version: 34
EOFAGH
    [[ $? == 0 ]] || return 1
    chmod 0600 /opt/AdGuardHome/AdGuardHome.yaml || return 1
    python3 - /opt/AdGuardHome/managed.json "$domain" "$AGH_PATH" "$AGH_WEB_PORT" "$AGH_DNS_PORT" "$AGH_ARCH" <<'PY'
import json, sys
file, domain, path, web, dns, arch = sys.argv[1:]
with open(file, 'w') as out:
    json.dump(dict(version='v0.107.79', domain=domain, path=path, web_port=int(web), dns_port=int(dns), arch=arch), out)
PY
    [[ $? == 0 ]] || return 1
    chmod 0600 /opt/AdGuardHome/managed.json || return 1
    agh_install_service || return 1
    agh_snippet > "$AGH_TEMP/candidate" || return 1
    install -o root -g root -m 0600 "$AGH_TEMP/candidate" /etc/nginx/snippets/x-ui-auto-optional/adguard.conf || return 1
    nginx -t || return 1
    AGH_NGINX_RELOAD_ATTEMPTED=1
    systemctl reload nginx && agh_health && adguard_admin_probe && check_installation
}

install_adguard() {
    local AGH_TEMP had_snippet=0 result=0 AGH_NGINX_RELOAD_ATTEMPTED=0
    AGH_TEMP=$(mktemp -d) || return 1
    chmod 0700 "$AGH_TEMP" || { rm -rf "$AGH_TEMP"; return 1; }
    if [[ -e /etc/nginx/snippets/x-ui-auto-optional/adguard.conf ]]; then
        cp -a /etc/nginx/snippets/x-ui-auto-optional/adguard.conf "$AGH_TEMP/previous" || { rm -rf "$AGH_TEMP"; return 1; }
        had_snippet=1
    fi
    if ! adguard_stage; then
        msg_err 'AdGuard Home stage failed.'
        cleanup_adguard || result=1
        if (( had_snippet )); then
            cp -a "$AGH_TEMP/previous" /etc/nginx/snippets/x-ui-auto-optional/adguard.conf || result=1
        fi
        nginx -t || result=1
        if (( AGH_NGINX_RELOAD_ATTEMPTED )); then systemctl reload nginx || result=1; fi
        check_installation || result=1
        AGH_PASSWORD=''
        if (( result == 0 )); then msg_warn 'Core 3x-ui stack remains operational.';
        else msg_err 'Rollback could not be verified. Inspect nginx and core services.'; fi
        rm -rf "$AGH_TEMP"
        return 1
    fi
    rm -rf "$AGH_TEMP"
}

show_results() {
    local version version_label="" firewall
    version=$(/usr/local/x-ui/x-ui -v 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1) || version=""
    [[ -n "$version" ]] && version_label=" (3x-ui ${version})"

    echo
    msg_inf '============================================================'
    msg_inf '  3x-ui Auto Nginx — Installation Complete'
    msg_inf '============================================================'
    # main() has already passed check_installation for services, nginx and certificate renewal.
    msg_ok " [✓] 3x-ui / Xray           Running${version_label}"
    case "${CPU_SUPPORT_LEVEL:-info}" in
        ok)   msg_ok " [✓] CPU support             ${CPU_SUPPORT_TEXT}" ;;
        warn) msg_warn " [!] CPU support             ${CPU_SUPPORT_TEXT}" ;;
        *)    msg_inf " [i] CPU support             ${CPU_SUPPORT_TEXT:-Compatible (acceleration not assessed)}" ;;
    esac
    msg_ok ' [✓] nginx                  Running'
    if [[ -s "/etc/letsencrypt/live/${domain}/fullchain.pem" && -s "/etc/letsencrypt/live/${domain}/privkey.pem" &&
          -s "/etc/letsencrypt/live/${reality_domain}/fullchain.pem" && -s "/etc/letsencrypt/live/${reality_domain}/privkey.pem" &&
          -s "/root/cert/${domain}/fullchain.pem" && -s "/root/cert/${domain}/privkey.pem" ]]; then
        msg_ok ' [✓] TLS certificates       Ready'
    else
        msg_warn ' [!] TLS certificates       Check certificate files'
    fi
    msg_ok ' [✓] XHTTP                  Ready'
    if [[ -s /var/www/diagnostics/index.html && -s /var/www/diagnostics/speedtest.js &&
          -s /var/www/diagnostics/speedtest_worker.js && -s /usr/local/lib/3x-ui-pro/mtr-backend.py ]]; then
        msg_ok ' [✓] Diagnostics            Ready'
    else
        msg_warn ' [!] Diagnostics            Check diagnostics files'
    fi
    if [[ -x /usr/local/bin/x-ui-backup ]]; then
        msg_ok ' [✓] Backup / Restore       Installed'
    else
        msg_warn ' [!] Backup / Restore       Backup utility unavailable'
    fi
    if [[ "${INSTALL_AGH:-n}" == y ]]; then
        msg_ok ' [✓] AdGuard Home           Running (v0.107.79)'
        msg_ok ' [✓] DNS-over-HTTPS         Ready'
    fi
    msg_ok ' [✓] Certificate renewal    Webroot + systemd timer'
    firewall=$(LC_ALL=C ufw status 2>/dev/null) || firewall=""
    case "$firewall" in
        "Status: active"*)   msg_ok ' [✓] Firewall / UFW         Active' ;;
        "Status: inactive"*) msg_warn ' [!] Firewall / UFW         Inactive — review firewall settings' ;;
        *)                   msg_warn ' [!] Firewall / UFW         Unknown — review firewall settings' ;;
    esac

    printf '\n Enabled by default: REALITY · XHTTP · Hysteria2\n Optional profiles: WS · Trojan gRPC\n'
    msg_inf "\n Panel:"
    msg_inf " https://${domain}/${panel_path}/"
    msg_inf "\n Diagnostics (panel login required):"
    msg_inf " https://${domain}/${panel_path}/diag"
    printf '\n Username: %s\n Password: %s\n' "$config_username" "$config_password"
    msg_inf "\n Backup:"
    printf ' x-ui-backup backup\n\n'
    if [[ "${INSTALL_AGH:-n}" == y ]]; then
        msg_inf " AdGuard Home: https://${domain}/${AGH_PATH}/"
        printf ' Login: admin\n Password: %s\n DoH: https://%s/dns-query\n\n' "$AGH_PASSWORD" "$domain"
        AGH_PASSWORD=''
    fi
    msg_inf '============================================================'
    msg_inf ' Save these credentials before closing the terminal.'
    msg_inf '============================================================'
}

# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────
main() {
    confirm_destructive_reinstall || exit 1
    validate_domains
    select_adguard || exit 1
    cleanup_adguard || { msg_err "Cannot clean AdGuard Home; core deployment was not removed."; exit 1; }
    clean_previous_install
    install_packages || { msg_err "Dependency setup failed."; exit 1; }
    setup_firewall || { msg_err "Firewall setup failed."; exit 1; }
    get_server_ip
    setup_acme_http || { msg_err "ACME HTTP setup failed."; exit 1; }
    get_ssl_certs || { msg_err "Webroot certificate setup failed."; exit 1; }

    install_panel || exit 1

    configure_nginx
    configure_xui_db || exit 1
    install_clash_sub
    install_fake_site
    install_diagnostics
    tune_system || exit 1
    install_backup_tool || exit 1
    setup_certificate_renewal || { msg_err "Certificate renewal setup failed."; exit 1; }

    if ! systemctl is-enabled --quiet x-ui; then
        systemctl daemon-reload && systemctl enable x-ui.service || exit 1
    fi
    x-ui restart || exit 1
    check_installation || { msg_err "Installation failed mandatory health checks."; exit 1; }

    if [[ "$INSTALL_AGH" == y ]]; then install_adguard || exit 1; fi
    show_results
}

main
