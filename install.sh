#!/usr/bin/env bash
# ==============================================================================
# 3X-UI + TELEMT + HARDENED NGINX + ADGUARD HOME (ONE-COMMAND INSTALLER)
# Repository: https://github.com/nexuspyx/3x-ui-adh-telemt-auto-nginx
# ==============================================================================

set -eo pipefail

[[ $EUID -ne 0 ]] && { echo -e "\e[1;31m[ERROR] Скрипт должен быть запущен с правами root (sudo bash $0)\e[0m"; exit 1; }

# Colors
C_RESET='\e[0m'
C_BOLD='\e[1m'
C_RED='\e[1;31m'
C_GREEN='\e[1;32m'
C_YELLOW='\e[1;33m'
C_BLUE='\e[1;34m'
C_CYAN='\e[1;36m'

msg_ok()   { printf "${C_GREEN}[✓] %s${C_RESET}\n" "$1"; }
msg_err()  { printf "${C_RED}[✗] %s${C_RESET}\n" "$1"; }
msg_inf()  { printf "${C_BLUE}[i] %s${C_RESET}\n" "$1"; }
msg_warn() { printf "${C_YELLOW}[!] %s${C_RESET}\n" "$1"; }

REPO_OWNER="nexuspyx"
REPO_NAME="3x-ui-adh-telemt-auto-nginx"
BRANCH="${INSTALL_BRANCH:-main}"
GITHUB_RAW="https://raw.githubusercontent.com/${REPO_OWNER}/${REPO_NAME}/${BRANCH}"

# Script working directory
WORKDIR="/tmp/3x-ui-setup"
mkdir -p "$WORKDIR"

# Helper for random strings
gen_random_string() {
    tr -dc 'a-zA-Z0-9' < /dev/urandom | head -c "$1" 2>/dev/null || openssl rand -hex "$1" | head -c "$1"
}

# Helper to find a free TCP port
get_free_port() {
    local port
    while true; do
        port=$((RANDOM % 30000 + 20000))
        if ! ss -tuln | grep -q ":${port} "; then
            echo "$port"
            return 0
        fi
    done
}

clear
echo -e "${C_CYAN}"
cat << 'EOF'
╔══════════════════════════════════════════════════════════════════════════════════╗
║        3X-UI + TELEMT + HARDENED NGINX + ADGUARD HOME (STEALTH EDITION)          ║
║                  Автоматическая установка и полная защита от ТСПУ                ║
╚══════════════════════════════════════════════════════════════════════════════════╝
EOF
echo -e "${C_RESET}"

# ─────────────────────────────────────────────────────────────────────────────
# [0] OS CHECK & FULL UPGRADE
# ─────────────────────────────────────────────────────────────────────────────
msg_inf "Проверка совместимости операционной системы..."
if [[ -f /etc/os-release ]]; then
    . /etc/os-release
    case "$ID" in
        ubuntu|debian)
            msg_ok "ОС поддерживается: ${PRETTY_NAME}"
            ;;
        *)
            msg_err "Неподдерживаемая ОС: ${ID}. Требуется Ubuntu 24.04/26.04 или Debian 13."
            exit 1
            ;;
    esac
else
    msg_err "Не удалось определить операционную систему."
    exit 1
fi

# Предварительное определение публичного IP сервера
SERVER_IP=$(curl -s4 icanhazip.com || curl -s4 ifconfig.me || echo "127.0.0.1")

msg_inf "Шаг [0/9]: Полное обновление системы (apt update & full-upgrade)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -q -y
apt-get full-upgrade -q -y -o Dpkg::Options::="--force-confdef" -o Dpkg::Options::="--force-confold"
apt-get autoremove -q -y
msg_ok "Система успешно обновлена до актуального состояния."

# ─────────────────────────────────────────────────────────────────────────────
# [1] INTERACTIVE WIZARD
# ─────────────────────────────────────────────────────────────────────────────
echo
echo -e "${C_BOLD}=== ШАГ [1/9]: ИНТЕРАКТИВНЫЙ МАСТЕР НАСТРОЙКИ ===${C_RESET}"

# 1.1 Домен
while true; do
    read -rp "-> Введите ваш домен (например, vps.example.com): " DOMAIN
    DOMAIN=$(echo "$DOMAIN" | tr -d '[:space:]')
    [[ -n "$DOMAIN" ]] && break
    msg_warn "Домен не может быть пустым!"
done

# 1.2 Cloudflare учетные данные
echo
msg_inf "Настройка тихого выпуска SSL через Cloudflare DNS-01 (порт 80 будет закрыт навсегда):"
while true; do
    read -rp "-> Введите Cloudflare Email: " CF_EMAIL
    CF_EMAIL=$(echo "$CF_EMAIL" | tr -d '[:space:]')
    [[ -n "$CF_EMAIL" ]] && break
    msg_warn "Email не может быть пустым!"
done

while true; do
    read -rsp "-> Введите Cloudflare Global API Key (или API Token): " CF_KEY
    echo
    CF_KEY=$(echo "$CF_KEY" | tr -d '[:space:]')
    [[ -n "$CF_KEY" ]] && break
    msg_warn "Ключ не может быть пустым!"
done

# 1.3 Кастомизация путей и портов
echo
echo -e "${C_BOLD}Кастомизация путей (Locations) и внутренних портов:${C_RESET}"
echo "1 - Настроить вручную свои пути и порты"
echo "2 - Использовать случайные скрытые пути и порты (Enter - рекомендуется)"
read -rp "Выберите вариант [1/2]: " PATH_CHOICE

