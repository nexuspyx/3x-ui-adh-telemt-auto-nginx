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

# Helper for interactive prompts (safely reads from /dev/tty if stdin is piped via curl ... | bash)
read_prompt() {
    local prompt_msg="$1"
    local var_name="$2"
    local is_secret="${3:-0}"
    local input_val=""
    if [[ ! -t 0 ]] && [[ -r /dev/tty ]]; then
        if [[ "$is_secret" -eq 1 ]]; then
            read -rsp "$prompt_msg" input_val < /dev/tty || true
            echo
        else
            read -rp "$prompt_msg" input_val < /dev/tty || true
        fi
    else
        if [[ "$is_secret" -eq 1 ]]; then
            read -rsp "$prompt_msg" input_val 2>/dev/null || true
            echo
        else
            read -rp "$prompt_msg" input_val 2>/dev/null || true
        fi
    fi
    printf -v "$var_name" '%s' "$input_val"
}

# Clear screen only when terminal is capable
[[ -t 1 ]] && clear 2>/dev/null || true
echo -e "${C_CYAN}"
cat << 'EOF'
╔══════════════════════════════════════════════════════════════════════════════════╗
║        3X-UI + TELEMT + HARDENED NGINX + ADGUARD HOME (STEALTH EDITION)          ║
║                  Автоматическая установка и полная защита от ТСПУ                ║
╚══════════════════════════════════════════════════════════════════════════════════╝
EOF
echo -e "${C_RESET}"

# ─────────────────────────────────────────────────────────────────────────────
# [0] OS CHECK
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

# ─────────────────────────────────────────────────────────────────────────────
# [1] INTERACTIVE WIZARD
# ─────────────────────────────────────────────────────────────────────────────
echo
echo -e "${C_BOLD}=== ШАГ [1/9]: ИНТЕРАКТИВНЫЙ МАСТЕР НАСТРОЙКИ ===${C_RESET}"

# 1.1 Домен
if [[ -z "$DOMAIN" ]]; then
    while true; do
        read_prompt "-> Введите ваш домен (например, vps.example.com): " DOMAIN
        DOMAIN=$(echo "$DOMAIN" | tr -d '[:space:]')
        [[ -n "$DOMAIN" ]] && break
        msg_warn "Домен не может быть пустым!"
    done
else
    DOMAIN=$(echo "$DOMAIN" | tr -d '[:space:]')
    msg_ok "Используется домен из окружения: ${DOMAIN}"
fi

# 1.2 Cloudflare учетные данные
echo
msg_inf "Настройка тихого выпуска SSL через Cloudflare DNS-01 (порт 80 будет закрыт навсегда):"
if [[ -z "$CF_EMAIL" && -z "$CF_KEY" ]]; then
    while true; do
        read_prompt "-> Введите Cloudflare Email (или оставьте пустым, если используете API Token): " CF_EMAIL
        CF_EMAIL=$(echo "$CF_EMAIL" | tr -d '[:space:]')
        break
    done

    while true; do
        read_prompt "-> Введите Cloudflare Global API Key (или API Token): " CF_KEY 1
        CF_KEY=$(echo "$CF_KEY" | tr -d '[:space:]')
        [[ -n "$CF_KEY" ]] && break
        msg_warn "Ключ/токен не может быть пустым!"
    done
else
    CF_EMAIL=$(echo "$CF_EMAIL" | tr -d '[:space:]')
    CF_KEY=$(echo "$CF_KEY" | tr -d '[:space:]')
    msg_ok "Используются учетные данные Cloudflare из окружения."
fi

# 1.3 Кастомизация путей и портов
if [[ -z "$PATH_CHOICE" ]]; then
    echo
    echo -e "${C_BOLD}Кастомизация путей (Locations) и внутренних портов:${C_RESET}"
    echo "1 - Настроить вручную свои пути и порты"
    echo "2 - Использовать случайные скрытые пути и порты (Enter - рекомендуется)"
    read_prompt "Выберите вариант [1/2]: " PATH_CHOICE
    PATH_CHOICE="${PATH_CHOICE:-2}"
fi

