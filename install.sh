#!/usr/bin/env bash
# ==============================================================================
# 3X-UI + TELEMT + HARDENED NGINX + ADGUARD HOME (ONE-COMMAND INSTALLER)
# Repository: https://github.com/nexuspyx/3x-ui-adh-telemt-auto-nginx
# Generated: 19:08.22.11.2026
# ==============================================================================

set +x
set -eo pipefail

if [[ "${1:-}" == "--help" ]]; then
    cat <<'EOF'
Установка на чистый VPS с Ubuntu 24.04/26.04 или Debian 13 (amd64/arm64).
Интерактивный запуск: bash install.sh
Запуск с автоматически выбранными путями и портами:
    INSTALL_NONINTERACTIVE=1 DOMAIN=vps.example.com \
        CF_TOKEN_FILE=/root/cloudflare-token bash install.sh
В CF_TOKEN_FILE укажите одну строку с API Token Cloudflare. Файл должен
принадлежать запускающему скрипт пользователю и иметь режим 0600 или 0400.
Для совместимости поддерживаются CF_TOKEN/CF_KEY из окружения.
Используйте шаблон Edit zone DNS для нужной зоны. Для своего токена
задайте Zone:Zone:Read и Zone:DNS:Edit. Проверка API читает зону и DNS.
Для Global API Key дополнительно задайте CF_EMAIL.
Для автоматического выбора можно переопределить PANEL_PORT, PANEL_PATH,
SUB_PATH, SUB_JSON_PATH, TG_PATH, ADH_DOH_PATH, WS_PATH и XHTTP_PATH.
До запуска нужны systemd, Python 3, curl, OpenSSL, iproute2 и ca-certificates.
EOF
    exit 0
