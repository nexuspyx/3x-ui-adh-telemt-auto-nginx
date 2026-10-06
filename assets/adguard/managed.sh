#!/usr/bin/env bash
# Shared integrated AdGuard Home contract (installer and Backup v3).
AGH_VERSION=v0.107.79
AGH_DIR=/opt/AdGuardHome
AGH_SNIPPET=/etc/nginx/snippets/x-ui-auto-optional/adguard.conf

agh_release() {
    case "$1" in
        amd64|x86_64) AGH_ARCH=amd64; AGH_SHA=c48f4a43000665484c5ec28177de11a004759b620dae8f77b2aabefc9ef3687f ;;
        arm64|aarch64) AGH_ARCH=arm64; AGH_SHA=3f7893c18e8aaadc456d0452839190561c306ca95175a2254958be80a769c1ae ;;
        *) return 1 ;;
    esac
}

# SHA256 of executables extracted from the audited v0.107.79 release archives.
agh_binary_hash() {
    case "$1" in
        amd64|x86_64) printf '%s\n' 7e247573e63ce771a5925d16ca4ca9344e6e888673244289dc302f0fdfdfbf4e ;;
        arm64|aarch64) printf '%s\n' 64a9b6fc6269247f1973cddbf285aa6ce866d11bd29546b0f4135ba31d2283c8 ;;
        *) return 1 ;;
    esac
}

agh_verify_binary() {
    local expected actual
    [[ -f "$AGH_DIR/AdGuardHome" && ! -L "$AGH_DIR/AdGuardHome" && -x "$AGH_DIR/AdGuardHome" ]] || return 1
    expected=$(agh_binary_hash "$1") || return 1
    actual=$(sha256sum "$AGH_DIR/AdGuardHome") || return 1
    [[ "${actual%% *}" == "$expected" ]]
}

agh_binary() {
    agh_verify_binary "${AGH_ARCH:-$(uname -m)}" || return 1
    [[ "$("$AGH_DIR/AdGuardHome" --version)" == "AdGuard Home, version $AGH_VERSION" ]]
}

agh_config() {
    [[ -d "$AGH_DIR" && ! -L "$AGH_DIR" && -f "$AGH_DIR/AdGuardHome.yaml" && ! -L "$AGH_DIR/AdGuardHome.yaml" &&
       -f "$AGH_DIR/managed.json" && ! -L "$AGH_DIR/managed.json" ]] || return 1
    local values
    values=$(python3 - "$AGH_DIR" "${AGH_DATA_ROOT:-$AGH_DIR}" <<'PY'
import json, pathlib, re, sys
root = pathlib.Path(sys.argv[1])
data_root = pathlib.Path(sys.argv[2])
m = json.loads((root / 'managed.json').read_text())
if not (m['version'] == 'v0.107.79' and m['arch'] in ('amd64', 'arm64')):
    raise ValueError('Invalid AGH version/architecture')
if not (re.fullmatch(r'adg-[A-Za-z0-9]{12}', m['path'])):
    raise ValueError('Invalid AGH admin prefix')
domain = m['domain']
if not (isinstance(domain, str) and len(domain) <= 253 and '.' in domain):
    raise ValueError('Invalid AGH domain')
if not (all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in domain.split('.'))):
    raise ValueError('Invalid AGH domain labels')
if not (type(m['web_port']) is int and type(m['dns_port']) is int):
    raise ValueError('Invalid AGH integer ports')
if not (10000 <= m['web_port'] <= 65535 and 10000 <= m['dns_port'] <= 65535):
    raise ValueError('Invalid AGH high ports')
if not (m['web_port'] != m['dns_port']):
    raise ValueError('Invalid AGH distinct ports')
# Read only managed scalar/list fields in the canonical YAML written by AGH.
# Unrecognized representations fail closed; native --check-config handles YAML.
s = (root / 'AdGuardHome.yaml').read_text()
def section(name):
    match = re.search(r'^' + name + r':\s*\n((?:[ \t].*\n|\n)*)', s, re.M)
    if not (match):
        raise ValueError('Invalid AGH required YAML section')
    return match[1]
def scalar(text, key):
    matches = re.findall(r'^  ' + key + r':\s*(.*?)\s*$', text, re.M)
    if not (len(matches) == 1):
        raise ValueError('Invalid AGH unique YAML field')
    return matches[0].strip('"\'')
if not (re.search(r'^schema_version: 34\s*$', s, re.M)):
    raise ValueError('Invalid AGH schema 34')
http, dns, tls = section('http'), section('dns'), section('tls')
if not (scalar(http, 'address') == '127.0.0.1:' + str(m['web_port'])):
    raise ValueError('Invalid AGH loopback HTTP address')
if not (scalar(dns, 'port') == str(m['dns_port'])):
    raise ValueError('Invalid AGH DNS port')
hosts = re.search(r'^  bind_hosts:\s*\n((?:    - .*\n)+)', dns, re.M)
if not (hosts and [x.strip().strip('"\'') for x in re.findall(r'^    - (.*)$', hosts[1], re.M)] == ['127.0.0.1']):
    raise ValueError('Invalid AGH loopback DNS bind')
if not (scalar(tls, 'enabled') == 'false'):
    raise ValueError('Invalid AGH native TLS disabled')
if not (re.search(r'^    insecure_enabled: true\s*$', http, re.M)):
    raise ValueError('Invalid AGH DoH insecure bridge')
for part in ('querylog', 'statistics'):
    p = section(part) if re.search(r'^' + part + ':', s, re.M) else ''
    if p and re.search(r'^  dir_path:', p, re.M):
        directory = scalar(p, 'dir_path')
        if not (not directory or pathlib.Path(directory).resolve().is_relative_to(data_root.resolve())):
            raise ValueError('Invalid AGH managed data directory')
for k in ('web_port', 'dns_port', 'path', 'domain', 'arch'):
    print(m[k])
PY
    ) || return 1
    local -a fields
    mapfile -t fields <<< "$values"
    AGH_WEB_PORT=${fields[0]} AGH_DNS_PORT=${fields[1]} AGH_PATH=${fields[2]} AGH_DOMAIN=${fields[3]} AGH_ARCH=${fields[4]}
    agh_binary && "$AGH_DIR/AdGuardHome" -c "$AGH_DIR/AdGuardHome.yaml" -w "$AGH_DIR" --no-check-update --check-config
}