if [[ "$PATH_CHOICE" == "1" ]]; then
    echo
    read_prompt "-> Путь для панели 3x-ui [/xui/]: " USER_PANEL_PATH
    read_prompt "-> Путь для обычной подписки Base64 [/sub/]: " USER_SUB_PATH
    read_prompt "-> Путь для JSON подписки Happ/Sing-box [/sub-json/]: " USER_SUB_JSON_PATH
    read_prompt "-> Путь для Telemt Telegram Proxy [/tg]: " USER_TG_PATH
    read_prompt "-> Путь для AdGuard Home DoH [/dns-query]: " USER_ADH_DOH_PATH
    read_prompt "-> Путь для VLESS WebSocket [/vless-ws]: " USER_WS_PATH
    read_prompt "-> Путь для VLESS XHTTP [/vless-xhttp]: " USER_XHTTP_PATH
    read_prompt "-> ServiceName для VLESS gRPC [vless-grpc]: " USER_GRPC_PATH
    read_prompt "-> ServiceName для Trojan gRPC [/trojan-grpc]: " USER_TROJAN_PATH
    
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
if [[ -z "$MGMT_CHOICE" ]]; then
    echo
    echo -e "${C_BOLD}Модуль управления «Конфигурирование сервера» (Stealth Dashboard):${C_RESET}"
    echo "1 - Случайный скрытый порт (Enter - рекомендуется)"
    echo "2 - Задать свой порт вручную"
    read_prompt "Выберите вариант [1/2]: " MGMT_CHOICE
    MGMT_CHOICE="${MGMT_CHOICE:-1}"
fi

if [[ "$MGMT_CHOICE" == "2" ]]; then
    read_prompt "-> Введите порт для модуля управления [54892]: " USER_MGMT_PORT
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

WS_PATH_LEADING="/${WS_PATH_NO_SLASH}"
XHTTP_PATH_LEADING="/${XHTTP_PATH_NO_SLASH}"
TROJAN_PATH_LEADING="/${TROJAN_PATH_NO_SLASH}"
GRPC_PATH_LEADING="/${GRPC_PATH_NO_SLASH}"

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
export DEBIAN_FRONTEND=noninteractive
apt-get update -q -y
apt-get install -q -y \
    curl wget jq sudo certbot python3-certbot-dns-cloudflare \
    sqlite3 ufw fail2ban python3-systemd nftables nginx-full \
    tar gzip openssl procps iproute2 netcat-openbsd apache2-utils \
    git ca-certificates python3-pip

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

if [[ -n "$CF_EMAIL" ]]; then
    cat << EOF > /etc/letsencrypt/cloudflare.ini
dns_cloudflare_email = ${CF_EMAIL}
dns_cloudflare_api_key = ${CF_KEY}
EOF
else
    cat << EOF > /etc/letsencrypt/cloudflare.ini
dns_cloudflare_api_token = ${CF_KEY}
EOF
fi
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
ADH_HASH=$(htpasswd -bnBC 10 "" "${ADH_PASS}" 2>/dev/null | tr -d ':\n')
[[ -z "$ADH_HASH" ]] && ADH_HASH=$(python3 -c "import crypt; print(crypt.crypt('${ADH_PASS}', crypt.mksalt(crypt.METHOD_SHA512)))" 2>/dev/null || true)

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
export XUI_NONINTERACTIVE=1
export XUI_USERNAME="${PANEL_USER}"
export XUI_PASSWORD="${PANEL_PASS}"
export XUI_PANEL_PORT="${PANEL_PORT}"
export XUI_WEB_BASE_PATH="${PANEL_PATH}"
export XUI_SSL_MODE="none"
export XUI_DB_TYPE="sqlite"
export XUI_ENABLE_FAIL2BAN="false"

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
        "path": "${WS_PATH_LEADING}",
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
        "path": "${XHTTP_PATH_LEADING}",
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