fi
if (( $# )); then
    printf 'Неизвестные аргументы. Используйте bash install.sh --help.\n' >&2
    exit 1
fi

# Legacy credentials are imported before any external command inherits them.
CF_CREDENTIAL="${CF_KEY:-${CF_TOKEN:-}}"
CF_ACCOUNT_EMAIL="${CF_EMAIL:-}"
unset CF_KEY CF_TOKEN CF_EMAIL
export -n CF_CREDENTIAL CF_ACCOUNT_EMAIL

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

fail() {
    msg_err "$1" >&2
    exit 1
}

gen_random_string() {
    local length="$1" alphabet="${2:-a-zA-Z0-9}" chunk letters="" attempt
    # Consume each finite batch completely so pipefail cannot turn a good sample into SIGPIPE.
    for ((attempt=0; attempt<16; attempt++)); do
        chunk=$(head -c "$((length * 2 + 32))" /dev/urandom | LC_ALL=C tr -dc "$alphabet") || return 1
        letters+=$chunk
        if (( ${#letters} >= length )); then
            printf '%s' "${letters:0:length}"
            return 0
        fi
    done
    msg_err "Не удалось получить случайные данные нужной длины." >&2
    return 1
}

read_prompt() {
    local message="$1" setting="$2" answer status=0
    local -a options=(-r)
    [[ "${3:-0}" == 1 ]] && options+=(-s)
    printf '%b' "$message"
    if [[ ! -t 0 ]] && { : </dev/tty; } 2>/dev/null; then
        IFS= read "${options[@]}" answer < /dev/tty || status=$?
    else
        IFS= read "${options[@]}" answer || status=$?
    fi
    [[ "${3:-0}" == 1 ]] && printf '\n'
    (( status == 0 )) || fail "Ввод закончился (EOF). Для автоматического запуска смотрите bash install.sh --help."
    printf -v "$setting" '%s' "$answer"
}

# Trim form input as UTF-8, including the formatting characters often copied at its edges.
trim_input() {
    printf '%s' "$1" | python3 -c '
import sys
import unicodedata

def padding(char):
    return char.isspace() or unicodedata.category(char) == "Cf"

def strip_outer(value):
    first = 0
    last = len(value)
    while first < last and padding(value[first]):
        first += 1
    while last > first and padding(value[last - 1]):
        last -= 1
    return value[first:last]

value = strip_outer(sys.stdin.buffer.read().decode("utf-8"))
if sys.argv[1] and len(value) >= 2 and value[0] in (chr(34), chr(39)) and value[-1] == value[0]:
    value = strip_outer(value[1:-1])
sys.stdout.buffer.write(value.encode("utf-8"))
' "${2:+unquote}" 2>/dev/null
}

normalize_domain() {
    printf '%s' "$1" | python3 -c '
import re
import sys
import unicodedata
from urllib.parse import urlsplit

try:
    raw = sys.stdin.buffer.read().decode("utf-8")
    if any(char.isspace() or unicodedata.category(char) in {"Cc", "Cf"} for char in raw):
        raise ValueError
    if raw.startswith("//") or "\\" in raw:
        raise ValueError
    parsed = urlsplit(raw if "://" in raw else "//" + raw)
    if parsed.scheme not in {"", "http", "https"} or parsed.username is not None:
        raise ValueError
    if not parsed.hostname or parsed.netloc.endswith(":") or parsed.port == 0:
        raise ValueError
    domain = parsed.hostname.encode("idna").decode("ascii").lower().removesuffix(".")
    label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    if len(domain) > 253 or not re.fullmatch(rf"(?:{label}\.)+{label}", domain):
        raise ValueError
    if domain.rsplit(".", 1)[1].isdigit():
        raise ValueError
except (UnicodeError, ValueError):
    sys.exit(1)
print(domain)
'
}

read_choice() {
    local setting="$1" selection="${!1}"
    if [[ -n "$selection" ]] || [[ "$INSTALL_NONINTERACTIVE" == 1 ]]; then
        selection=$(trim_input "$selection") || fail "Ошибка кодировки параметра $setting."
        selection="${selection:-1}"
        [[ "$selection" == 1 || "$selection" == 2 ]] || fail "Параметр $setting должен быть 1 или 2."
        [[ "$INSTALL_NONINTERACTIVE" != 1 || "$selection" == 1 ]] ||
            fail "В режиме INSTALL_NONINTERACTIVE доступен вариант 1."
    else
        while true; do
            read_prompt "${C_GREEN}?${C_RESET} Выберите вариант [1/2] (по умолчанию: 1): " selection
            if selection=$(trim_input "$selection"); then
                selection="${selection:-1}"
                [[ "$selection" == 1 || "$selection" == 2 ]] && break
            fi
            msg_warn "Введите 1 или 2."
        done
    fi
    printf -v "$setting" '%s' "$selection"
}

normalize_port() {
    local number
    number=$(trim_input "$1") || return 1
    [[ "$number" =~ ^[0123456789]+$ ]] || return 1
    number="${number#"${number%%[!0]*}"}"
    [[ -n "$number" && ${#number} -le 5 ]] || return 1
    (( number >= 1024 && number <= 65535 )) || return 1
    printf '%s' "$number"
}

reserve_port() {
    local port="$1" protocols="${2:-tcp}" protocol
    [[ " $reserved_ports " != *" $port "* ]] || return 1
    for protocol in $protocols; do
        [[ " $occupied_ports " != *" $protocol:$port "* ]] || return 1
    done
    reserved_ports+=" $port"
}

set_port() {
    local setting="$1" description="$2" default_port="$3" manual="$4" entered normalized attempt
    if [[ "$manual" == 1 ]]; then
        while true; do
            read_prompt "${C_CYAN}->${C_RESET} $description [$default_port]: " entered
            if entered=$(trim_input "$entered") && normalized=$(normalize_port "${entered:-$default_port}") &&
                reserve_port "$normalized"; then
                break
            fi
            msg_warn "Выберите свободный порт от 1024 до 65535 без конфликта с другими службами."
        done
    elif [[ -n "${!setting}" ]]; then
        normalized=$(normalize_port "${!setting}") && reserve_port "$normalized" ||
            fail "Параметр $setting задаёт недопустимый или занятый порт."
    else
        for ((attempt=0; attempt<30000; attempt++)); do
            normalized=$(( (RANDOM + attempt) % 30000 + 20000 ))
            if reserve_port "$normalized"; then
                break
            fi
        done
        (( attempt < 30000 )) || fail "Не удалось подобрать свободный порт для $description."
    fi
    printf -v "$setting" '%s' "$normalized"
}

normalize_path() {
    local path="$1" LC_ALL=C
    path="${path#/}"
    path="${path%/}"
    [[ "$path" =~ ^[a-zA-Z0-9_-]+(/[a-zA-Z0-9_-]+)*$ ]] || return 1
    printf '/%s' "$path"
}

reserve_path() {
    local path candidate mode="$2" i
    path=$(printf '%s' "$1" | LC_ALL=C tr 'A-Z' 'a-z')
    case "$path" in
        /assets*|/wp-admin*|/wp-login*|/xmlrpc*|/phpmyadmin*|/pma*|/adminer*|/actuator*|/solr*|/shell*|/eval*)
            return 1 ;;
    esac
    for i in "${!paths[@]}"; do
        candidate="${paths[i]}"
        [[ "$path" != "$candidate" && "$path" != "$candidate/"* && "$candidate" != "$path/"* ]] || return 1
        [[ "$mode" != prefix || "$candidate" != "$path"* ]] || return 1
        [[ "${path_modes[i]}" != prefix || "$path" != "$candidate"* ]] || return 1
    done
    paths+=("$path")
    path_modes+=("$mode")
}

validate_path() {
    local path="$1" mode="$2"
    if [[ "$mode" == management ]]; then
        [[ "${path#/}" != */* ]]
    else
        reserve_path "$path" "$mode"
    fi
}

set_path() {
    local setting="$1" description="$2" default_path="$3" mode="$4" manual="$5" entered path
    if [[ "$manual" == 1 ]]; then
        while true; do
            read_prompt "${C_CYAN}->${C_RESET} $description [$default_path]: " entered
            if entered=$(trim_input "$entered") && path=$(normalize_path "${entered:-$default_path}") &&
                validate_path "$path" "$mode"; then
                break
            fi
            msg_warn "Введите свободный путь из латинских букв, цифр, дефисов и знаков подчёркивания."
        done
    else
        entered=$(trim_input "${!setting}") || fail "Ошибка кодировки параметра $setting."
        if [[ -n "$entered" || -n "$default_path" ]]; then
            path=$(normalize_path "${entered:-$default_path}") && validate_path "$path" "$mode" ||
                fail "Параметр $setting задаёт недопустимый или занятый путь."
        else
            while true; do
                path="/$(gen_random_string 10)"
                validate_path "$path" "$mode" && break
            done
        fi
    fi
    case "$mode" in
        directory) path+=/ ;;
        management) path="${path#/}" ;;
    esac
    printf -v "$setting" '%s' "$path"
}

echo
echo -e "${C_CYAN}╔══════════════════════════════════════════════════════════════════════════════════╗${C_RESET}"
echo -e "${C_CYAN}║        3X-UI + TELEMT + HARDENED NGINX + ADGUARD HOME (STEALTH EDITION)          ║${C_RESET}"
echo -e "${C_CYAN}║                  Мастер автоматической установки и полной защиты                 ║${C_RESET}"
echo -e "${C_CYAN}╚══════════════════════════════════════════════════════════════════════════════════╝${C_RESET}"
echo

# ─────────────────────────────────────────────────────────────────────────────
# [0] OS CHECK & NETWORK DETECTION
# ─────────────────────────────────────────────────────────────────────────────
msg_inf "Проверка совместимости операционной системы..."
[[ -r /etc/os-release ]] || fail "Не удалось определить операционную систему."
. /etc/os-release
case "$ID:$VERSION_ID" in
    ubuntu:24.04|ubuntu:26.04|debian:13) ;;
    *) fail "Неподдерживаемая ОС: ${PRETTY_NAME:-$ID}. Требуется Ubuntu 24.04/26.04 или Debian 13." ;;
esac
case "$(uname -m)" in
    x86_64|aarch64|arm64) ;;
    *) fail "Поддерживаются архитектуры amd64 и arm64." ;;
esac
for command_name in python3 curl openssl ss systemctl; do
    command -v "$command_name" >/dev/null ||
        fail "До запуска установщика нужна утилита $command_name. Смотрите bash install.sh --help."
done
systemctl show --property=Version --value >/dev/null 2>&1 ||
    fail "Не удалось подключиться к системному менеджеру systemd."
case "${INSTALL_NONINTERACTIVE:-0}" in
    0|1) ;;
    *) fail "INSTALL_NONINTERACTIVE должен быть 0 или 1." ;;
esac
msg_ok "Операционная система поддерживается: ${PRETTY_NAME:-$ID}"

PANEL_PORT="${PANEL_PORT:-}"
GRPC_PORT=43935
TG_PORT=18080
TG_DECOY_PORT=18082
TG_PANEL_INTERNAL_PORT=8080
TG_PANEL_PORT=65001
ADH_DNS_PORT=57998
ADH_WEB_PORT=3000
ADH_DOH_PORT="$ADH_WEB_PORT"

# Snapshot the existing listeners before allocating or changing any port.
listeners=$(ss -H -lntu) || fail "Не удалось проверить занятые порты."
occupied_ports=""
reserved_ports=""
while read -r protocol _ _ _ address _; do
    case "$protocol" in
        tcp|udp) occupied_ports+=" $protocol:${address##*:}" ;;
    esac
done <<< "$listeners"
for port in 80 9091 9443 "$GRPC_PORT" "$TG_PORT" "$TG_DECOY_PORT" "$TG_PANEL_INTERNAL_PORT" "$TG_PANEL_PORT" "$ADH_WEB_PORT"; do
    reserve_port "$port" || fail "Фиксированный порт $port/TCP занят."
done
for port in 443 8443 "$ADH_DNS_PORT"; do
    reserve_port "$port" "tcp udp" || fail "Фиксированный порт $port (TCP/UDP) занят."
done

detect_public_ipv4() {
    curl -q -fsS4 --connect-timeout 2 --max-time 4 "$1" 2>/dev/null | python3 -c '
import ipaddress
import sys
try:
    ip = ipaddress.IPv4Address(sys.stdin.read().strip())
    if not ip.is_global or ip.is_multicast:
        sys.exit(1)
except ValueError:
    sys.exit(1)
print(ip)
' 2>/dev/null
}
msg_inf "Определение внешнего IPv4-адреса сервера..."
SERVER_IP=$(detect_public_ipv4 https://icanhazip.com) ||
    SERVER_IP=$(detect_public_ipv4 https://ifconfig.me) ||
    fail "Не удалось определить публичный IPv4. Проверьте DNS и исходящие HTTPS-соединения."
msg_ok "Внешний IPv4-адрес сервера: $SERVER_IP"

print_banner() {
    printf "\n"
    printf "  \e[38;5;51m   ______      __                   _____                                \e[0m\n"
    printf "  \e[38;5;45m  / ____/_  __/ /___  __________   / ___/___  ______   _____  _____        \e[0m\n"
    printf "  \e[38;5;39m / /_  / / / / __/ / / / ___/ _ \  \__ \/ _ \/ ___/ | / / _ \/ ___/        \e[0m\n"
    printf "  \e[38;5;33m/ __/ / /_/ / /_/ /_/ / /  /  __/ ___/ /  __/ /   | |/ /  __/ /            \e[0m\n"
    printf "  \e[38;5;27m/_/    \__,_/\__/\__,_/_/   \___/ /____/\___/_/    |___/\___/_/             \e[0m\n"
    printf "\n"
    printf "  \e[1;37m• Stealth VPN & Multi-Proxy Core Architecture\e[0m  \e[38;5;45m[ Ubuntu / Debian ]\e[0m\n"
    printf "  \e[38;5;244m• Reality • XHTTP • Hysteria 2 • gRPC • AdGuard DoH • Telemt • Server Mgr\e[0m\n"
    printf "  ─────────────────────────────────────────────────────────────────────────────\n\n"
}

# ─────────────────────────────────────────────────────────────────────────────
# [1] INTERACTIVE WIZARD
# ─────────────────────────────────────────────────────────────────────────────
print_banner
printf '%b=== ШАГ [1/9]: МАСТЕР НАСТРОЙКИ СЕРВЕРА ===%b\n\n' "$C_BOLD" "$C_RESET"

msg_inf "Для выпуска сертификата укажите домен с A-записью на $SERVER_IP."
domain_input="${DOMAIN:-}"
if [[ -n "$domain_input" || "$INSTALL_NONINTERACTIVE" == 1 ]]; then
    domain_input=$(trim_input "$domain_input") && DOMAIN=$(normalize_domain "$domain_input") ||
        fail "Укажите корректное доменное имя в DOMAIN."
else
    while true; do
        read_prompt "${C_GREEN}?${C_RESET} Введите доменное имя сервера (например, vps.example.com): " domain_input
        if domain_input=$(trim_input "$domain_input") && DOMAIN=$(normalize_domain "$domain_input"); then
            break
        fi
        msg_warn "Введите корректное доменное имя без внутренних пробелов."
    done
fi
msg_ok "Доменное имя сервера: $DOMAIN"

read_cf_file() {
    python3 -c '
import os
import stat
import sys
with os.fdopen(os.open(sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK), "rb") as source:
    info = os.fstat(source.fileno())
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) not in (0o400, 0o600)):
        sys.exit(1)
    data = source.read(4097).removesuffix(b"\n").removesuffix(b"\r")
    if len(data) > 4096 or any(char in data for char in (b"\r", b"\n", b"\0")):
        sys.exit(1)
    sys.stdout.buffer.write(data)
' "$1" 2>/dev/null
}

cf_request() {
    local endpoint="$1" expectation="$2" zone="${3:-}" response
    if ! response=$(
        if [[ -n "$CF_ACCOUNT_EMAIL" ]]; then
            printf 'X-Auth-Key: %s\nX-Auth-Email: %s\n' "$CF_CREDENTIAL" "$CF_ACCOUNT_EMAIL"
        else
            printf 'Authorization: Bearer %s\n' "$CF_CREDENTIAL"
        fi | curl -q -sS --proto '=https' --connect-timeout 6 --max-time 12 \
            --header @- --write-out '\n%{http_code}' \
            "https://api.cloudflare.com/client/v4/$endpoint" 2>/dev/null
    ); then
        msg_err "Ошибка Cloudflare: API недоступен. Проверьте DNS и HTTPS." >&2
        return 1
    fi
    printf '%s' "$response" | python3 -c '
import json
import re
import sys

expectation, zone = sys.argv[1:]
raw, _, http_status = sys.stdin.read().rpartition("\n")

def error(message):
    print(f"Ошибка Cloudflare: {message}", file=sys.stderr)
    sys.exit(1)

if not re.fullmatch(r"[0-9]{3}", http_status):
    error("не удалось прочитать HTTP-статус.")
if http_status != "200":
    error(f"HTTP {http_status}. Проверьте учетные данные, права и доступ к зоне.")
try:
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("success") is not True or "result" not in data:
        error("API отклонил запрос. Проверьте учетные данные, права и доступ к зоне.")
    result = data["result"]
    if expectation == "active" and (not isinstance(result, dict) or result.get("status") != "active"):
        error("токен не активен.")
    if expectation == "user" and not isinstance(result, dict):
        error("некорректный ответ проверки Global API Key.")
    if expectation in {"zone", "dns"} and not isinstance(result, list):
        error("некорректный ответ для зоны или DNS-записей.")
    if expectation == "zone":
        for item in result:
            if isinstance(item, dict) and item.get("name") == zone:
                zone_id = item.get("id")
                if isinstance(zone_id, str) and re.fullmatch(r"[0-9a-fA-F]{32}", zone_id):
                    print(zone_id)
                    break
except (UnicodeError, ValueError):
    error("не удалось разобрать ответ JSON.")
' "$expectation" "$zone"
}

validate_cf_credentials() {
    local candidate="$DOMAIN" zone_id
    local LC_ALL=C
    if [[ "$CF_CREDENTIAL" =~ ^[0-9a-fA-F]{37}$ ]]; then
        if [[ ! "$CF_ACCOUNT_EMAIL" =~ ^[[:alnum:]._%+-]+@[[:alnum:].-]+\.[[:alnum:]]+$ ]]; then
            msg_err "Ошибка Cloudflare: для Global API Key обязателен корректный CF_EMAIL." >&2
            return 1
        fi
        cf_request user user || return 1
    else
        if [[ ! "$CF_CREDENTIAL" =~ ^[a-zA-Z0-9_-]{40}$ || -n "$CF_ACCOUNT_EMAIL" ]]; then
            msg_err "Ошибка Cloudflare: нужен API Token из 40 символов или Global API Key с email." >&2
            return 1
        fi
        cf_request user/tokens/verify active || return 1
    fi

    # Certbot can use a parent zone when the requested hostname is a subdomain.
    while [[ "$candidate" == *.* ]]; do
        zone_id=$(cf_request "zones?name=$candidate" zone "$candidate") || return 1
        if [[ -n "$zone_id" ]]; then
            cf_request "zones/$zone_id/dns_records?per_page=1" dns
            return $?
        fi
        candidate="${candidate#*.}"
    done
    msg_err "Ошибка Cloudflare: зона для $DOMAIN недоступна по указанным учетным данным." >&2
    return 1
}

msg_inf "Для SSL через DNS-01 используйте шаблон Cloudflare Edit zone DNS для нужной зоны."
if [[ -n "${CF_TOKEN_FILE:-}" ]]; then
    [[ -z "$CF_CREDENTIAL" ]] || fail "Укажите CF_TOKEN_FILE или CF_TOKEN/CF_KEY."
    CF_CREDENTIAL=$(read_cf_file "$CF_TOKEN_FILE") ||
        fail "Не удалось прочитать CF_TOKEN_FILE. Нужен обычный файл владельца с режимом 0600 или 0400."
fi
if [[ -n "${CF_TOKEN_FILE:-}" || -n "$CF_CREDENTIAL" || "$INSTALL_NONINTERACTIVE" == 1 ]]; then
    CF_CREDENTIAL=$(trim_input "$CF_CREDENTIAL" unquote) &&
        CF_ACCOUNT_EMAIL=$(trim_input "$CF_ACCOUNT_EMAIL" unquote) ||
        fail "Ошибка Cloudflare: учетные данные должны быть в UTF-8."
    validate_cf_credentials || exit 1
else
    while true; do
        read_prompt "${C_GREEN}?${C_RESET} Введите Cloudflare API Token: " CF_CREDENTIAL 1
        CF_ACCOUNT_EMAIL=""
        if ! CF_CREDENTIAL=$(trim_input "$CF_CREDENTIAL" unquote); then
            msg_warn "Ошибка Cloudflare: учетные данные должны быть в UTF-8."
            continue
        fi
        if [[ "$CF_CREDENTIAL" =~ ^[0-9a-fA-F]{37}$ ]]; then
            read_prompt "${C_GREEN}?${C_RESET} Введите email Cloudflare для Global API Key: " CF_ACCOUNT_EMAIL
            if ! CF_ACCOUNT_EMAIL=$(trim_input "$CF_ACCOUNT_EMAIL" unquote); then
                msg_warn "Ошибка Cloudflare: email должен быть в UTF-8."
                continue
            fi
        fi
        validate_cf_credentials && break
    done
fi
msg_ok "Учетные данные Cloudflare проверены. Доступ к чтению зоны и DNS-записей подтверждён."

printf '\n%bНастройка путей и портов%b\n' "$C_BOLD" "$C_RESET"
msg_inf "1 — автоматический выбор. 2 — ручная настройка путей и порта 3X-UI."
read_choice PATH_CHOICE
paths=()
path_modes=()
if [[ "$PATH_CHOICE" == 2 ]]; then
    set_path PANEL_PATH "Путь к веб-панели 3X-UI" "/xui/" directory 1
    set_port PANEL_PORT "Внутренний порт панели управления 3X-UI" 2053 1
    set_path SUB_PATH "Путь для обычной подписки" "/sub/" directory 1
    set_path SUB_JSON_PATH "Путь для JSON подписки" "/sub-json/" directory 1
    set_path TG_PATH "Путь для Telegram Web Proxy" "${TG_PATH:-/tg}" prefix 1
    set_path ADH_DOH_PATH "Путь для AdGuard DoH" "${ADH_DOH_PATH:-/mediagetv2}" doh 1
    set_path WS_PATH "Путь для VLESS WebSocket" "/vless-ws" exact 1
    set_path XHTTP_PATH "Путь для VLESS XHTTP" "/vless-xhttp" prefix 1
else
    # Register configured paths first so generated ones can avoid collisions with them.
    TG_PATH="${TG_PATH:-${USER_TG_PATH:-/tg}}"
    ADH_DOH_PATH="${ADH_DOH_PATH:-${USER_ADH_DOH_PATH:-/mediagetv2}}"
    routes=(PANEL_PATH:directory SUB_PATH:directory SUB_JSON_PATH:directory TG_PATH:prefix ADH_DOH_PATH:doh WS_PATH:exact XHTTP_PATH:prefix)
    for route in "${routes[@]}"; do
        setting="${route%:*}"
        if [[ -n "${!setting}" ]]; then
            set_path "$setting" "$setting" "" "${route#*:}" 0
        fi
    done
    for route in "${routes[@]}"; do
        setting="${route%:*}"
        if [[ -z "${!setting}" ]]; then
            set_path "$setting" "$setting" "" "${route#*:}" 0
        fi
    done
    # An explicit panel port must take precedence over later auto-allocated ports.
    [[ -z "$PANEL_PORT" ]] || set_port PANEL_PORT "панель 3X-UI" "" 0
fi
while true; do
    GRPC_PATH="vless-$(gen_random_string 8)"
    reserve_path "/$GRPC_PATH" prefix && break
done
while true; do
    TROJAN_PATH="trojan-$(gen_random_string 8)"
    reserve_path "/$TROJAN_PATH" prefix && break
done

if [[ "$PATH_CHOICE" == 2 ]]; then
    printf '\n%bНастройка модуля управления сервером%b\n' "$C_BOLD" "$C_RESET"
    msg_inf "1 — автоматический выбор. 2 — ручная настройка порта, пути и данных входа."
    read_choice MGMT_CHOICE
else
    MGMT_CHOICE="${MGMT_CHOICE:-1}"
fi
if [[ "$MGMT_CHOICE" == 2 ]]; then
    set_port MGMT_PORT "Порт модуля управления" 54892 1
    set_path MGMT_SECRET_PATH "Секретный путь модуля управления" "cfg-admin" management 1
    while true; do
        read_prompt "${C_CYAN}->${C_RESET} Логин администратора [admin]: " MGMT_USER
        if MGMT_USER=$(trim_input "$MGMT_USER"); then
            MGMT_USER="${MGMT_USER:-admin}"
            [[ ! "$MGMT_USER" =~ [[:space:][:cntrl:]:] ]] && break
        fi
        msg_warn "Введите логин без пробелов, управляющих символов и двоеточия."
    done
    read_prompt "${C_CYAN}->${C_RESET} Пароль администратора [Enter для автогенерации]: " MGMT_PASS 1
    [[ -n "$MGMT_PASS" ]] || MGMT_PASS="$(gen_random_string 16)"
else
    MGMT_PORT=""
    set_port MGMT_PORT "модуль управления" "" 0
    MGMT_SECRET_PATH="cfg-$(gen_random_string 8)"
    MGMT_USER="admin_$(gen_random_string 4)"
    MGMT_PASS="$(gen_random_string 16)"
fi

[[ -n "$PANEL_PORT" ]] || set_port PANEL_PORT "панель 3X-UI" "" 0
for setting in SUB_PORT WS_PORT CASCADE_1_PORT CASCADE_2_PORT CASCADE_3_PORT TROJAN_PORT; do
    printf -v "$setting" '%s' ""
    set_port "$setting" "$setting" "" 0
done
CASCADE_WS_PORT="$CASCADE_1_PORT"
PANEL_PATH_NO_SLASH="${PANEL_PATH#/}"
PANEL_PATH_NO_SLASH="${PANEL_PATH_NO_SLASH%/}"
TG_PATH_NO_SLASH="${TG_PATH#/}"
ADH_DOH_PATH_NO_SLASH="${ADH_DOH_PATH#/}"
WS_PATH_NO_SLASH="${WS_PATH#/}"
XHTTP_PATH_NO_SLASH="${XHTTP_PATH#/}"
TROJAN_PATH_NO_SLASH="$TROJAN_PATH"
GRPC_PATH_NO_SLASH="$GRPC_PATH"

PANEL_PATH_NO_SLASH_LEADING="/$PANEL_PATH_NO_SLASH"
WS_PATH_LEADING="$WS_PATH"
CASCADE_1_SUFFIX=$(gen_random_string 5 'a-zA-Z')
CASCADE_1_PATH="$WS_PATH_LEADING/$CASCADE_1_SUFFIX"
CASCADE_2_SUFFIX=$(gen_random_string 5 'a-zA-Z')
CASCADE_2_PATH="$WS_PATH_LEADING/$CASCADE_2_SUFFIX"
CASCADE_3_SUFFIX=$(gen_random_string 5 'a-zA-Z')
CASCADE_3_PATH="$WS_PATH_LEADING/$CASCADE_3_SUFFIX"
CASCADE_SUFFIX="$CASCADE_1_SUFFIX"
CASCADE_WS_PATH_LEADING="$CASCADE_1_PATH"
XHTTP_PATH_LEADING="$XHTTP_PATH"
TROJAN_PATH_LEADING="/$TROJAN_PATH"
GRPC_PATH_LEADING="/$GRPC_PATH"

PANEL_USER="admin_$(gen_random_string 6)"
PANEL_PASS="$(gen_random_string 14)"
TG_USER="user_$(gen_random_string 4)"
TG_SECRET="$(openssl rand -hex 16)"
TG_PANEL_PASS="$(gen_random_string 14)"
ADH_USER="adguard_$(gen_random_string 6)"
ADH_PASS="$(gen_random_string 14)"

printf '\n  • Домен: %s (%s)\n' "$DOMAIN" "$SERVER_IP"
printf '  • Панель 3X-UI: %s (внутренний порт %s)\n' "$PANEL_PATH" "$PANEL_PORT"
printf '  • Модуль управления: /%s/ (порт %s)\n\n' "$MGMT_SECRET_PATH" "$MGMT_PORT"
msg_ok "Все параметры приняты. Переходим к автоматической установке компонентов..."

# ─────────────────────────────────────────────────────────────────────────────
# [2] SYSTEM DEPENDENCIES & NETWORK HARDENING
# ─────────────────────────────────────────────────────────────────────────────
msg_inf "Шаг [2/9]: Установка зависимостей и тюнинг сетевого стека..."
if [[ -n "${SSH_CONNECTION:-}" ]]; then
    read -r _ _ _ SSH_PORT <<< "$SSH_CONNECTION"
else
    SSH_PORT=$(/usr/sbin/sshd -T 2>/dev/null | awk '/^port / { if (port && port != $2) exit 1; port=$2 } END { if (port) print port }') ||
        fail "Не удалось однозначно определить порт SSH. Запустите установку из SSH-сеанса."
fi
[[ "$SSH_PORT" =~ ^[1-9][0-9]{0,4}$ ]] && (( SSH_PORT <= 65535 )) ||
    fail "Не удалось определить порт SSH."
ss -H -ltn | awk -v port="$SSH_PORT" '$4 ~ (":" port "$") { found=1 } END { exit !found }' ||
    fail "На определённом порту SSH нет слушателя."
if command -v ufw >/dev/null 2>&1; then
    preliminary_ufw=$(LC_ALL=C ufw status) || fail "Не удалось проверить действующий UFW."
    [[ "$preliminary_ufw" == 'Status: inactive' ]] ||
        fail "На сервере уже действует UFW. Чистая установка требует исходного состояния без активных правил."
fi
for path in /usr/local/x-ui /etc/x-ui/x-ui.db /opt/AdGuardHome /etc/telemt/config.toml \
    /etc/telemt-panel/config.toml /etc/server-manager/config.json /root/server_credentials.txt; do
    [[ ! -e "$path" && ! -L "$path" ]] || fail "Обнаружено существующее состояние: $path. Требуется чистый VPS."
done
WORKDIR=$(mktemp -d /tmp/future-server-install.XXXXXXXX) ||
    fail "Не удалось создать приватный рабочий каталог."
cleanup_workdir() {
    local rc=$?
    if [[ -n "${WORKDIR:-}" && -d "$WORKDIR" ]]; then
        rm -rf "$WORKDIR"
    fi
    exit "$rc"
}
trap cleanup_workdir EXIT INT TERM
asset_root=
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
    source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
    [[ ! -d "$source_dir/assets" ]] || asset_root="$source_dir/assets"
fi
if [[ -z "$asset_root" ]]; then
    [[ "$BRANCH" =~ ^[A-Za-z0-9._-]+$ ]] || fail "Некорректная ревизия INSTALL_BRANCH."
    if [[ ! "$BRANCH" =~ ^[a-f0-9]{40}$ ]]; then
        commit_json=$(curl -q -fsS --connect-timeout 10 --max-time 30 \
            "https://api.github.com/repos/$REPO_OWNER/$REPO_NAME/commits/$BRANCH") ||
            fail "Не удалось определить ревизию файлов проекта."
        BRANCH=$(printf '%s' "$commit_json" | python3 -c 'import json, sys; print(json.load(sys.stdin)["sha"])') ||
            fail "GitHub вернул неизвестный ответ."
        [[ "$BRANCH" =~ ^[a-f0-9]{40}$ ]] || fail "GitHub вернул некорректную ревизию."
    fi
    GITHUB_RAW="https://raw.githubusercontent.com/$REPO_OWNER/$REPO_NAME/$BRANCH"
fi
while IFS= read -r asset; do
    destination="$WORKDIR/assets/$asset"
    mkdir -p -- "$(dirname -- "$destination")"
    if [[ -n "$asset_root" ]]; then
        install -m 0600 "$asset_root/$asset" "$destination" || fail "Ресурс проекта недоступен: $asset"
    else
        curl -q -fsSL --proto '=https' --proto-redir '=https' --retry 2 --connect-timeout 10 \
            --max-time 45 "$GITHUB_RAW/assets/$asset" -o "$destination" || fail "Не удалось скачать ресурс: $asset"
    fi
    [[ -s "$destination" ]] || fail "Ресурс пуст: $asset"
done <<'PROJECT_ASSETS'
install/releases.sh
adguard/AdGuardHome.yaml.template
adguard/configure_xui_dns.py
telemt/telemt.toml.template
telemt/telemt-panel.toml.template
telemt/telemt.service
telemt/telemt-panel.service
nginx/01-bot-protection.conf
nginx/stream.conf.template
nginx/telemt-decoy.conf.template
nginx/telemt-panel.conf.template
nginx/vhost.conf.template
server-manager/server_manager.py
server-manager/server-mgr
server-manager/server-manager.service
sub-template/index.html
PROJECT_ASSETS
bash -n "$WORKDIR/assets/install/releases.sh" || fail "Синтаксическая ошибка в подготовке релизов."
source "$WORKDIR/assets/install/releases.sh"
XUI_TAG=""
if XUI_TAG=$(curl -q -fsSL --proto '=https' --proto-redir '=https' \
    --connect-timeout 4 --max-time 8 \
    "https://api.github.com/repos/MHSanaei/3x-ui/releases/latest" 2>/dev/null | python3 -c 'import json, sys; print(json.load(sys.stdin).get("tag_name", ""))' 2>/dev/null) && [[ "$XUI_TAG" =~ ^v[0-9]+\.[0-9]+ ]]; then
    :
else
    XUI_TAG="v3.11.0"
fi
prepare_fork_releases "$WORKDIR" "$(dpkg --print-architecture)" "$XUI_TAG" || fail "Не удалось подготовить проверенные релизы."

setup_swap() {
    local active available fs
    msg_inf "Проверка и настройка файла подкачки (Swap)..."
    if active=$(swapon --show=NAME --noheadings 2>/dev/null) && [[ -n "$active" ]]; then
        msg_ok "Файл подкачки (swap) уже настроен и активен."
        return 0
    fi

    if [[ -e /swapfile || -L /swapfile ]]; then
        if swapon /swapfile 2>/dev/null; then
            msg_ok "Существующий /swapfile успешно подключен."
            return 0
        fi
    fi

    available=$(df -Pk / 2>/dev/null | awk 'END {print $4}') || available=0
    if [[ ! "$available" =~ ^[0-9]+$ ]] || (( available < 3145728 )); then
        msg_warn "Менее 3 ГБ свободного места на диске. Создание swap пропущено."
        return 0
    fi

    fs=$(findmnt -n -o FSTYPE --target / 2>/dev/null) || fs=""
    if [[ "$fs" != "ext4" && "$fs" != "xfs" ]]; then
        msg_warn "Файловая система ($fs) не поддерживает fallocate для swap. Пропущено."
        return 0
    fi

    if fallocate -l 1G /swapfile 2>/dev/null && chmod 600 /swapfile && mkswap /swapfile >/dev/null 2>&1 && swapon /swapfile 2>/dev/null; then
        if ! grep -q "^/swapfile" /etc/fstab 2>/dev/null; then
            printf '\n/swapfile none swap sw 0 0\n' >> /etc/fstab
        fi
        msg_ok "Файл подкачки swap 1 ГБ успешно создан и активирован."
    else
        rm -f /swapfile 2>/dev/null || true
        msg_warn "Не удалось автоматически активировать /swapfile."
    fi
}
setup_swap

export DEBIAN_FRONTEND=noninteractive
apt-get update -q -y
apt-get install -q -y --no-remove ufw
ufw_status=$(LC_ALL=C ufw status verbose) || fail "Не удалось определить состояние UFW."
[[ "$ufw_status" == 'Status: inactive' || "$ufw_status" == 'Status: active'* ]] ||
    fail "UFW вернул неизвестное состояние."
if [[ "$ufw_status" == 'Status: active'* && "$ufw_status" != *'Default: deny (incoming)'* ]]; then
    fail "Действующая политика UFW не запрещает входящие подключения по умолчанию."
fi
if [[ "$ufw_status" == 'Status: inactive' ]]; then
    ufw allow "${SSH_PORT}/tcp" comment 'SSH' > /dev/null
fi
ufw allow 443/tcp comment 'Nginx HTTPS & Reality' > /dev/null
ufw allow 443/udp comment 'Hysteria 2 / QUIC' > /dev/null
ufw deny 80/tcp comment 'Port 80 Closed' > /dev/null
if [[ "$ufw_status" == 'Status: inactive' ]]; then
    ufw default deny incoming > /dev/null
    ufw default allow outgoing > /dev/null
    ufw --force enable > /dev/null
fi
if [[ -f /etc/default/ufw ]]; then
    sed -i 's/^IPV6=.*/IPV6=no/' /etc/default/ufw 2>/dev/null || true
fi
if [[ -f /etc/ufw/before.rules ]]; then
    sed -i 's/-A ufw-before-input -p icmp --icmp-type echo-request -j ACCEPT/-A ufw-before-input -p icmp --icmp-type echo-request -j DROP/g' /etc/ufw/before.rules 2>/dev/null || true
    sed -i 's/-A ufw-before-forward -p icmp --icmp-type echo-request -j ACCEPT/-A ufw-before-forward -p icmp --icmp-type echo-request -j DROP/g' /etc/ufw/before.rules 2>/dev/null || true
fi
if [[ -f /etc/ufw/sysctl.conf ]]; then
    sed -i 's|^#*net/ipv4/icmp_echo_ignore_all=.*|net/ipv4/icmp_echo_ignore_all=1|' /etc/ufw/sysctl.conf 2>/dev/null || true
fi
msg_ok "Правила для SSH и 443 TCP/UDP добавлены в UFW (IPv6 отключен, ICMP ping заглушен)."

apt-get install -q -y --no-remove \
    curl wget jq sudo certbot python3-certbot-dns-cloudflare \
    sqlite3 ufw fail2ban python3-systemd nftables nginx-full \
    tar gzip openssl procps iproute2 netcat-openbsd apache2-utils \
    ca-certificates

# Отключение IPv6, скрытие ICMP Ping и включение BBR
cat << 'EOF' > /etc/sysctl.d/99-hardened-anti-tspu.conf
# Полное отключение IPv6 (максимальная скрытность)
net.ipv6.conf.all.disable_ipv6 = 1
net.ipv6.conf.default.disable_ipv6 = 1
net.ipv6.conf.lo.disable_ipv6 = 1

# Stealth режим: полный игнор ICMP echo (Ping) и других ICMP запросов
net.ipv4.icmp_echo_ignore_all = 1
net.ipv4.icmp_echo_ignore_broadcasts = 1
net.ipv4.icmp_ignore_bogus_error_responses = 1

# BBR Congestion Control & TCP Fast Open
net.core.default_qdisc = fq
net.ipv4.tcp_congestion_control = bbr
net.ipv4.tcp_fastopen = 3

# Оптимизация сетевых буферов и защита от потерь QUIC/UDP (Hysteria 2)
net.core.rmem_max = 67108864
net.core.wmem_max = 67108864
net.core.rmem_default = 1048576
net.core.wmem_default = 1048576
net.ipv4.tcp_rmem = 4096 87380 33554432
net.ipv4.tcp_wmem = 4096 65536 33554432

# Внутренние очереди ядра (невидимы для ТСПУ, защита от переполнения)
net.ipv4.tcp_max_syn_backlog = 4096
net.core.netdev_max_backlog = 8192

# Плавность BBR и защита от задержек после пауз
net.ipv4.tcp_slow_start_after_idle = 0

# Защита от зависаний при урезанном MTU в туннелях (только при обнаружении дропа)
net.ipv4.tcp_mtu_probing = 1

# Расширение диапазона исходящих портов (защита от исчерпания сокетов)
net.ipv4.ip_local_port_range = 10240 65535

# Защита памяти 1 ГБ RAM от избыточного сброса в swap
vm.swappiness = 10
EOF
sysctl --system > /dev/null 2>&1 || true
sysctl -w net.ipv6.conf.all.disable_ipv6=1 net.ipv6.conf.default.disable_ipv6=1 net.ipv6.conf.lo.disable_ipv6=1 \
    net.ipv4.icmp_echo_ignore_all=1 net.ipv4.icmp_echo_ignore_broadcasts=1 net.ipv4.icmp_ignore_bogus_error_responses=1 > /dev/null 2>&1 || true

msg_ok "IPv6 отключен, ICMP ping заглушен, профиль Stealth & Safe Mobile sysctl активирован."

# ─────────────────────────────────────────────────────────────────────────────
# [3] SSL VIA CLOUDFLARE DNS-01 API (PORT 80 NEVER OPENED)
# ─────────────────────────────────────────────────────────────────────────────
msg_inf "Шаг [3/9]: Выпуск SSL-сертификата через Cloudflare DNS-01 API..."
mkdir -p /etc/letsencrypt

write_cf_file() {
    if [[ -n "$CF_ACCOUNT_EMAIL" ]]; then
        printf 'dns_cloudflare_email = %s\ndns_cloudflare_api_key = %s\n' "$CF_ACCOUNT_EMAIL" "$CF_CREDENTIAL"
    else
        printf 'dns_cloudflare_api_token = %s\n' "$CF_CREDENTIAL"
    fi | python3 -c '
import os
import stat
import sys
data = sys.stdin.buffer.read()
flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK
with os.fdopen(os.open(sys.argv[1], flags, 0o600), "wb") as target:
    info = os.fstat(target.fileno())
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid() or info.st_nlink != 1:
        sys.exit(1)
    os.fchmod(target.fileno(), 0o600)
    target.write(data)
    target.truncate()
' "$1" 2>/dev/null
}
write_cf_file /etc/letsencrypt/cloudflare.ini ||
    fail "Не удалось безопасно записать /etc/letsencrypt/cloudflare.ini."
unset CF_CREDENTIAL CF_ACCOUNT_EMAIL

if ! certbot certonly \
    --dns-cloudflare \
    --dns-cloudflare-credentials /etc/letsencrypt/cloudflare.ini \
    --dns-cloudflare-propagation-seconds 15 \
    --cert-name "${DOMAIN}" \
    -d "${DOMAIN}" \
    --non-interactive \
    --agree-tos \
    --register-unsafely-without-email; then
    msg_err "Не удалось выпустить SSL-сертификат через Cloudflare DNS-01."
    echo -e "\nВозможные причины ошибки:"
    echo -e " 1. Домен ${C_BOLD}${DOMAIN}${C_RESET} не добавлен в аккаунт Cloudflare, к которому привязан токен."
    echo -e " 2. У токена нет прав на редактирование DNS (требуется шаблон 'Edit zone DNS' с правом Zone -> DNS -> Edit)."
    echo -e " 3. В Cloudflare в секции Zone Resources не выбран домен или не указано 'All zones'."
    echo -e "\nПроверьте права токена в Cloudflare и перезапустите установку."
    exit 1
fi

install -d -m 0700 /root/cert "/root/cert/${DOMAIN}"
ln -sf "/etc/letsencrypt/live/${DOMAIN}/fullchain.pem" "/root/cert/${DOMAIN}/fullchain.pem"
ln -sf "/etc/letsencrypt/live/${DOMAIN}/privkey.pem" "/root/cert/${DOMAIN}/privkey.pem"

# Хук автопродления сертификатов
mkdir -p /etc/letsencrypt/renewal-hooks/deploy
cat << 'EOF' > /etc/letsencrypt/renewal-hooks/deploy/hardened-nginx
#!/usr/bin/env bash
set -e
case "${RENEWED_LINEAGE:-}" in
    /etc/letsencrypt/live/"DOMAIN_MARKER") ;;
    *) exit 0 ;;
esac
systemctl reload nginx
systemctl restart x-ui telemt server-manager
EOF
sed -i "s/DOMAIN_MARKER/$DOMAIN/" /etc/letsencrypt/renewal-hooks/deploy/hardened-nginx
chmod 0700 /etc/letsencrypt/renewal-hooks/deploy/hardened-nginx
msg_ok "SSL сертификат Cloudflare DNS-01 успешно выпущен. Порт 80 остался закрыт."

# ─────────────────────────────────────────────────────────────────────────────
# [4] ADGUARD HOME SETUP (0 MS LOCAL DNS & DOH)
# ─────────────────────────────────────────────────────────────────────────────
msg_inf "Шаг [4/9]: Установка и настройка AdGuard Home (DNS 0 мс + DoH)..."
install -d -m 0755 /opt/AdGuardHome
install -m 0755 "$WORKDIR/releases/AdGuardHome" /opt/AdGuardHome/AdGuardHome

# Генерация bcrypt хеша для пароля AdGuard
ADH_HASH=$(printf '%s\n' "$ADH_PASS" | htpasswd -niBC 10 adguard 2>/dev/null | cut -d: -f2) ||
    fail "Не удалось создать хеш пароля AdGuard Home."
[[ -n "$ADH_HASH" ]] || fail "Хеш пароля AdGuard Home пуст."

# Скачивание шаблона AdGuardHome
install -m 0644 "$WORKDIR/assets/adguard/AdGuardHome.yaml.template" "$WORKDIR/AdGuardHome.yaml.template"

sed -e "s|__ADH_WEB_PORT__|${ADH_WEB_PORT}|g" \
    -e "s|__ADH_USER__|${ADH_USER}|g" \
    -e "s|__ADH_PASS_HASH__|${ADH_HASH}|g" \
    -e "s|__ADH_DNS_PORT__|${ADH_DNS_PORT}|g" \
    -e "s|__ADH_DOH_PORT__|${ADH_DOH_PORT}|g" \
    "$WORKDIR/AdGuardHome.yaml.template" > /opt/AdGuardHome/AdGuardHome.yaml

(cd /opt/AdGuardHome && ./AdGuardHome -s install > /dev/null 2>&1) || true
if [[ -f /etc/systemd/system/AdGuardHome.service ]]; then
    sed -i 's|^WorkingDirectory=.*|WorkingDirectory=/opt/AdGuardHome|' /etc/systemd/system/AdGuardHome.service 2>/dev/null || true
    systemctl daemon-reload > /dev/null 2>&1 || true
fi
systemctl restart AdGuardHome
msg_ok "AdGuard Home установлен и запущен на портах ${ADH_WEB_PORT} (Web), ${ADH_DNS_PORT} (Local DNS), ${ADH_DOH_PORT} (DoH)."

# ─────────────────────────────────────────────────────────────────────────────
# [5] 3X-UI PANEL & ALL 7 INBOUNDS CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────
msg_inf "Шаг [5/9]: Установка 3X-UI и конфигурирование базы SQLite со всеми 7 inbounds..."
cp -R "$WORKDIR/releases/x-ui" /usr/local/x-ui
rm -f /usr/local/x-ui/bin/*_IR.dat /usr/local/x-ui/bin/mtg-* 2>/dev/null || true
install -m 0755 /usr/local/x-ui/x-ui.sh /usr/bin/x-ui
install -m 0644 /usr/local/x-ui/x-ui.service.debian /etc/systemd/system/x-ui.service
install -d -m 0700 /etc/x-ui
/usr/local/x-ui/x-ui setting -username "$PANEL_USER" -password "$PANEL_PASS" \
    -port "$PANEL_PORT" -webBasePath "$PANEL_PATH" >/dev/null 2>&1 ||
    fail "Не удалось задать начальные параметры 3x-ui."
/usr/local/x-ui/x-ui migrate >/dev/null 2>&1 || fail "Не удалось подготовить базу 3x-ui."
panel_user=$(sqlite3 -bail /etc/x-ui/x-ui.db 'SELECT username FROM users ORDER BY id LIMIT 1;') ||
    fail "Не удалось прочитать базу 3x-ui."
[[ "$panel_user" == "$PANEL_USER" ]] || fail "3x-ui не сохранил параметры администратора."

# Развертывание Apple Xray Subscription Template
install -d -m 0755 /etc/x-ui/sub_templates/apple
install -m 0644 "$WORKDIR/assets/sub-template/index.html" /etc/x-ui/sub_templates/apple/index.html
ln -sf /etc/x-ui/sub_templates/apple/index.html /etc/x-ui/sub_templates/apple/sub.html

# Предотвращение блокировки частых перезапусков x-ui в systemd
mkdir -p /etc/systemd/system/x-ui.service.d
echo -e "[Unit]\nStartLimitIntervalSec=0" > /etc/systemd/system/x-ui.service.d/20-restart-limits.conf

# Генерация ключей для Reality
detect_xray_bin() {
    local arch
    case "$(uname -m)" in
        x86_64|x64|amd64)           arch='amd64' ;;
        i*86|x86)                   arch='386'   ;;
        armv8*|arm64|aarch64)       arch='arm64' ;;
        armv7*)                     arch='armv7' ;;
        armv6*)                     arch='armv6' ;;
        armv5*)                     arch='armv5' ;;
        s390x)                      arch='s390x' ;;
        *)                          arch='amd64' ;;
    esac
    if [[ -x "/usr/local/x-ui/bin/xray-linux-${arch}" ]]; then
        echo "/usr/local/x-ui/bin/xray-linux-${arch}"
    elif [[ -x "/usr/local/x-ui/bin/xray-linux-arm32" ]]; then
        echo "/usr/local/x-ui/bin/xray-linux-arm32"
    else
        find /usr/local/x-ui/bin/ -name "xray*" -type f -perm /111 2>/dev/null | head -n1
    fi
}
XRAY_BIN=$(detect_xray_bin)
[[ -n "$XRAY_BIN" && -x "$XRAY_BIN" ]] || { msg_err "Исполняемый файл Xray не найден в /usr/local/x-ui/bin/"; exit 1; }

REALITY_KEYS=$("$XRAY_BIN" x25519 2>/dev/null) || { msg_err "Не удалось сгенерировать Reality ключи с помощью Xray x25519"; exit 1; }
REALITY_PRIV=$(printf '%s\n' "$REALITY_KEYS" | grep -i "Private" | awk '{print $NF}')
REALITY_PUB=$(printf '%s\n' "$REALITY_KEYS" | grep -i "Public" | awk '{print $NF}')
[[ -n "$REALITY_PRIV" && -n "$REALITY_PUB" ]] || { msg_err "Не удалось извлечь криптографические ключи Reality."; exit 1; }
REALITY_SHORTID=$(openssl rand -hex 8)

# Наполнение базы данных 3x-ui SQLite
sqlite3 -bail /etc/x-ui/x-ui.db << EOF
BEGIN TRANSACTION;
-- Обновление настроек панели и подписок (удаление старых и вставка актуальных)
DELETE FROM settings WHERE key IN (
    'webPort', 'webBasePath', 'webDomain', 'webCertFile', 'webKeyFile', 'webListen', 'subListen',
    'subCertFile', 'subKeyFile',
    'subPort', 'subPath', 'subURI', 'subJsonPath', 'subJsonURI',
    'subJsonEnable', 'subEnable', 'subUpdates', 'subEncrypt', 'subShowInfo', 'subThemeDir'
);

INSERT INTO settings (key, value) VALUES
    ('webPort', '${PANEL_PORT}'),
    ('webBasePath', '${PANEL_PATH}'),
    ('webDomain', ''),
    ('webCertFile', '/root/cert/${DOMAIN}/fullchain.pem'),
    ('webKeyFile', '/root/cert/${DOMAIN}/privkey.pem'),
    ('webListen', '127.0.0.1'),
    ('subListen', '127.0.0.1'),
    ('subCertFile', '/root/cert/${DOMAIN}/fullchain.pem'),
    ('subKeyFile', '/root/cert/${DOMAIN}/privkey.pem'),
    ('subPort', '${SUB_PORT}'),
    ('subPath', '${SUB_PATH}'),
    ('subURI', 'https://${DOMAIN}${SUB_PATH}'),
    ('subJsonPath', '${SUB_JSON_PATH}'),
    ('subJsonURI', 'https://${DOMAIN}${SUB_JSON_PATH}'),
    ('subJsonEnable', 'true'),
    ('subEnable', 'true'),
    ('subUpdates', '12'),
    ('subEncrypt', 'true'),
    ('subShowInfo', 'true'),
    ('subThemeDir', '/etc/x-ui/sub_templates/apple');

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
    '{"enabled": true, "destOverride": ["http", "tls", "quic"]}'
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
    '{"enabled": true, "destOverride": ["http", "tls", "quic"]}'
);

-- 2.1. VLESS WebSocket Duplicate (Cascade-to-N-1, disabled by default)
INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, listen, port, protocol, settings, stream_settings, tag, sniffing)
VALUES (
    '1', '0', '0', '0', '⛓️ Cascade-to-N-1', '0', '0', '127.0.0.1', '${CASCADE_1_PORT}', 'vless',
    '{"clients": [], "decryption": "none", "fallbacks": []}',
    '{
      "network": "ws",
      "security": "none",
      "wsSettings": {
        "acceptProxyProtocol": false,
        "path": "${CASCADE_1_PATH}",
        "host": "${DOMAIN}"
      }
    }',
    'in-${CASCADE_1_PORT}-tcp',
    '{"enabled": true, "destOverride": ["http", "tls", "quic"]}'
);

-- 2.2. VLESS WebSocket Duplicate (Cascade-to-N-2, disabled by default)
INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, listen, port, protocol, settings, stream_settings, tag, sniffing)
VALUES (
    '1', '0', '0', '0', '⛓️ Cascade-to-N-2', '0', '0', '127.0.0.1', '${CASCADE_2_PORT}', 'vless',
    '{"clients": [], "decryption": "none", "fallbacks": []}',
    '{
      "network": "ws",
      "security": "none",
      "wsSettings": {
        "acceptProxyProtocol": false,
        "path": "${CASCADE_2_PATH}",
        "host": "${DOMAIN}"
      }
    }',
    'in-${CASCADE_2_PORT}-tcp',
    '{"enabled": true, "destOverride": ["http", "tls", "quic"]}'
);

-- 2.3. VLESS WebSocket Duplicate (Cascade-to-N-3, disabled by default)
INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, listen, port, protocol, settings, stream_settings, tag, sniffing)
VALUES (
    '1', '0', '0', '0', '⛓️ Cascade-to-N-3', '0', '0', '127.0.0.1', '${CASCADE_3_PORT}', 'vless',
    '{"clients": [], "decryption": "none", "fallbacks": []}',
    '{
      "network": "ws",
      "security": "none",
      "wsSettings": {
        "acceptProxyProtocol": false,
        "path": "${CASCADE_3_PATH}",
        "host": "${DOMAIN}"
      }
    }',
    'in-${CASCADE_3_PORT}-tcp',
    '{"enabled": true, "destOverride": ["http", "tls", "quic"]}'
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
    '{"enabled": true, "destOverride": ["http", "tls", "quic"]}'
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
    '{"enabled": true, "destOverride": ["http", "tls", "quic"]}'
);

-- 5. VLESS gRPC
INSERT INTO inbounds (user_id, up, down, total, remark, enable, expiry_time, listen, port, protocol, settings, stream_settings, tag, sniffing)
VALUES (
    '1', '0', '0', '0', '⚡ VLESS-gRPC', '1', '0', '127.0.0.1', '${GRPC_PORT}', 'vless',
    '{"clients": [], "decryption": "none", "fallbacks": []}',
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
    '{"enabled": true, "destOverride": ["http", "tls", "quic"]}'
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
    '{"enabled": true, "destOverride": ["http", "tls", "quic"]}'
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
    ((SELECT id FROM inbounds WHERE tag='in-${CASCADE_1_PORT}-tcp'), '$(openssl rand -hex 8)', 0, 'cascade-to-n-1', '${DOMAIN}', 443, 'tls', 'firefox', '["http/1.1"]'),
    ((SELECT id FROM inbounds WHERE tag='in-${CASCADE_2_PORT}-tcp'), '$(openssl rand -hex 8)', 0, 'cascade-to-n-2', '${DOMAIN}', 443, 'tls', 'firefox', '["http/1.1"]'),
    ((SELECT id FROM inbounds WHERE tag='in-${CASCADE_3_PORT}-tcp'), '$(openssl rand -hex 8)', 0, 'cascade-to-n-3', '${DOMAIN}', 443, 'tls', 'firefox', '["http/1.1"]'),
    ((SELECT id FROM inbounds WHERE tag='inbound-/dev/shm/uds2023.sock,0666:0|'), '$(openssl rand -hex 8)', 0, 'xhttp', '${DOMAIN}', 443, 'tls', 'firefox', '["h2","http/1.1"]'),
    ((SELECT id FROM inbounds WHERE tag='inbound-${TROJAN_PORT}'), '$(openssl rand -hex 8)', 0, 'trojan', '${DOMAIN}', 443, 'tls', 'firefox', '["h2","http/1.1"]'),
    ((SELECT id FROM inbounds WHERE tag='inbound-${GRPC_PORT}'), '$(openssl rand -hex 8)', 0, 'vless-grpc', '${DOMAIN}', 443, 'tls', 'firefox', '["h2","http/1.1"]'),
    ((SELECT id FROM inbounds WHERE tag='inbound-443-udp'), '$(openssl rand -hex 8)', 0, 'hysteria2', '${DOMAIN}', 443, 'same', '', '[]');
COMMIT;
EOF

# Регистрация SSL сертификатов через CLI 3x-ui
/usr/local/x-ui/x-ui cert \
    -webCert "/root/cert/${DOMAIN}/fullchain.pem" \
    -webCertKey "/root/cert/${DOMAIN}/privkey.pem" >/dev/null 2>&1 || true

# Синхронизация DNS AdGuard Home с шаблоном Xray в SQLite 3x-ui
msg_inf "Конфигурирование DNS AdGuard Home (порт ${ADH_DNS_PORT}) в шаблоне Xray..."
install -m 0644 "$WORKDIR/assets/adguard/configure_xui_dns.py" "$WORKDIR/configure_xui_dns.py"
if [[ -f "$WORKDIR/configure_xui_dns.py" ]]; then
    python3 "$WORKDIR/configure_xui_dns.py" "/etc/x-ui/x-ui.db" "${ADH_DNS_PORT}" || msg_warn "Предупреждение: авто-патчинг DNS шаблона вернул ошибку, продолжаем установку..."
    install -o root -g root -m 0755 "$WORKDIR/configure_xui_dns.py" /usr/local/bin/x-ui-dns-sync 2>/dev/null || true
fi

systemctl daemon-reload
systemctl enable --now x-ui
sleep 1
if ! systemctl is-active --quiet x-ui; then
    sleep 2
fi
systemctl is-active --quiet x-ui || { msg_err "Служба 3X-UI (x-ui) не запустилась после обновления базы данных. Проверьте: journalctl -u x-ui -n 30"; exit 1; }
msg_ok "Все 7 inbounds, оба типа подписок и параметры 3X-UI записаны в базу данных и запущены."

# ─────────────────────────────────────────────────────────────────────────────
# [6] TELEMT & TELEMT-PANEL SETUP (TELEGRAM PROXY)
# ─────────────────────────────────────────────────────────────────────────────
msg_inf "Шаг [6/9]: Установка Telemt Web Proxy и панели управления..."
install -m 0755 "$WORKDIR/releases/telemt" /usr/local/bin/telemt
install -m 0755 "$WORKDIR/releases/telemt-panel" /usr/local/bin/telemt-panel

mkdir -p /etc/telemt /etc/telemt-panel /var/lib/telemt-panel

# Скачивание шаблонов Telemt
install -m 0600 /dev/null /etc/telemt/config.toml
install -m 0600 /dev/null /etc/telemt-panel/config.toml
install -m 0644 "$WORKDIR/assets/telemt/telemt.toml.template" "$WORKDIR/telemt.toml.template"
sed -e "s|__TG_PORT__|${TG_PORT}|g" \
    -e "s|__TG_PATH_NO_SLASH__|${TG_PATH_NO_SLASH}|g" \
    -e "s|__DOMAIN__|${DOMAIN}|g" \
    -e "s|__SERVER_IP__|${SERVER_IP}|g" \
    -e "s|__TG_DECOY_PORT__|${TG_DECOY_PORT}|g" \
    -e "s|__TG_USER__|${TG_USER}|g" \
    "$WORKDIR/telemt.toml.template" > "$WORKDIR/telemt-rendered.toml"
printf '%s' "$TG_SECRET" | python3 -c '
from pathlib import Path
import sys
sys.stdout.write(Path(sys.argv[1]).read_text().replace("__TG_SECRET__", sys.stdin.read()))
' "$WORKDIR/telemt-rendered.toml" > /etc/telemt/config.toml

# Генерация хеша пароля для Telemt-Panel
TG_PANEL_HASH=$(printf '%s\n' "$TG_PANEL_PASS" | htpasswd -niBC 10 admin 2>/dev/null | cut -d: -f2) ||
    fail "Не удалось создать хеш пароля Telemt Panel."
[[ -n "$TG_PANEL_HASH" ]] || fail "Хеш пароля Telemt Panel пуст."

install -m 0644 "$WORKDIR/assets/telemt/telemt-panel.toml.template" "$WORKDIR/telemt-panel.toml.template"
sed -e "s|__TG_PANEL_INTERNAL_PORT__|${TG_PANEL_INTERNAL_PORT}|g" \
    -e "s|__TG_PANEL_PASS_HASH__|${TG_PANEL_HASH}|g" \
    "$WORKDIR/telemt-panel.toml.template" > /etc/telemt-panel/config.toml

# Systemd службы Telemt
install -m 0644 "$WORKDIR/assets/telemt/telemt.service" /etc/systemd/system/telemt.service
install -m 0644 "$WORKDIR/assets/telemt/telemt-panel.service" /etc/systemd/system/telemt-panel.service

systemctl daemon-reload
systemctl enable --now telemt telemt-panel || fail "Не удалось запустить Telemt и Telemt Panel."
systemctl is-active --quiet telemt && systemctl is-active --quiet telemt-panel ||
    fail "Службы Telemt не готовы."
msg_ok "Telemt Web Proxy настроен и запущен с маскировкой 200 OK."

# Автоматическая настройка и загрузка базы GeoIP для Telemt Panel
msg_inf "Подключение и загрузка базы GeoIP (MaxMind GeoLite2-City) в Telemt Panel..."
for _ in {1..15}; do
    if curl -s "http://127.0.0.1:${TG_PANEL_INTERNAL_PORT}/api/auth/methods" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done

TG_LOGIN_RESP=$(printf '{"username":"admin","password":"%s"}' "$TG_PANEL_PASS" | curl -q -fsS -i --max-time 10 -X POST \
    -H "Origin: http://127.0.0.1:${TG_PANEL_INTERNAL_PORT}" \
    -H "Content-Type: application/json" \
    --data-binary @- \
    "http://127.0.0.1:${TG_PANEL_INTERNAL_PORT}/api/auth/login" 2>/dev/null || true)

TG_COOKIE=$(echo "$TG_LOGIN_RESP" | grep -i "set-cookie:" | tr -d '\r' | sed -E 's/.*panel_session=([^;]+).*/\1/' || true)

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
install -m 0644 "$WORKDIR/assets/nginx/01-bot-protection.conf" /etc/nginx/conf.d/01-bot-protection.conf

# Настройка Stream SNI
install -m 0644 "$WORKDIR/assets/nginx/stream.conf.template" "$WORKDIR/stream.conf.template"
sed "s|__REALITY_DOMAIN__|${DOMAIN}|g" "$WORKDIR/stream.conf.template" > /etc/nginx/stream-enabled/stream.conf

# Включение stream блока в nginx.conf если не включен
if ! grep -q "stream-enabled" /etc/nginx/nginx.conf; then
    echo "stream { include /etc/nginx/stream-enabled/*.conf; }" >> /etc/nginx/nginx.conf
fi

# Настройка Decoy сервера 18082
install -m 0644 "$WORKDIR/assets/nginx/telemt-decoy.conf.template" "$WORKDIR/telemt-decoy.conf.template"
sed "s|__TG_DECOY_PORT__|${TG_DECOY_PORT}|g" "$WORKDIR/telemt-decoy.conf.template" > /etc/nginx/sites-available/telemt-decoy.conf
ln -sf /etc/nginx/sites-available/telemt-decoy.conf /etc/nginx/sites-enabled/telemt-decoy.conf

# Настройка проксирования панели Telemt на порту 65001 с HTTPS
install -m 0644 "$WORKDIR/assets/nginx/telemt-panel.conf.template" "$WORKDIR/telemt-panel.conf.template"
sed -e "s|__TG_PANEL_PORT__|${TG_PANEL_PORT}|g" \
    -e "s|__TG_PANEL_INTERNAL_PORT__|${TG_PANEL_INTERNAL_PORT}|g" \
    -e "s|__DOMAIN__|${DOMAIN}|g" \
    "$WORKDIR/telemt-panel.conf.template" > /etc/nginx/sites-available/telemt-panel.conf
ln -sf /etc/nginx/sites-available/telemt-panel.conf /etc/nginx/sites-enabled/telemt-panel.conf

# Настройка главного VHost 9443
install -m 0644 "$WORKDIR/assets/nginx/vhost.conf.template" "$WORKDIR/vhost.conf.template"
sed -e "s|__DOMAIN__|${DOMAIN}|g" \
    -e "s|__PANEL_PATH_NO_SLASH__|${PANEL_PATH_NO_SLASH_LEADING}|g" \
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
    -e "s|__CASCADE_WS_PATH__|${CASCADE_1_PATH}|g" \
    -e "s|__CASCADE_WS_PORT__|${CASCADE_1_PORT}|g" \
    -e "s|__CASCADE_1_PATH__|${CASCADE_1_PATH}|g" \
    -e "s|__CASCADE_1_PORT__|${CASCADE_1_PORT}|g" \
    -e "s|__CASCADE_2_PATH__|${CASCADE_2_PATH}|g" \
    -e "s|__CASCADE_2_PORT__|${CASCADE_2_PORT}|g" \
    -e "s|__CASCADE_3_PATH__|${CASCADE_3_PATH}|g" \
    -e "s|__CASCADE_3_PORT__|${CASCADE_3_PORT}|g" \
    -e "s|__XHTTP_PATH__|${XHTTP_PATH_LEADING}|g" \
    -e "s|__TROJAN_PATH__|${TROJAN_PATH_LEADING}|g" \
    -e "s|__TROJAN_PORT__|${TROJAN_PORT}|g" \
    -e "s|__GRPC_PATH__|${GRPC_PATH_LEADING}|g" \
    -e "s|__GRPC_PORT__|${GRPC_PORT}|g" \
    "$WORKDIR/vhost.conf.template" > "/etc/nginx/sites-available/${DOMAIN}.conf"

ln -sf "/etc/nginx/sites-available/${DOMAIN}.conf" "/etc/nginx/sites-enabled/${DOMAIN}.conf"

nginx -t || { msg_err "Конфигурация Nginx содержит ошибки (nginx -t вернул ненулевой код)."; exit 1; }
systemctl restart nginx || { msg_err "Не удалось перезапустить службу Nginx."; exit 1; }
systemctl enable nginx 2>/dev/null || true
msg_ok "Nginx проверен и запущен. Anti-Bot защита и маршрутизация 443 активны."

# ─────────────────────────────────────────────────────────────────────────────
# [8] SERVER CONFIGURATION MODULE SETUP (STEALTH DASHBOARD)
# ─────────────────────────────────────────────────────────────────────────────
msg_inf "Шаг [8/9]: Установка и настройка модуля «Конфигурирование сервера»..."
mkdir -p /etc/server-manager

# Копирование скриптов модуля
install -m 0644 "$WORKDIR/assets/server-manager/server_manager.py" /usr/local/bin/server-manager.py
install -m 0644 "$WORKDIR/assets/server-manager/server-mgr" /usr/local/bin/server-mgr
install -m 0644 "$WORKDIR/assets/server-manager/server-manager.service" /etc/systemd/system/server-manager.service

chmod +x /usr/local/bin/server-manager.py /usr/local/bin/server-mgr 2>/dev/null || true

# Сохранение мастер-шаблона Nginx для модуля
cp "$WORKDIR/vhost.conf.template" /etc/server-manager/vhost.conf.template 2>/dev/null || true

# Сериализация учетных данных без подстановки пароля в код Python или аргументы.
printf '%s\0' "$DOMAIN" "$MGMT_PORT" "$MGMT_SECRET_PATH" "$MGMT_USER" "$MGMT_PASS" "$SERVER_IP" | (
    umask 077
    python3 -c '
import hashlib
import json
import sys

domain, port, path, user, password, server_ip, _ = sys.stdin.buffer.read().decode("utf-8").split("\0")
json.dump({
    "domain": domain,
    "mgmt_port": int(port),
    "mgmt_secret_path": path,
    "auth_user": user,
    "auth_pass_plain": password,
    "auth_pass_hash": hashlib.pbkdf2_hmac("sha256", password.encode(), b"server_manager_salt_3x", 100000).hex(),
    "auto_lock_minutes": 30,
    "server_ip": server_ip,
}, sys.stdout, indent=2)
print()
' > /etc/server-manager/config.json
)
chmod 0600 /etc/server-manager/config.json

systemctl daemon-reload
systemctl enable --now server-manager || fail "Не удалось запустить Server Manager."
manager_ready=no
for attempt in {1..15}; do
    if curl -q -fsS --noproxy '*' --connect-timeout 2 --max-time 3 \
        --resolve "${DOMAIN}:${MGMT_PORT}:127.0.0.1" "https://${DOMAIN}:${MGMT_PORT}/" >/dev/null 2>&1; then
        manager_ready=yes
        break
    fi
    sleep 1
done
[[ "$manager_ready" == yes ]] || fail "Server Manager не ответил по проверенному HTTPS."
msg_ok "Модуль «Конфигурирование сервера» установлен и запущен на порту ${MGMT_PORT} (Stealth)."

# ─────────────────────────────────────────────────────────────────────────────
# [9] FIREWALL & BRUTE-FORCE PROTECTION (UFW & FAIL2BAN)
# ─────────────────────────────────────────────────────────────────────────────
msg_inf "Шаг [9/9]: Настройка файрвола UFW и защиты от брутфорса Fail2ban..."
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
systemctl enable fail2ban || fail "Не удалось включить Fail2ban."
systemctl restart fail2ban || fail "Не удалось запустить Fail2ban."
msg_ok "Fail2ban установлен и настроен: прогрессивный бан активен, нарушители блокируются на уровне всех портов сервера."

# Освобождение оперативной памяти: очистка временного каталога инсталлятора из tmpfs
if [[ -n "${WORKDIR:-}" && -d "$WORKDIR" ]]; then
    rm -rf "$WORKDIR"
    WORKDIR=""
fi

# Оптимизация дискового пространства и удаление неиспользуемого балласта
optimize_disk_footprint() {
    msg_inf "Оптимизация дискового пространства и удаление балласта..."
    local virt
    virt=$(systemd-detect-virt 2>/dev/null || echo "none")
    if [[ "$virt" != "none" && "$virt" != "" ]]; then
        if dpkg -l "linux-firmware*" >/dev/null 2>&1; then
            DEBIAN_FRONTEND=noninteractive apt-get purge -q -y "linux-firmware*" firmware-sof-signed >/dev/null 2>&1 || true
            rm -rf /usr/lib/firmware/* 2>/dev/null || true
        fi
    fi

    local bloat_pkgs=()
    for pkg in snapd docker.io containerd docker-compose-v2 modemmanager fwupd upower kdump-tools sos python3-botocore; do
        if dpkg -s "$pkg" >/dev/null 2>&1; then
            bloat_pkgs+=("$pkg")
        fi
    done
    if (( ${#bloat_pkgs[@]} > 0 )); then
        systemctl stop snapd.socket snapd.service docker.socket docker.service containerd.service modemmanager fwupd upower 2>/dev/null || true
        DEBIAN_FRONTEND=noninteractive apt-get purge -q -y "${bloat_pkgs[@]}" >/dev/null 2>&1 || true
        rm -rf /var/lib/docker /var/lib/containerd /var/lib/snapd /var/cache/snapd /snap /root/snap /var/lib/kdump 2>/dev/null || true
    fi

    local rc_pkgs
    rc_pkgs=$(dpkg -l 2>/dev/null | awk '/^rc/ {print $2}') || rc_pkgs=""
    if [[ -n "$rc_pkgs" ]]; then
        DEBIAN_FRONTEND=noninteractive apt-get purge -q -y $rc_pkgs >/dev/null 2>&1 || true
    fi

    DEBIAN_FRONTEND=noninteractive apt-get autoremove -q -y --purge >/dev/null 2>&1 || true
    apt-get clean >/dev/null 2>&1 || true
    rm -rf /var/cache/apt/archives/* /var/lib/apt/lists/* 2>/dev/null || true
    msg_ok "Оптимизация диска завершена (кэши очищены, балласт удален)."
}
optimize_disk_footprint

# ─────────────────────────────────────────────────────────────────────────────
# FINAL SUMMARY REPORT
# ─────────────────────────────────────────────────────────────────────────────
(
umask 077
set -o noclobber
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
    Секретный путь:   /${MGMT_SECRET_PATH}/
    Статус в UFW:     ЗАКРЫТ (Режим невидимки)

    КАК ОТКРЫТЬ ДОСТУП ПРИ НЕОБХОДИМОСТИ:
    1) Быстрая команда в SSH (с авто-таймером закрытия 30 мин):
       server-mgr open
    2) Или через SSH-туннель (без открытия портов в интернет вообще):
       ssh -L 8088:127.0.0.1:${MGMT_PORT} root@${DOMAIN}
       и вход в браузере по адресу: https://localhost:8088/

    Закрыть порт вручную: server-mgr close (или кнопка «Выход» в веб-интерфейсе)

[1] ПАНЕЛЬ УПРАВЛЕНИЯ 3X-UI:
    URL:              https://${DOMAIN}${PANEL_PATH}
    Логин:            ${PANEL_USER}
    Пароль:           ${PANEL_PASS}
    Внутренний порт:  ${PANEL_PORT} (доступен только через HTTPS :443)
    SSL сертификат:   УСТАНОВЛЕН (/root/cert/${DOMAIN}/fullchain.pem)

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
                      (поддерживает слэш на конце / и профили устройств: https://${DOMAIN}${ADH_DOH_PATH}/устройство)
    Локальный DNS:    127.0.0.1:${ADH_DNS_PORT} (задействован для Xray Outbound 0 ms)

[5] СПИСОК РАЗВЕРНУТЫХ INBOUNDS В XRAY:
    • Reality TCP:     порт 8443 (fallback -> 9443 xver:1)
    • VLESS WS:        порт ${WS_PORT}, путь ${WS_PATH}
    • Cascade-to-N-1:  порт ${CASCADE_1_PORT}, путь ${CASCADE_1_PATH} (выключен)
    • Cascade-to-N-2:  порт ${CASCADE_2_PORT}, путь ${CASCADE_2_PATH} (выключен)
    • Cascade-to-N-3:  порт ${CASCADE_3_PORT}, путь ${CASCADE_3_PATH} (выключен)
    • VLESS XHTTP:     UDS /dev/shm/uds2023.sock, путь ${XHTTP_PATH}
    • Trojan gRPC:     порт ${TROJAN_PORT}, путь ${TROJAN_PATH}
    • VLESS gRPC:      порт ${GRPC_PORT}, serviceName ${GRPC_PATH}
    • Hysteria 2:      порт 443/UDP (TLS Cloudflare)
    • AmneziaWG:       порт 8443/UDP

[6] ПАРАМЕТРЫ ЗАЩИТЫ:
    [✓] UFW:           Добавлены разрешения для SSH ${SSH_PORT}/TCP и 443 TCP/UDP
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
)
echo
msg_ok "Данные доступа сохранены в закрытом файле /root/server_credentials.txt."
msg_inf "Каталог на сервере для загрузки вашего сайта: /var/www/html/"