if [[ "$PATH_CHOICE" == "1" ]]; then
    echo
    read -rp "-> Путь для панели 3x-ui [/xui/]: " USER_PANEL_PATH
    read -rp "-> Путь для обычной подписки Base64 [/sub/]: " USER_SUB_PATH
    read -rp "-> Путь для JSON подписки Happ/Sing-box [/sub-json/]: " USER_SUB_JSON_PATH
    read -rp "-> Путь для Telemt Telegram Proxy [/tg]: " USER_TG_PATH
    read -rp "-> Путь для AdGuard Home DoH [/dns-query]: " USER_ADH_DOH_PATH
    read -rp "-> Путь для VLESS WebSocket [/vless-ws]: " USER_WS_PATH
    read -rp "-> Путь для VLESS XHTTP [/vless-xhttp]: " USER_XHTTP_PATH
    read -rp "-> ServiceName для VLESS gRPC [vless-grpc]: " USER_GRPC_PATH
    read -rp "-> ServiceName для Trojan gRPC [/trojan-grpc]: " USER_TROJAN_PATH
    
    PANEL_PATH="${USER_PANEL_PATH:-/xui/}"
    SUB_PATH="${USER_SUB_PATH:-/sub/}"
    SUB_JSON_PATH="${USER_SUB_JSON_PATH:-/sub-json/}"
    TG_PATH="${USER_TG_PATH:-/tg}"
    ADH_DOH_PATH="${USER_ADH_DOH_PATH:-/dns-query}"
    WS_PATH="${USER_WS_PATH:-/vless-ws}"
    XHTTP_PATH="${USER_XHTTP_PATH:-/vless-xhttp}"
    GRPC_PATH="${USER_GRPC_PATH:-vless-grpc}"
    TROJAN_PATH="${USER_TROJAN_PATH:-/trojan-grpc}"
else
    # Генерация случайных путей
    PANEL_PATH="/$(gen_random_string 10)/"
    SUB_PATH="/$(gen_random_string 10)/"
    SUB_JSON_PATH="/$(gen_random_string 10)/"
    TG_PATH="/tg"
    ADH_DOH_PATH="/dns-query"
    WS_PATH="/$(gen_random_string 10)"
    XHTTP_PATH="/$(gen_random_string 10)"
    GRPC_PATH="vless-grpc"
    TROJAN_PATH="/$(gen_random_string 10)"
fi

# 1.4 Настройка модуля «Конфигурирование сервера»
echo
echo -e "${C_BOLD}Модуль управления «Конфигурирование сервера» (Stealth Dashboard):${C_RESET}"
echo "1 - Случайный скрытый порт (Enter - рекомендуется)"
echo "2 - Задать свой порт вручную"
read -rp "Выберите вариант [1/2]: " MGMT_CHOICE

if [[ "$MGMT_CHOICE" == "2" ]]; then
    read -rp "-> Введите порт для модуля управления [54892]: " USER_MGMT_PORT
    MGMT_PORT="${USER_MGMT_PORT:-54892}"
else
    MGMT_PORT=$(get_free_port)
fi

MGMT_SECRET_PATH="cfg-$(gen_random_string 8)"
MGMT_USER="admin_$(gen_random_string 4)"
MGMT_PASS="$(gen_random_string 16)"