-- 8. Заполнение таблицы hosts для внешних ссылок подписок (порт 443, TLS)
DELETE FROM hosts;
INSERT INTO hosts (inbound_id, group_id, sort_order, remark, address, port, security, fingerprint, alpn)
VALUES
    ((SELECT id FROM inbounds WHERE tag='in-8443-tcp'), '$(openssl rand -hex 8)', 0, 'reality', '${DOMAIN}', 443, 'same', '', '[]'),
    ((SELECT id FROM inbounds WHERE tag='in-${WS_PORT}-tcp'), '$(openssl rand -hex 8)', 0, 'ws', '${DOMAIN}', 443, 'tls', 'firefox', '["http/1.1"]'),
    ((SELECT id FROM inbounds WHERE tag='inbound-/dev/shm/uds2023.sock,0666:0|'), '$(openssl rand -hex 8)', 0, 'xhttp', '${DOMAIN}', 443, 'tls', 'firefox', '["h2","http/1.1"]'),
    ((SELECT id FROM inbounds WHERE tag='inbound-${TROJAN_PORT}'), '$(openssl rand -hex 8)', 0, 'trojan', '${DOMAIN}', 443, 'tls', 'firefox', '["h2","http/1.1"]'),
    ((SELECT id FROM inbounds WHERE tag='inbound-${GRPC_PORT}'), '$(openssl rand -hex 8)', 0, 'vless-grpc', '${DOMAIN}', 443, 'tls', 'firefox', '["h2","http/1.1"]'),
    ((SELECT id FROM inbounds WHERE tag='inbound-443-udp'), '$(openssl rand -hex 8)', 0, 'hysteria2', '${DOMAIN}', 443, 'same', '', '[]');
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
TG_PANEL_HASH=$(htpasswd -bnBC 10 "" "${TG_PANEL_PASS}" 2>/dev/null | tr -d ':\n')
[[ -z "$TG_PANEL_HASH" ]] && TG_PANEL_HASH=$(python3 -c "import crypt; print(crypt.crypt('${TG_PANEL_PASS}', crypt.mksalt(crypt.METHOD_SHA512)))" 2>/dev/null || true)

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

# Автоматическая настройка и загрузка базы GeoIP для Telemt Panel
msg_inf "Подключение и загрузка базы GeoIP (MaxMind GeoLite2-City) в Telemt Panel..."
for _ in {1..15}; do
    if curl -s "http://127.0.0.1:${TG_PANEL_INTERNAL_PORT}/api/auth/methods" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

TG_COOKIE=$(curl -s -i -X POST \
    -H "Origin: http://127.0.0.1:${TG_PANEL_INTERNAL_PORT}" \
    -H "Content-Type: application/json" \
    -d "{\"username\":\"admin\",\"password\":\"${TG_PANEL_PASS}\"}" \
    "http://127.0.0.1:${TG_PANEL_INTERNAL_PORT}/api/auth/login" 2>/dev/null | grep -i "set-cookie:" | tr -d '\r' | sed -E 's/.*panel_session=([^;]+).*/\1/')

if [[ -n "$TG_COOKIE" ]]; then
    curl -s -X PUT \
        -H "Origin: http://127.0.0.1:${TG_PANEL_INTERNAL_PORT}" \
        -H "Cookie: panel_session=${TG_COOKIE}" \
        -H "Content-Type: application/json" \
        -d '{"enabled":true,"source":"urls","schedule":"weekly","country":{"enabled":false,"location":""},"asn":{"enabled":false,"location":""},"city":{"enabled":true,"location":"https://raw.githubusercontent.com/P3TERX/GeoLite.mmdb/download/GeoLite2-City.mmdb"}}' \
        "http://127.0.0.1:${TG_PANEL_INTERNAL_PORT}/api/settings/geoip" >/dev/null 2>&1 || true
    msg_ok "База GeoIP (GeoLite2-City) успешно подключена и настроена на еженедельное обновление."
else
    msg_warn "Не удалось авторизоваться в Telemt Panel для автоматического включения GeoIP."
fi

# ─────────────────────────────────────────────────────────────────────────────
# [7] HARDENED NGINX CONFIGURATION & PLACEHOLDER SITE
# ─────────────────────────────────────────────────────────────────────────────
msg_inf "Шаг [7/9]: Конфигурирование Nginx, Stream SNI, защиты от ботов и страницы-заглушки..."

mkdir -p /var/www/html /etc/nginx/conf.d /etc/nginx/sites-available /etc/nginx/sites-enabled /etc/nginx/stream-enabled