agh_unit() {
    local unit
    unit=$(systemctl cat AdGuardHome) || return 1
    grep -Eq '^ExecStart=' <<< "$unit" || return 1
    python3 - "$AGH_DIR" "$unit" <<'PY'
import shlex, sys
root, unit = sys.argv[1:]
lines = [l for l in unit.splitlines() if l.startswith('ExecStart=')]
if not (len(lines) == 1):
    raise ValueError('Invalid AGH unique ExecStart')
args = shlex.split(lines[0].split('=', 1)[1])
if not (args[0] == root + '/AdGuardHome'):
    raise ValueError('Invalid AGH service executable')
# Upstream service serializer uses long names and adds -s/--service run.
def value(short, long):
    key = long if long in args else short
    return args[args.index(key) + 1]
if not (value('-c', '--config') == root + '/AdGuardHome.yaml'):
    raise ValueError('Invalid AGH explicit config file')
if not (value('-w', '--work-dir') == root):
    raise ValueError('Invalid AGH explicit work directory')
if not ('--no-check-update' in args):
    raise ValueError('Invalid AGH disabled update check')
if not (value('-s', '--service') == 'run'):
    raise ValueError('Invalid AGH service run action')
if not (len(args) == 8):
    raise ValueError('Invalid AGH ExecStart arguments')
PY
}

agh_install_service() {
    local attempt
    agh_config || return 1
    "$AGH_DIR/AdGuardHome" -c "$AGH_DIR/AdGuardHome.yaml" -w "$AGH_DIR" --no-check-update -s install &&
        systemctl daemon-reload && agh_unit && systemctl enable --now AdGuardHome &&
        systemctl is-active --quiet AdGuardHome || return 1
    # systemd start may return before the application has bound its sockets.
    for attempt in {1..20}; do
        agh_listeners 2>/dev/null && return 0
        sleep 0.25
    done
    return 1
}