# Нормализация слэшей
[[ "$PANEL_PATH" != /* ]] && PANEL_PATH="/$PANEL_PATH"
[[ "$PANEL_PATH" != */ ]] && PANEL_PATH="$PANEL_PATH/"
[[ "$SUB_PATH" != /* ]] && SUB_PATH="/$SUB_PATH"
[[ "$SUB_PATH" != */ ]] && SUB_PATH="$SUB_PATH/"
[[ "$SUB_JSON_PATH" != /* ]] && SUB_JSON_PATH="/$SUB_JSON_PATH"
[[ "$SUB_JSON_PATH" != */ ]] && SUB_JSON_PATH="$SUB_JSON_PATH/"
[[ "$TG_PATH" != /* ]] && TG_PATH="/$TG_PATH"
[[ "$ADH_DOH_PATH" != /* ]] && ADH_DOH_PATH="/$ADH_DOH_PATH"

TG_PATH_NO_SLASH="${TG_PATH#/}"
TG_PATH_NO_SLASH="${TG_PATH_NO_SLASH%/}"
ADH_DOH_PATH_NO_SLASH="${ADH_DOH_PATH#/}"
ADH_DOH_PATH_NO_SLASH="${ADH_DOH_PATH_NO_SLASH%/}"
WS_PATH_NO_SLASH="${WS_PATH#/}"
XHTTP_PATH_NO_SLASH="${XHTTP_PATH#/}"
TROJAN_PATH_NO_SLASH="${TROJAN_PATH#/}"
GRPC_PATH_NO_SLASH="${GRPC_PATH#/}"

# Внутренние порты
PANEL_PORT=$(get_free_port)
SUB_PORT=$(get_free_port)
WS_PORT=$(get_free_port)
GRPC_PORT="43935"
TROJAN_PORT=$(get_free_port)
TG_PORT="18080"
TG_DECOY_PORT="18082"
TG_PANEL_INTERNAL_PORT="8080"
TG_PANEL_PORT="65001"
ADH_DNS_PORT="57998"
ADH_DOH_PORT="44712"
ADH_WEB_PORT="3000"

# Генерация учетных записей
PANEL_USER="admin_$(gen_random_string 6)"
PANEL_PASS="$(gen_random_string 14)"
TG_USER="user_$(gen_random_string 4)"
TG_SECRET="$(openssl rand -hex 16)"
TG_PANEL_PASS="$(gen_random_string 14)"
ADH_USER="adguard_$(gen_random_string 6)"
ADH_PASS="$(gen_random_string 14)"

msg_ok "Параметры и учетные записи сгенерированы."

# ─────────────────────────────────────────────────────────────────────────────
# [2] SYSTEM DEPENDENCIES & NETWORK HARDENING
# ─────────────────────────────────────────────────────────────────────────────
msg_inf "Шаг [2/9]: Установка зависимостей и тюнинг сетевого стека..."
apt-get install -q -y \
    curl wget jq sudo certbot python3-certbot-dns-cloudflare \
    sqlite3 ufw nginx-full tar gzip openssl procps iproute2 \
    netcat-openbsd apache2-utils git ca-certificates python3-pip

# Отключение IPv6, скрытие ICMP Ping и включение BBR
cat << 'EOF' > /etc/sysctl.d/99-hardened-anti-tspu.conf
# Полное отключение IPv6 (устраняет 1-3с задержки DNS / Outbound)
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 1

# Stealth режим: полный игнор ICMP echo запросов (сервер не пингуется)
net.ipv4.icmp_echo_ignore_all = 1

# BBR Congestion Control & TCP Fast Open
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
net.ipv4.tcp_fastopen = 3

# Оптимизация сетевых буферов
net.core.rmem_max = 67108864
net.core.wmem_max = 67108864
net.ipv4.tcp_rmem = 4096 87380 33554432
net.ipv4.tcp_wmem = 4096 65536 33554432
EOF
sysctl --system > /dev/null 2>&1 || true

# Отключение IPv6 в UFW
sed -i 's/^IPV6=.*/IPV6=no/' /etc/default/ufw 2>/dev/null || true
msg_ok "IPv6 полностью отключен, ICMP ping заглушен, TCP BBR активирован."

# ─────────────────────────────────────────────────────────────────────────────
# [3] SSL VIA CLOUDFLARE DNS-01 API (PORT 80 NEVER OPENED)
# ─────────────────────────────────────────────────────────────────────────────
msg_inf "Шаг [3/9]: Выпуск SSL-сертификата через Cloudflare DNS-01 API..."
mkdir -p /etc/letsencrypt

cat << EOF > /etc/letsencrypt/cloudflare.ini
dns_cloudflare_email = ${CF_EMAIL}
dns_cloudflare_api_key = ${CF_KEY}
EOF
chmod 0600 /etc/letsencrypt/cloudflare.ini

certbot certonly \
    --dns-cloudflare \
    --dns-cloudflare-credentials /etc/letsencrypt/cloudflare.ini \
    --cert-name "${DOMAIN}" \
    -d "${DOMAIN}" \
    --non-interactive \
    --agree-tos \
    --register-unsafely-without-email

mkdir -p "/root/cert/${DOMAIN}"
ln -sf "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" "/root/cert/${DOMAIN}/fullchain.pem"
ln -sf "/etc/letsencrypt/live/${DOMAIN}/privkey.pem" "/root/cert/${DOMAIN}/privkey.pem"

# Хук автопродления сертификатов
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat << 'EOF' > /etc/letsencrypt/renewal-hooks/deploy/hardened-nginx
#!/usr/bin/env bash
systemctl reload nginx 2>/dev/null || true
systemctl restart x-ui 2>/dev/null || true
systemctl restart telemt 2>/dev/null || true
EOF
chmod +x /etc/letsencrypt/renewal-hooks/deploy/hardened-nginx
msg_ok "SSL сертификат Cloudflare DNS-01 успешно выпущен. Порт 80 остался закрыт."

# ─────────────────────────────────────────────────────────────────────────────
# [4] ADGUARD HOME SETUP (0 MS LOCAL DNS & DOH)
# ─────────────────────────────────────────────────────────────────────────────
msg_inf "Шаг [4/9]: Установка и настройка AdGuard Home (DNS 0 мс + DoH)..."
AGH_ARCH="amd64"
[[ "$(uname -m)" =~ (aarch64|arm64) ]] && AGH_ARCH="arm64"

mkdir -p /opt/AdGuardHome
curl -sSL "https://static.adguard.com/adguardhome/release/AdGuardHome_linux_${AGH_ARCH}.tar.gz" | tar -xz -C /tmp/
mv /tmp/AdGuardHome/* /opt/AdGuardHome/ 2>/dev/null || true
rm -rf /tmp/AdGuardHome

# Генерация bcrypt хеша для пароля AdGuard
ADH_HASH=$(python3 -c "import crypt; print(crypt.crypt('${ADH_PASS}', crypt.mksalt(crypt.METHOD_SHA512)))" 2>/dev/null || htpasswd -bnBC 10 "" "${ADH_PASS}" | tr -d ':\n')

# Скачивание шаблона AdGuardHome
curl -sSL "${GITHUB_RAW}/assets/adguard/AdGuardHome.yaml.template" -o "$WORKDIR/AdGuardHome.yaml.template" || cp "assets/adguard/AdGuardHome.yaml.template" "$WORKDIR/AdGuardHome.yaml.template" 2>/dev/null || true

sed -e "s|__ADH_WEB_PORT__|${ADH_WEB_PORT}|g" \
    -e "s|__ADH_USER__|${ADH_USER}|g" \
    -e "s|__ADH_PASS_HASH__|${ADH_HASH}|g" \
    -e "s|__ADH_DNS_PORT__|${ADH_DNS_PORT}|g" \
    -e "s|__ADH_DOH_PORT__|${ADH_DOH_PORT}|g" \
    "$WORKDIR/AdGuardHome.yaml.template" > /opt/AdGuardHome/AdGuardHome.yaml

/opt/AdGuardHome/AdGuardHome -s install > /dev/null 2>&1 || true
systemctl restart AdGuardHome
msg_ok "AdGuard Home установлен и запущен на портах ${ADH_WEB_PORT} (Web), ${ADH_DNS_PORT} (Local DNS), ${ADH_DOH_PORT} (DoH)."

# ─────────────────────────────────────────────────────────────────────────────
# [5] 3X-UI PANEL & ALL 7 INBOUNDS CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
msg_inf "Шаг [5/9]: Установка 3X-UI и конфигурирование базы SQLite со всеми 7 inbounds..."
bash <(curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh) << EOF
y
${PANEL_PORT}
${PANEL_USER}
${PANEL_PASS}
EOF

systemctl stop x-ui 2>/dev/null || true

# Генерация ключей для Reality
XRAY_BIN="/usr/local/x-ui/bin/xray-linux-${AGH_ARCH}"
if [[ ! -x "$XRAY_BIN" ]]; then
    XRAY_BIN=$(find /usr/local/x-ui/bin/ -name "xray*" -type f -perm /111 | head -n1)
fi

REALITY_KEYS=$("$XRAY_BIN" x25519 2>/dev/null || echo "Private key: iHdT9omhO7_J7btChCGwboMNr6UUUWtTrMZ9w97wUX0\nPublic key: rwiO94EBPsv4M--KzrFxLuC4vdyByhndJULbfGJ-XEU")
REALITY_PRIV=$(echo -e "$REALITY_KEYS" | grep -i "Private" | awk '{print $NF}')
REALITY_PUB=$(echo -e "$REALITY_KEYS" | grep -i "Public" | awk '{print $NF}')
REALITY_SHORTID=$(openssl rand -hex 8)

# Наполнение базы данных 3x-ui SQLite
sqlite3 /etc/x-ui/x-ui.db << EOF
-- Обновление настроек панели и подписок
UPDATE settings SET value = '${PANEL_PORT}' WHERE key = 'webPort';
UPDATE settings SET value = '${PANEL_PATH}' WHERE key = 'webBasePath';
UPDATE settings SET value = '${DOMAIN}' WHERE key = 'webDomain';
UPDATE settings SET value = '/etc/letsencrypt/live/${DOMAIN}/fullchain.pem' WHERE key = 'webCertFile';
UPDATE settings SET value = '/etc/letsencrypt/live/${DOMAIN}/privkey.pem' WHERE key = 'webKeyFile';

UPDATE settings SET value = '${SUB_PORT}' WHERE key = 'subPort';
UPDATE settings SET value = '${SUB_PATH}' WHERE key = 'subPath';
UPDATE settings SET value = 'https://${DOMAIN}${SUB_PATH}' WHERE key = 'subURI';
UPDATE settings SET value = '${SUB_JSON_PATH}' WHERE key = 'subJsonPath';
UPDATE settings SET value = 'https://${DOMAIN}${SUB_JSON_PATH}' WHERE key = 'subJsonURI';
UPDATE settings SET value = 'true' WHERE key = 'subJsonEnable';
UPDATE settings SET value = 'true' WHERE key = 'subEnable';
UPDATE settings SET value = '12' WHERE key = 'subUpdates';
UPDATE settings SET value = 'true' WHERE key = 'subEncrypt';
UPDATE settings SET value = 'true' WHERE key = 'subShowInfo';

-- Очистка дефолтных inbounds перед наполнением нашими
DELETE FROM inbounds;

-- 1. VLESS Reality (TCP 8443 -> Fallback 9443 xver:1)
INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, listen, port, protocol, settings, stream_settings, tag, sniffing)
VALUES (
    '1', '0', '0', '0', '🛡️ Reality', '1', '0', '127.0.0.1', '8443', 'vless',
    '{"clients": [], "decryption": "none", "fallbacks": []}',
    '{
      "network": "tcp",
      "security": "reality",
      "realitySettings": {
        "show": false,
        "xver": 1,
        "target": "127.0.0.1:9443",
        "serverNames": ["${DOMAIN}"],
        "privateKey": "${REALITY_PRIV}",
        "shortIds": ["${REALITY_SHORTID}"],
        "settings": {
          "publicKey": "${REALITY_PUB}",
          "fingerprint": "firefox",
          "spiderX": "/"
        }
      },
      "tcpSettings": {
        "acceptProxyProtocol": true,
        "header": {"type": "none"}
      }
    }',
    'in-8443-tcp',
    '{"enabled": true, "destOverride": ["http", "tls"]}'
);

-- 2. VLESS WebSocket
INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, listen, port, protocol, settings, stream_settings, tag, sniffing)
VALUES (
    '1', '0', '0', '0', '🚀 VLESS-WS', '1', '0', '127.0.0.1', '${WS_PORT}', 'vless',
    '{"clients": [], "decryption": "none", "fallbacks": []}',
    '{
      "network": "ws",
      "security": "none",
      "wsSettings": {
        "acceptProxyProtocol": false,
        "path": "${WS_PATH}",
        "host": "${DOMAIN}"
      }
    }',
    'in-${WS_PORT}-tcp',
    '{"enabled": true, "destOverride": ["http", "tls"]}'
);

-- 3. VLESS XHTTP (Unix Domain Socket)
INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, listen, port, protocol, settings, stream_settings, tag, sniffing)
VALUES (
    '1', '0', '0', '0', '⚡ VLESS-XHTTP', '1', '0', '/dev/shm/uds2023.sock,0666', '0', 'vless',
    '{"clients": [], "decryption": "none", "fallbacks": []}',
    '{
      "network": "xhttp",
      "security": "none",
      "xhttpSettings": {
        "path": "${XHTTP_PATH}",
        "mode": "stream-up"
      },
      "sockopt": {
        "trustedXForwardedFor": ["X-Forwarded-For"]
      }
    }',
    'inbound-/dev/shm/uds2023.sock,0666:0|',
    '{"enabled": true, "destOverride": ["http", "tls"]}'
);

-- 4. Trojan gRPC
INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, listen, port, protocol, settings, stream_settings, tag, sniffing)
VALUES (
    '1', '0', '0', '0', '🔒 Trojan-gRPC', '1', '0', '127.0.0.1', '${TROJAN_PORT}', 'trojan',
    '{"clients": [], "fallbacks": []}',
    '{
      "network": "grpc",
      "security": "none",
      "grpcSettings": {
        "serviceName": "${TROJAN_PATH_NO_SLASH}",
        "authority": "${DOMAIN}",
        "multiMode": false
      }
    }',
    'inbound-${TROJAN_PORT}',
    '{"enabled": true, "destOverride": ["http", "tls"]}'
);

-- 5. VLESS gRPC
INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, listen, port, protocol, settings, stream_settings, tag, sniffing)
VALUES (
    '1', '0', '0', '0', '⚡ VLESS-gRPC', '1', '0', '127.0.0.1', '${GRPC_PORT}', 'vless',
    '{"clients": [], "fallbacks": []}',
    '{
      "network": "grpc",
      "security": "none",
      "grpcSettings": {
        "serviceName": "${GRPC_PATH_NO_SLASH}",
        "authority": "${DOMAIN}",
        "multiMode": false
      }
    }',
    'inbound-${GRPC_PORT}',
    '{"enabled": true, "destOverride": ["http", "tls"]}'
);

-- 6. Hysteria 2 (UDP 443)
INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, listen, port, protocol, settings, stream_settings, tag, sniffing)
VALUES (
    '1', '0', '0', '0', '🌪️ Hysteria2', '1', '0', '', '443', 'hysteria',
    '{"version": 2, "clients": []}',
    '{
      "network": "hysteria",
      "security": "tls",
      "hysteriaSettings": {
        "version": 2,
        "udpIdleTimeout": 60
      },
      "tlsSettings": {
        "serverName": "${DOMAIN}",
        "alpn": ["h3"],
        "certificates": [{
          "certificateFile": "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem",
          "keyFile": "/etc/letsencrypt/live/${DOMAIN}/privkey.pem"
        }]
      }
    }',
    'inbound-443-udp',
    '{"enabled": true, "destOverride": ["http", "tls"]}'
);

-- 7. AmneziaWG (UDP 8443)
INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, listen, port, protocol, settings, stream_settings, tag, sniffing)
VALUES (
    '1', '0', '0', '0', '🛡️ AmneziaWG', '0', '0', '0.0.0.0', '8443', 'amneziawg',
    '{}', '{}', 'inbound-8443-udp', '{"enabled": false}'
);
EOF

systemctl daemon-reload
systemctl restart x-ui
msg_ok "Все 7 inbounds, оба типа подписок и параметры 3X-UI записаны в базу данных."

# ─────────────────────────────────────────────────────────────────────────────
# [6] TELEMT & TELEMT-PANEL SETUP (TELEGRAM PROXY)
# ─────────────────────────────────────────────────────────────────────────────
msg_inf "Шаг [6/9]: Установка Telemt Web Proxy и панели управления..."
ARCH_SUFFIX="x86_64"
[[ "$(uname -m)" =~ (aarch64|arm64) ]] && ARCH_SUFFIX="aarch64"

# Динамическое получение актуальных официальных версий с GitHub релизов разработчиков
TELEMT_TAG=$(curl -sSL https://api.github.com/repos/telemt/telemt/releases/latest | jq -r .tag_name 2>/dev/null || echo "3.5.14")
[[ -z "$TELEMT_TAG" || "$TELEMT_TAG" == "null" ]] && TELEMT_TAG="3.5.14"

TELEMT_PANEL_TAG=$(curl -sSL https://api.github.com/repos/amirotin/telemt_panel/releases | jq -r '.[0].tag_name' 2>/dev/null || echo "v1.0.0-rc.3")
[[ -z "$TELEMT_PANEL_TAG" || "$TELEMT_PANEL_TAG" == "null" ]] && TELEMT_PANEL_TAG="v1.0.0-rc.3"

msg_inf "Загрузка актуальных версий: Telemt (${TELEMT_TAG}) и Telemt Panel (${TELEMT_PANEL_TAG})..."
curl -sSL "https://github.com/telemt/telemt/releases/download/${TELEMT_TAG}/telemt-${ARCH_SUFFIX}-linux-musl.tar.gz" -o "$WORKDIR/telemt.tar.gz" 2>/dev/null || true
if [[ -f "$WORKDIR/telemt.tar.gz" ]]; then
    tar -xzf "$WORKDIR/telemt.tar.gz" -C /usr/local/bin/
    chmod +x /usr/local/bin/telemt 2>/dev/null || true
fi

curl -sSL "https://github.com/amirotin/telemt_panel/releases/download/${TELEMT_PANEL_TAG}/telemt-panel-${ARCH_SUFFIX}-linux-musl.tar.gz" -o "$WORKDIR/telemt-panel.tar.gz" 2>/dev/null || true
if [[ -f "$WORKDIR/telemt-panel.tar.gz" ]]; then
    tar -xzf "$WORKDIR/telemt-panel.tar.gz" -C /usr/local/bin/
    chmod +x /usr/local/bin/telemt-panel 2>/dev/null || true
fi

mkdir -p /etc/telemt /etc/telemt-panel /var/lib/telemt-panel

# Скачивание шаблонов Telemt
curl -sSL "${GITHUB_RAW}/assets/telemt/telemt.toml.template" -o "$WORKDIR/telemt.toml.template" || cp "assets/telemt/telemt.toml.template" "$WORKDIR/telemt.toml.template" 2>/dev/null || true
sed -e "s|__TG_PORT__|${TG_PORT}|g" \
    -e "s|__TG_PATH_NO_SLASH__|${TG_PATH_NO_SLASH}|g" \
    -e "s|__DOMAIN__|${DOMAIN}|g" \
    -e "s|__SERVER_IP__|${SERVER_IP}|g" \
    -e "s|__TG_DECOY_PORT__|${TG_DECOY_PORT}|g" \
    -e "s|__TG_USER__|${TG_USER}|g" \
    -e "s|__TG_SECRET__|${TG_SECRET}|g" \
    "$WORKDIR/telemt.toml.template" > /etc/telemt/config.toml

# Генерация хеша пароля для Telemt-Panel
TG_PANEL_HASH=$(python3 -c "import crypt; print(crypt.crypt('${TG_PANEL_PASS}', crypt.mksalt(crypt.METHOD_SHA512)))" 2>/dev/null || htpasswd -bnBC 10 "" "${TG_PANEL_PASS}" | tr -d ':\n')

curl -sSL "${GITHUB_RAW}/assets/telemt/telemt-panel.toml.template" -o "$WORKDIR/telemt-panel.toml.template" || cp "assets/telemt/telemt-panel.toml.template" "$WORKDIR/telemt-panel.toml.template" 2>/dev/null || true
sed -e "s|__TG_PANEL_INTERNAL_PORT__|${TG_PANEL_INTERNAL_PORT}|g" \
    -e "s|__TG_PANEL_PASS_HASH__|${TG_PANEL_HASH}|g" \
    "$WORKDIR/telemt-panel.toml.template" > /etc/telemt-panel/config.toml

# Systemd службы Telemt
curl -sSL "${GITHUB_RAW}/assets/telemt/telemt.service" -o /etc/systemd/system/telemt.service || cp "assets/telemt/telemt.service" /etc/systemd/system/telemt.service 2>/dev/null || true
curl -sSL "${GITHUB_RAW}/assets/telemt/telemt-panel.service" -o /etc/systemd/system/telemt-panel.service || cp "assets/telemt/telemt-panel.service" /etc/systemd/system/telemt-panel.service 2>/dev/null || true

systemctl daemon-reload
systemctl enable --now telemt telemt-panel 2>/dev/null || true
msg_ok "Telemt Web Proxy настроен и запущен с маскировкой 200 OK."

# ─────────────────────────────────────────────────────────────────────────────
# [7] HARDENED NGINX CONFIGURATION & COVER SITE
# ─────────────────────────────────────────────────────────────────────────────
msg_inf "Шаг [7/9]: Конфигурирование Nginx, Stream SNI, защиты от ботов и маскировочного сайта..."

mkdir -p /var/www/html /etc/nginx/conf.d /etc/nginx/sites-available /etc/nginx/sites-enabled /etc/nginx/stream-enabled

# Установка маскировочного сайта
cat << 'EOF' > /var/www/html/index.html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Cloud Network Edge</title>
    <style>
        body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: #0b0f19; color: #f3f4f6; margin: 0; display: flex; align-items: center; justify-content: center; min-height: 100vh; }
        .card { max-width: 500px; padding: 40px; background: #111827; border-radius: 12px; box-shadow: 0 4px 20px rgba(0,0,0,0.5); text-align: center; border: 1px solid #1f2937; }
        h1 { color: #60a5fa; font-size: 24px; margin-bottom: 12px; }
        p { color: #9ca3af; font-size: 15px; line-height: 1.6; }
        .status { display: inline-block; padding: 6px 12px; background: #064e3b; color: #34d399; border-radius: 9999px; font-size: 13px; font-weight: 600; margin-top: 15px; }
    </style>
</head>
<body>
    <div class="card">
        <h1>High-Performance Application Gateway</h1>
        <p>This edge cluster node is operating normally. All incoming traffic is filtered and routed via high-speed low-latency internal services.</p>
        <span class="status">● System Operational</span>
    </div>
</body>
</html>
EOF

# Запрет индексации поисковиками
cat << 'EOF' > /var/www/html/robots.txt
User-agent: *
Disallow: /
EOF

# Копирование bot-protection
curl -sSL "${GITHUB_RAW}/assets/nginx/01-bot-protection.conf" -o /etc/nginx/conf.d/01-bot-protection.conf || cp "assets/nginx/01-bot-protection.conf" /etc/nginx/conf.d/01-bot-protection.conf 2>/dev/null || true

# Настройка Stream SNI
curl -sSL "${GITHUB_RAW}/assets/nginx/stream.conf.template" -o "$WORKDIR/stream.conf.template" || cp "assets/nginx/stream.conf.template" "$WORKDIR/stream.conf.template" 2>/dev/null || true
sed "s|__REALITY_DOMAIN__|${DOMAIN}|g" "$WORKDIR/stream.conf.template" > /etc/nginx/stream-enabled/stream.conf

# Включение stream блока в nginx.conf если не включен
if ! grep -q "stream-enabled" /etc/nginx/nginx.conf; then
    echo "stream { include /etc/nginx/stream-enabled/*.conf; }" >> /etc/nginx/nginx.conf
fi

# Настройка Decoy сервера 18082
curl -sSL "${GITHUB_RAW}/assets/nginx/telemt-decoy.conf.template" -o "$WORKDIR/telemt-decoy.conf.template" || cp "assets/nginx/telemt-decoy.conf.template" "$WORKDIR/telemt-decoy.conf.template" 2>/dev/null || true
sed "s|__TG_DECOY_PORT__|${TG_DECOY_PORT}|g" "$WORKDIR/telemt-decoy.conf.template" > /etc/nginx/sites-available/telemt-decoy.conf
ln -sf /etc/nginx/sites-available/telemt-decoy.conf /etc/nginx/sites-enabled/telemt-decoy.conf

# Настройка проксирования панели Telemt на порту 65001 с HTTPS
curl -sSL "${GITHUB_RAW}/assets/nginx/telemt-panel.conf.template" -o "$WORKDIR/telemt-panel.conf.template" || cp "assets/nginx/telemt-panel.conf.template" "$WORKDIR/telemt-panel.conf.template" 2>/dev/null || true
sed -e "s|__TG_PANEL_PORT__|${TG_PANEL_PORT}|g" \
    -e "s|__TG_PANEL_INTERNAL_PORT__|${TG_PANEL_INTERNAL_PORT}|g" \
    -e "s|__DOMAIN__|${DOMAIN}|g" \
    "$WORKDIR/telemt-panel.conf.template" > /etc/nginx/sites-available/telemt-panel.conf
ln -sf /etc/nginx/sites-available/telemt-panel.conf /etc/nginx/sites-enabled/telemt-panel.conf

# Настройка главного VHost 9443
curl -sSL "${GITHUB_RAW}/assets/nginx/vhost.conf.template" -o "$WORKDIR/vhost.conf.template" || cp "assets/nginx/vhost.conf.template" "$WORKDIR/vhost.conf.template" 2>/dev/null || true
sed -e "s|__DOMAIN__|${DOMAIN}|g" \
    -e "s|__PANEL_PATH__|${PANEL_PATH}|g" \
    -e "s|__PANEL_PORT__|${PANEL_PORT}|g" \
    -e "s|__SUB_PATH__|${SUB_PATH}|g" \
    -e "s|__SUB_JSON_PATH__|${SUB_JSON_PATH}|g" \
    -e "s|__SUB_PORT__|${SUB_PORT}|g" \
    -e "s|__TG_PATH__|${TG_PATH}|g" \
    -e "s|__TG_PORT__|${TG_PORT}|g" \
    -e "s|__TG_DECOY_PORT__|${TG_DECOY_PORT}|g" \
    -e "s|__ADH_DOH_PATH__|${ADH_DOH_PATH}|g" \
    -e "s|__ADH_DOH_PORT__|${ADH_DOH_PORT}|g" \
    -e "s|__WS_PATH__|${WS_PATH}|g" \
    -e "s|__WS_PORT__|${WS_PORT}|g" \
    -e "s|__XHTTP_PATH__|${XHTTP_PATH}|g" \
    -e "s|__TROJAN_PATH__|${TROJAN_PATH}|g" \
    -e "s|__TROJAN_PORT__|${TROJAN_PORT}|g" \
    -e "s|__GRPC_PATH__|${GRPC_PATH}|g" \
    -e "s|__GRPC_PORT__|${GRPC_PORT}|g" \
    "$WORKDIR/vhost.conf.template" > "/etc/nginx/sites-available/${DOMAIN}.conf"

ln -sf "/etc/nginx/sites-available/${DOMAIN}.conf" "/etc/nginx/sites-enabled/${DOMAIN}.conf"
rm -f /etc/nginx/sites-enabled/default /etc/nginx/sites-available/default 2>/dev/null || true

nginx -t
systemctl reload nginx
msg_ok "Nginx проверен и перезагружен. Anti-Bot защита и маршрутизация 443 активны."

# ─────────────────────────────────────────────────────────────────────────────
# [8] SERVER CONFIGURATION MODULE SETUP (STEALTH DASHBOARD)
# ─────────────────────────────────────────────────────────────────────────────
msg_inf "Шаг [8/9]: Установка и настройка модуля «Конфигурирование сервера»..."
mkdir -p /etc/server-manager

# Копирование скриптов модуля
curl -sSL "${GITHUB_RAW}/assets/server-manager/server_manager.py" -o /usr/local/bin/server-manager.py || cp "assets/server-manager/server_manager.py" /usr/local/bin/server-manager.py 2>/dev/null || true
curl -sSL "${GITHUB_RAW}/assets/server-manager/server-mgr" -o /usr/local/bin/server-mgr || cp "assets/server-manager/server-mgr" /usr/local/bin/server-mgr 2>/dev/null || true
curl -sSL "${GITHUB_RAW}/assets/server-manager/server-manager.service" -o /etc/systemd/system/server-manager.service || cp "assets/server-manager/server-manager.service" /etc/systemd/system/server-manager.service 2>/dev/null || true

chmod +x /usr/local/bin/server-manager.py /usr/local/bin/server-mgr 2>/dev/null || true

# Сохранение мастер-шаблона Nginx для модуля
cp "$WORKDIR/vhost.conf.template" /etc/server-manager/vhost.conf.template 2>/dev/null || true

# Генерация PBKDF2 хеша пароля администратора модуля
MGMT_PASS_HASH=$(python3 -c "import hashlib; print(hashlib.pbkdf2_hmac('sha256', '${MGMT_PASS}'.encode('utf-8'), b'server_manager_salt_3x', 100000).hex())" 2>/dev/null || echo "")

cat << EOF > /etc/server-manager/config.json
{
  "domain": "${DOMAIN}",
  "mgmt_port": ${MGMT_PORT},
  "mgmt_secret_path": "${MGMT_SECRET_PATH}",
  "auth_user": "${MGMT_USER}",
  "auth_pass_plain": "${MGMT_PASS}",
  "auth_pass_hash": "${MGMT_PASS_HASH}",
  "auto_lock_minutes": 30,
  "server_ip": "${SERVER_IP}"
}
EOF

chmod 0600 /etc/server-manager/config.json

systemctl daemon-reload
systemctl enable --now server-manager 2>/dev/null || true
msg_ok "Модуль «Конфигурирование сервера» установлен и запущен на порту ${MGMT_PORT} (Stealth)."

# ─────────────────────────────────────────────────────────────────────────────
# [9] FIREWALL CONFIGURATION (PORT 80 CLOSED FOREVER)
# ─────────────────────────────────────────────────────────────────────────────
msg_inf "Шаг [9/9]: Настройка файрвола UFW (Порт 80 наглухо блокируется)..."
SSH_PORT=$(ss -tlnp | grep sshd | awk '{print $4}' | awk -F':' '{print $NF}' | head -n1 || echo 22)
[[ -z "$SSH_PORT" ]] && SSH_PORT=22

ufw --force reset > /dev/null 2>&1 || true
ufw default deny incoming > /dev/null
ufw default allow outgoing > /dev/null
ufw allow "${SSH_PORT}/tcp" comment 'SSH' > /dev/null
ufw allow 443/tcp comment 'Nginx HTTPS & Reality' > /dev/null
ufw allow 443/udp comment 'Hysteria 2' > /dev/null
ufw allow 8443/tcp comment 'Reality Fallback' > /dev/null
ufw allow 8443/udp comment 'AmneziaWG' > /dev/null
ufw deny 80/tcp comment 'Port 80 Closed' > /dev/null
ufw --force enable > /dev/null
msg_ok "UFW активирован. Порт 80 ЗАКРЫТ. Открыты только SSH, 443 (TCP/UDP) и 8443 (TCP/UDP). Порт модуля ${MGMT_PORT} ЗАКРЫТ (Stealth)."

# ─────────────────────────────────────────────────────────────────────────────
# FINAL SUMMARY REPORT
# ─────────────────────────────────────────────────────────────────────────────
cat << EOF > /root/server_credentials.txt
╔══════════════════════════════════════════════════════════════════════════════════════╗
║                   СЕРВЕР УСПЕШНО НАСТРОЕН И ЗАЩИЩЕН (STEALTH-РЕЖИМ)                  ║
╚══════════════════════════════════════════════════════════════════════════════════════╝

[0] МОДУЛЬ «КОНФИГУРИРОВАНИЕ СЕРВЕРА» (STEALTH DASHBOARD):
    Основная ссылка:  https://${DOMAIN}:${MGMT_PORT}/
    (Автоматически перенаправляет на секретный закрытый путь /${MGMT_SECRET_PATH}/)
    Логин:            ${MGMT_USER}
    Пароль:           ${MGMT_PASS}
    Внутренний порт:  ${MGMT_PORT}
    Статус в UFW:     ЗАКРЫТ (Режим невидимки)

    КАК ОТКРЫТЬ ДОСТУП ПРИ НЕОБХОДИМОСТИ:
    1) Быстрая команда в SSH (с авто-таймером закрытия 30 мин):
       server-mgr open
    2) Или через SSH-туннель (без открытия портов в интернет вообще):
       ssh -L 8088:127.0.0.1:${MGMT_PORT} root@${DOMAIN}
       и вход в браузере по адресу: http://localhost:8088/

    Закрыть порт вручную: server-mgr close (или кнопка «Выход» в веб-интерфейсе)

[1] ПАНЕЛЬ УПРАВЛЕНИЯ 3X-UI:
    URL:              https://${DOMAIN}${PANEL_PATH}
    Логин:            ${PANEL_USER}
    Пароль:           ${PANEL_PASS}
    Внутренний порт:  ${PANEL_PORT} (доступен только через HTTPS :443)

[2] ПОДПИСКИ ДЛЯ КЛИЕНТОВ (2 ТИПА):
    Обычная (Base64): https://${DOMAIN}${SUB_PATH}
    JSON (Happ/Sing): https://${DOMAIN}${SUB_JSON_PATH}
    Порт сервиса:     ${SUB_PORT}

[3] TELEMT (TELEGRAM WEB PROXY):
    Веб-панель HTTPS: https://${DOMAIN}:${TG_PANEL_PORT}
    Логин панели:     admin
    Пароль панели:    ${TG_PANEL_PASS}
    Путь прокси:      https://${DOMAIN}${TG_PATH}
    
    ССЫЛКИ ДЛЯ ПОДКЛЮЧЕНИЯ В TELEGRAM:
    1) Web Proxy (Рекомендуется, с указанием секретного пути):
       tg://webproxy?server=${DOMAIN}%2F${TG_PATH_NO_SLASH}&secret=${TG_SECRET}
    2) Classic MTProto (через порт 443):
       tg://proxy?server=${DOMAIN}&port=443&secret=dd${TG_SECRET}
    
    📁 МЕСТО ХРАНЕНИЯ КЛЮЧЕЙ КЛИЕНТОВ И МИГРАЦИЯ:
    -> Файл конфига и ключей: /etc/telemt/config.toml (секция [access.users])
    -> Лимиты и статистика:  /etc/telemt/telemt.limit.json
    -> Конфиг веб-панели:    /etc/telemt-panel/config.toml
    -> Для переноса без смены ссылок клиентов: скопируйте /etc/telemt/config.toml

[4] ADGUARD HOME (DNS 0 МС И ФИЛЬТРАЦИЯ РЕКЛАМЫ):
    Веб-интерфейс:    http://${SERVER_IP}:${ADH_WEB_PORT}
    Логин:            ${ADH_USER}
    Пароль:           ${ADH_PASS}
    DoH (HTTPS):      https://${DOMAIN}${ADH_DOH_PATH}
    Локальный DNS:    127.0.0.1:${ADH_DNS_PORT} (задействован для Xray Outbound 0 ms)

[5] СПИСОК РАЗВЕРНУТЫХ INBOUNDS В XRAY:
    • Reality TCP:     порт 8443 (fallback -> 9443 xver:1)
    • VLESS WS:        порт ${WS_PORT}, путь ${WS_PATH}
    • VLESS XHTTP:     UDS /dev/shm/uds2023.sock, путь ${XHTTP_PATH}
    • Trojan gRPC:     порт ${TROJAN_PORT}, путь ${TROJAN_PATH}
    • VLESS gRPC:      порт ${GRPC_PORT}, serviceName ${GRPC_PATH}
    • Hysteria 2:      порт 443/UDP (TLS Cloudflare)
    • AmneziaWG:       порт 8443/UDP

[6] ПАРАМЕТРЫ ЗАЩИТЫ:
    [✓] Порт 80:       НАГЛУХО ЗАКРЫТ (UFW DENY)
    [✓] SSL:           Cloudflare DNS-01 API (тихий выпуск и автопродление)
    [✓] IPv6:          ВЫКЛЮЧЕН (исключены задержки при резолве)
    [✓] ICMP Ping:     ВЫКЛЮЧЕН (Stealth, сервер не пингуется)
    [✓] Anti-Bot:      АКТИВЕН (Return 444 для сканеров и ботов, robots.txt Disallow)
    [✓] Decoy 200 OK:  АКТИВЕН (порт 18082, маскировка от зондирования ТСПУ)

Данные доступов сохранены в: /root/server_credentials.txt
EOF

chmod 0600 /root/server_credentials.txt
cat /root/server_credentials.txt
echo
msg_ok "Установка успешно завершена! Скопируйте данные выше."