# Установка страницы-заглушки
cat << 'EOF' > /var/www/html/index.html
<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="robots" content="noindex, nofollow">
    <title>Сайт работает / Web Server is working</title>
    <style>
        :root {
            color-scheme: dark;
        }
        * {
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }
        body {
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
            background-color: #0b0f19;
            color: #f3f4f6;
            display: flex;
            align-items: center;
            justify-content: center;
            min-height: 100vh;
            padding: 24px;
        }
        .container {
            max-width: 540px;
            width: 100%;
            background: #111827;
            border: 1px solid #1f2937;
            border-radius: 16px;
            padding: 36px;
            box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
            text-align: center;
        }
        .status-badge {
            display: inline-flex;
            align-items: center;
            gap: 8px;
            background: rgba(16, 185, 129, 0.12);
            color: #10b981;
            padding: 6px 14px;
            border-radius: 9999px;
            font-size: 13px;
            font-weight: 600;
            margin-bottom: 20px;
            border: 1px solid rgba(16, 185, 129, 0.25);
        }
        .status-dot {
            width: 8px;
            height: 8px;
            background: #10b981;
            border-radius: 50%;
            box-shadow: 0 0 8px #10b981;
        }
        h1 {
            color: #f9fafb;
            font-size: 24px;
            font-weight: 700;
            margin-bottom: 12px;
        }
        p {
            color: #9ca3af;
            font-size: 15px;
            line-height: 1.6;
            margin-bottom: 24px;
        }
        .info-card {
            background: #0f172a;
            border: 1px dashed #374151;
            border-radius: 10px;
            padding: 16px 20px;
            text-align: left;
            margin-bottom: 20px;
        }
        .info-label {
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #6b7280;
            margin-bottom: 6px;
            font-weight: 600;
        }
        .info-code {
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            font-size: 14px;
            color: #38bdf8;
            word-break: break-all;
        }
        .footer-note {
            font-size: 13px;
            color: #6b7280;
            line-height: 1.5;
        }
        .footer-note code {
            font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
            background: #1f2937;
            padding: 2px 6px;
            border-radius: 4px;
            color: #e5e7eb;
            font-size: 12px;
        }
    </style>
</head>
<body>
    <div class="container">
        <div class="status-badge">
            <span class="status-dot"></span> Веб-сервер работает &bull; HTTP 200 OK
        </div>
        <h1>Сайт успешно настроен</h1>
        <p>Это стандартная страница-заглушка. Веб-сервер активен и готов отдавать контент вашего сайта.</p>
        <div class="info-card">
            <div class="info-label">Директория для загрузки сайта на сервере:</div>
            <div class="info-code">/var/www/html/</div>
        </div>
        <div class="footer-note">
            Чтобы опубликовать ваш сайт, загрузите его файлы в указанный каталог, заменив <code>index.html</code>.
        </div>
    </div>
</body>
</html>
EOF

# Запрет индексации поисковиками
cat << 'EOF' > /var/www/html/robots.txt
User-agent: *
Disallow: /
EOF

chown -R www-data:www-data /var/www/html 2>/dev/null || true
chmod 644 /var/www/html/index.html /var/www/html/robots.txt 2>/dev/null || true
msg_ok "Страница-заглушка установлена в /var/www/html/"

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
    -e "s|__WS_PATH__|${WS_PATH_LEADING}|g" \
    -e "s|__WS_PORT__|${WS_PORT}|g" \
    -e "s|__XHTTP_PATH__|${XHTTP_PATH_LEADING}|g" \
    -e "s|__TROJAN_PATH__|${TROJAN_PATH_LEADING}|g" \
    -e "s|__TROJAN_PORT__|${TROJAN_PORT}|g" \
    -e "s|__GRPC_PATH__|${GRPC_PATH_LEADING}|g" \
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
# [9] FIREWALL & BRUTE-FORCE PROTECTION (UFW & FAIL2BAN)
# ─────────────────────────────────────────────────────────────────────────────
msg_inf "Шаг [9/9]: Настройка файрвола UFW и защиты от брутфорса Fail2ban..."
SSH_PORT=$(ss -tlnp | grep sshd | awk '{print $4}' | awk -F':' '{print $NF}' | head -n1 || echo 22)
[[ -z "$SSH_PORT" ]] && SSH_PORT=22