agh_snippet() {
    cat <<EOFNG
# Integrated AdGuard Home (project-owned).
location = /dns-query {
    limit_except GET POST { deny all; }
    proxy_pass http://127.0.0.1:${AGH_WEB_PORT};
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$remote_addr;
    proxy_set_header X-Forwarded-Proto https;
    proxy_buffering off;
    proxy_intercept_errors off;
    access_log off;
}
location = /${AGH_PATH} { return 302 /${AGH_PATH}/; }
location ^~ /${AGH_PATH}/ {
    proxy_pass http://127.0.0.1:${AGH_WEB_PORT}/;
    proxy_redirect / /${AGH_PATH}/;
    proxy_cookie_path / /${AGH_PATH}/;
    proxy_http_version 1.1;
    proxy_set_header Host \$host;
    proxy_set_header X-Real-IP \$remote_addr;
    proxy_set_header X-Forwarded-For \$remote_addr;
    proxy_set_header X-Forwarded-Proto https;
    proxy_intercept_errors off;
    add_header X-Robots-Tag "noindex, nofollow" always;
}
EOFNG
}

agh_listeners() {
    local pid listeners
    pid=$(systemctl show -p MainPID --value AdGuardHome) || return 1
    [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
    listeners=$(ss -H -lntup) || return 1
    python3 - "$pid" "$AGH_WEB_PORT" "$AGH_DNS_PORT" "$listeners" <<'PY'
import sys
pid, web, dns, listing = sys.argv[1:]
owned = [l.split() for l in listing.splitlines() if 'pid=' + pid + ',' in l]
if not (owned):
    raise ValueError('Invalid AGH owned listeners')
if not (all(l[4].startswith('127.0.0.1:') for l in owned)):
    raise ValueError('Invalid AGH loopback listeners')
if not ({l[4] for l in owned} == {'127.0.0.1:' + web, '127.0.0.1:' + dns}):
    raise ValueError('Invalid AGH listener endpoints')
if not (any(l[0] == 'tcp' and l[4].endswith(':' + web) for l in owned)):
    raise ValueError('Invalid AGH TCP web socket')
if not (any(l[0] == 'udp' and l[4].endswith(':' + dns) for l in owned)):
    raise ValueError('Invalid AGH UDP DNS socket')
PY
}

agh_doh_probe() {
    local url=$1 resolve=${2:-} directory result
    directory=$(mktemp -d) || return 1
    local -a options=()
    [[ -z "$resolve" ]] || options+=(--resolve "$resolve")
    result=$(curl --noproxy '*' -fsS --connect-timeout 5 --max-time 15 "${options[@]}" \
        -H 'Accept: application/dns-message' -D "$directory/headers" -o "$directory/body" -w '%{http_code}' \
        "${url}?dns=AAABAAABAAAAAAAAA3d3dwdleGFtcGxlA2NvbQAAAQAB") || { rm -rf "$directory"; return 1; }
    [[ "$result" == 200 ]] && grep -iqE '^Content-Type: application/dns-message\s*' "$directory/headers" &&
        python3 - "$directory/body" <<'PY'
import pathlib, struct, sys
packet = pathlib.Path(sys.argv[1]).read_bytes()
if not (len(packet) >= 12):
    raise ValueError('Invalid AGH DNS response length')
_, flags, questions, answers, _, _ = struct.unpack('!6H', packet[:12])
if not (flags & 0x8000 and not flags & 15 and questions == 1 and answers > 0):
    raise ValueError('Invalid AGH successful DNS response')
PY
    result=$?
    rm -rf "$directory"
    return "$result"
}

agh_health() {
    agh_config && agh_unit && systemctl is-active --quiet AdGuardHome && agh_listeners || return 1
    local expected body backend
    expected=$(agh_snippet) || return 1
    [[ -f "$AGH_SNIPPET" && ! -L "$AGH_SNIPPET" && "$(cat "$AGH_SNIPPET")" == "$expected" ]] || return 1
    agh_doh_probe "http://127.0.0.1:${AGH_WEB_PORT}/dns-query" || return 1
    agh_doh_probe "https://${AGH_DOMAIN}/dns-query" "${AGH_DOMAIN}:443:127.0.0.1" || return 1
    backend=$(curl --noproxy '*' -fsS --connect-timeout 5 --max-time 15 "http://127.0.0.1:${AGH_WEB_PORT}/login.html") || return 1
    body=$(curl --noproxy '*' -fsS --connect-timeout 5 --max-time 15 \
        --resolve "${AGH_DOMAIN}:443:127.0.0.1" "https://${AGH_DOMAIN}/${AGH_PATH}/login.html") || return 1
    [[ "$body" == "$backend" && "$body" == *'<html'* && "$body" == *'src="login.'* ]]
}
