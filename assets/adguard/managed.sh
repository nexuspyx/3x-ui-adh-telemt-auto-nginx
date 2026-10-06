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

agh_binary() {
    [[ -f "$AGH_DIR/AdGuardHome" && ! -L "$AGH_DIR/AdGuardHome" && -x "$AGH_DIR/AdGuardHome" ]] || return 1
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
assert m['version'] == 'v0.107.79' and m['arch'] in ('amd64', 'arm64')
assert re.fullmatch(r'adg-[A-Za-z0-9]{12}', m['path'])
domain = m['domain']
assert isinstance(domain, str) and len(domain) <= 253 and '.' in domain
assert all(re.fullmatch(r'[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?', label) for label in domain.split('.'))
assert type(m['web_port']) is int and type(m['dns_port']) is int
assert 10000 <= m['web_port'] <= 65535 and 10000 <= m['dns_port'] <= 65535
assert m['web_port'] != m['dns_port']
# Read only managed scalar/list fields in the canonical YAML written by AGH.
# Unrecognized representations fail closed; native --check-config handles YAML.
s = (root / 'AdGuardHome.yaml').read_text()
def section(name):
    match = re.search(r'^' + name + r':\s*\n((?:[ \t].*\n|\n)*)', s, re.M)
    assert match, name
    return match[1]
def scalar(text, key):
    matches = re.findall(r'^  ' + key + r':\s*(.*?)\s*$', text, re.M)
    assert len(matches) == 1, key
    return matches[0].strip('"\'')
assert re.search(r'^schema_version: 34\s*$', s, re.M)
http, dns, tls = section('http'), section('dns'), section('tls')
assert scalar(http, 'address') == '127.0.0.1:' + str(m['web_port'])
assert scalar(dns, 'port') == str(m['dns_port'])
hosts = re.search(r'^  bind_hosts:\s*\n((?:    - .*\n)+)', dns, re.M)
assert hosts and [x.strip().strip('"\'') for x in re.findall(r'^    - (.*)$', hosts[1], re.M)] == ['127.0.0.1']
assert scalar(tls, 'enabled') == 'false'
assert re.search(r'^    insecure_enabled: true\s*$', http, re.M)
for part in ('querylog', 'statistics'):
    p = section(part) if re.search(r'^' + part + ':', s, re.M) else ''
    if p and re.search(r'^  dir_path:', p, re.M):
        directory = scalar(p, 'dir_path')
        assert not directory or pathlib.Path(directory).resolve().is_relative_to(data_root.resolve())
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
assert len(lines) == 1
args = shlex.split(lines[0].split('=', 1)[1])
assert args[0] == root + '/AdGuardHome'
# Upstream service serializer uses long names and adds -s/--service run.
def value(short, long):
    key = long if long in args else short
    return args[args.index(key) + 1]
assert value('-c', '--config') == root + '/AdGuardHome.yaml'
assert value('-w', '--work-dir') == root
assert '--no-check-update' in args
assert value('-s', '--service') == 'run'
assert len(args) == 8, args
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
assert owned
assert all(l[4].startswith('127.0.0.1:') for l in owned)
assert {l[4] for l in owned} == {'127.0.0.1:' + web, '127.0.0.1:' + dns}
assert any(l[0] == 'tcp' and l[4].endswith(':' + web) for l in owned)
assert any(l[0] == 'udp' and l[4].endswith(':' + dns) for l in owned)
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
assert len(packet) >= 12
_, flags, questions, answers, _, _ = struct.unpack('!6H', packet[:12])
assert flags & 0x8000 and not flags & 15 and questions == 1 and answers > 0
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