# 9.1 Настройка UFW: открытыми остаются ТОЛЬКО порты 22 и 443
ufw --force reset > /dev/null 2>&1 || true
ufw default deny incoming > /dev/null
ufw default allow outgoing > /dev/null
ufw allow "${SSH_PORT}/tcp" comment 'SSH' > /dev/null
ufw allow 443/tcp comment 'Nginx HTTPS & Reality' > /dev/null
ufw allow 443/udp comment 'Hysteria 2 / QUIC' > /dev/null
ufw deny 80/tcp comment 'Port 80 Closed' > /dev/null
ufw --force enable > /dev/null
msg_ok "UFW активирован. Порт 80 ЗАКРЫТ. Открыты ТОЛЬКО порты ${SSH_PORT}/tcp (SSH) и 443 (TCP/UDP). Все остальные порты закрыты."

# 9.2 Настройка Fail2ban: прогрессивный бан и полная блокировка всех портов через nftables
mkdir -p /etc/fail2ban/jail.d

F2B_BACKEND="systemd"
journalctl -n 1 --no-pager >/dev/null 2>&1 || F2B_BACKEND="auto"

cat << EOF > /etc/fail2ban/jail.d/90-ssh-hardening.local
# Managed by 3x-ui Setup: Enhanced SSH Protection & Allports Ban
[DEFAULT]
# Блокировка нарушителя на уровне всех портов сервера через nftables
banaction = nftables[type=allports]
banaction_allports = nftables[type=allports]

# Прогрессивный бан: увеличение срока блокировки при повторных атаках
bantime.increment = true
bantime.rndtime = 1200
bantime.maxtime = 5w
bantime.factor = 2

# Белый список локальных адресов
ignoreip = 127.0.0.1/8 ::1

[sshd]
enabled = true
port = ${SSH_PORT}
backend = ${F2B_BACKEND}
maxretry = 5
findtime = 1800
bantime = 3600
EOF

systemctl daemon-reload > /dev/null 2>&1 || true
systemctl enable fail2ban > /dev/null 2>&1 || true
systemctl restart fail2ban > /dev/null 2>&1 || true
msg_ok "Fail2ban установлен и настроен: прогрессивный бан активен, нарушители блокируются на уровне всех портов сервера."

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
    База GeoIP:       АКТИВНА (GeoLite2-City, еженедельное автообновление)
    
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
    [✓] Файрвол UFW:   АКТИВЕН И НАСТРОЕН (Открыты только порты ${SSH_PORT} SSH и 443 TCP/UDP, все остальные порты закрыты)
    [✓] Fail2ban:      УСТАНОВЛЕН И НАСТРОЕН (SSH защита, прогрессивный бан, блокировка по ВСЕМ портам через nftables)
    [✓] Порт 80:       НАГЛУХО ЗАКРЫТ (UFW DENY)
    [✓] SSL:           Cloudflare DNS-01 API (тихий выпуск и автопродление)
    [✓] IPv6:          ВЫКЛЮЧЕН (исключены задержки при резолве)
    [✓] ICMP Ping:     ВЫКЛЮЧЕН (Stealth, сервер не пингуется)
    [✓] Anti-Bot:      АКТИВЕН (Return 444 для сканеров и ботов, robots.txt Disallow)
    [✓] Decoy 200 OK:  АКТИВЕН (порт 18082, маскировка от зондирования ТСПУ)

[7] ВАШ САЙТ (ЗАГЛУШКА И КАТАЛОГ ДЛЯ ЗАГРУЗКИ):
    URL сайта:         https://${DOMAIN}/
    Каталог на сервере: /var/www/html/
    Файл главной:      /var/www/html/index.html
    Инструкция:        По адресу https://${DOMAIN}/ сейчас отображается
                       стандартная страница-заглушка (HTTP 200 OK).
                       Чтобы загрузить свой сайт, скопируйте его файлы
                       (HTML, CSS, JS, медиа) в каталог:
                       /var/www/html/
                       (заменив стандартный файл index.html).

Данные доступов сохранены в: /root/server_credentials.txt
EOF

chmod 0600 /root/server_credentials.txt
cat /root/server_credentials.txt
echo
msg_ok "Установка успешно завершена! Скопируйте данные выше."
msg_inf "Каталог на сервере для загрузки вашего сайта: /var/www/html/"
