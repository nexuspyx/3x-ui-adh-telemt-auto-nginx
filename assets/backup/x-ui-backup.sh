#!/usr/bin/env bash
# Managed 3x-ui Auto Nginx backup: same-host rollback or clean-host recovery, same OS/arch.
# Usage: x-ui-backup {backup|restore <archive>|list}
set -Eeuo pipefail
umask 077

BACKUP_STORE=/var/backups/x-ui
DB=/etc/x-ui/x-ui.db
XRAY_DIR=/usr/local/x-ui/bin
SYSCTL_FILE=/etc/sysctl.d/99-3x-ui-pro.conf
XHTTP_SOCKET=/dev/shm/uds2023.sock
LEGACY_CERTBOT_CRON='@monthly certbot renew --non-interactive --pre-hook "systemctl stop nginx" --post-hook "systemctl start nginx" > /dev/null 2>&1'
PACKAGES=(nginx-full certbot sqlite3 curl wget jq ufw
          netcat-openbsd mtr python3 python3-configobj python3-cryptography libcap2-bin ca-certificates openssl procps iproute2 tar gzip tzdata)
RUNTIME_PATHS=(/etc/x-ui /usr/local/x-ui /usr/bin/x-ui)
TREE_PATHS=(/opt/AdGuardHome /etc/nginx /etc/letsencrypt /root/cert /usr/local/lib/3x-ui-pro
            /var/www/html /var/www/subpage)
EXTRA_PATHS=(/etc/systemd/system/x-ui.service /etc/systemd/system/mtr-backend.service
             /var/www/diagnostics/index.html /var/www/diagnostics/speedtest.js
             /var/www/diagnostics/speedtest_worker.js "$SYSCTL_FILE")
REQUIRED_PATHS=("$DB" /usr/local/x-ui/x-ui /usr/bin/x-ui /etc/nginx/nginx.conf
                /etc/letsencrypt /etc/letsencrypt/renewal-hooks/deploy/3x-ui-auto-nginx /usr/local/lib/3x-ui-pro/mtr-backend.py
                /var/www/html /var/www/subpage "${EXTRA_PATHS[@]:0:5}")
STAGING= OUTPUT= STAGE=preflight
BACKUP_FINISHED=0 RESUME_XUI=0 APT_UPDATED=0
RESUME_CERTBOT_TIMER=0
RESUME_AGH=0 ADGUARD_HOME=false AGH_ARCH=

stage() { STAGE=$*; printf '\n==> %s\n' "$*"; }
ok()    { printf '[OK] %s\n' "$*"; }
warn()  { printf '[WARN] %s\n' "$*" >&2; }
die()   { printf '[FAIL] %s\n' "$*" >&2; exit 1; }
require_root() { [[ $EUID -eq 0 ]] || die 'Run x-ui-backup as root.'; }

cleanup() {
    local result=$?
    trap - EXIT ERR
    if (( RESUME_XUI )); then
        if systemctl start x-ui && systemctl is-active --quiet x-ui; then
            ok 'x-ui returned to its original active state after failure'
        else
            printf '[FAIL] Cannot recover x-ui; run systemctl start x-ui.\n' >&2
            result=1
        fi
    fi
    if (( RESUME_AGH )); then
        systemctl start AdGuardHome && systemctl is-active --quiet AdGuardHome || { warn 'Cannot recover AdGuardHome; run systemctl start AdGuardHome.'; result=1; }
    fi
    resume_certbot_timer || { warn 'Cannot restore certbot.timer; run systemctl start certbot.timer.'; result=1; }
    if [[ -n "$OUTPUT" ]] && (( ! BACKUP_FINISHED )); then
        rm -f -- "$OUTPUT" || { warn 'Cannot remove incomplete archive'; result=1; }
    fi
    if [[ -n "$STAGING" ]]; then
        rm -rf -- "$STAGING" || { warn 'Cannot remove private staging directory'; result=1; }
    fi
    exit "$result"
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

load_adguard_backup_state() {
    ADGUARD_HOME=false
    if [[ -e /opt/AdGuardHome || -e /etc/systemd/system/AdGuardHome.service ||
          -e /etc/nginx/snippets/adguard.conf || -e /etc/nginx/snippets/x-ui-auto-optional/adguard.conf ||
          -e /usr/local/lib/3x-ui-pro/managed-adguard.sh ]] ||
       systemctl is-active --quiet AdGuardHome 2>/dev/null || systemctl is-enabled --quiet AdGuardHome 2>/dev/null ||
       grep -RqE 'location.*(/adg-|/dns-query)' /etc/nginx/sites-available /etc/nginx/snippets 2>/dev/null; then
        [[ -f /usr/local/lib/3x-ui-pro/managed-adguard.sh && ! -L /usr/local/lib/3x-ui-pro/managed-adguard.sh &&
           -f /etc/nginx/snippets/x-ui-auto-optional/adguard.conf && ! -e /etc/nginx/snippets/adguard.conf ]] || die 'Partial AdGuard Home state.'
        . /usr/local/lib/3x-ui-pro/managed-adguard.sh
        agh_config && agh_unit || die 'AdGuard Home configuration/service contract is invalid.'
        [[ "$(cat "$AGH_SNIPPET")" == "$(agh_snippet)" ]] || die 'AdGuard Home nginx state is inconsistent.'
        [[ "$AGH_ARCH" == "$ARCH" ]] || die 'AdGuard Home architecture does not match the host.'
        ADGUARD_HOME=true
    fi
}

check_python_dependencies() {
    python3 -c 'import configobj, cryptography' || die 'Required Python modules are unavailable: configobj, cryptography.'
}

pause_certbot_timer() {
    local timer_state service_state
    timer_state=$(systemctl is-active certbot.timer) || :
    case "$timer_state" in
        active) RESUME_CERTBOT_TIMER=1 ;;
        inactive|failed) ;;
        *) die 'Cannot determine certbot.timer state; retry after checking systemd.' ;;
    esac
    systemctl stop certbot.timer || die 'Cannot pause certbot.timer.'
    service_state=$(systemctl is-active certbot.service) || :
    case "$service_state" in
        inactive|failed) ;;
        *) die 'Certbot renewal is running or its state is unknown. No certificate state was copied or replaced; retry after it finishes.' ;;
    esac
}

resume_certbot_timer() {
    (( RESUME_CERTBOT_TIMER )) || return 0
    systemctl start certbot.timer && systemctl is-active --quiet certbot.timer || return 1
    RESUME_CERTBOT_TIMER=0
}

prepare_store() {
    [[ ! -L "$BACKUP_STORE" ]] || die 'Backup store must not be a symlink.'
    mkdir -p "$BACKUP_STORE"
    chown root:root "$BACKUP_STORE"
    chmod 0700 "$BACKUP_STORE"
    STAGING=$(mktemp -d "$BACKUP_STORE/.${1}-XXXXXX")
}

host_identity() {
    # os-release is trusted local OS state, never taken from the archive.
    . /etc/os-release
    OS_ID=$ID OS_VERSION=$VERSION_ID
    ARCH=$(dpkg --print-architecture)
    case "$OS_ID:$OS_VERSION" in
        ubuntu:24.04|ubuntu:26.04|debian:13) ;;
        *) die 'Supported OS: Ubuntu 24.04/26.04 or Debian 13.' ;;
    esac
}

install_missing_packages() {
    local package status missing=()
    for package in "$@"; do
        status=$(dpkg-query -W -f='${Status}' "$package" 2>/dev/null) || status=
        [[ "$status" == 'install ok installed' ]] || missing+=("$package")
    done
    if (( ${#missing[@]} )); then
        stage "Installing missing dependencies: ${missing[*]}"
        if (( ! APT_UPDATED )); then
            apt-get update -qq || die 'Cannot update package lists; managed files have not been replaced.'
            APT_UPDATED=1
        fi
        DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}" ||
            die 'Dependency installation failed; managed files have not been replaced.'
    fi
}

quick_check() {
    local result
    [[ -f "$1" && ! -L "$1" ]] || die 'SQLite database is missing or is a symlink.'
    result=$(sqlite3 "$1" 'PRAGMA quick_check;' 2>/dev/null) || die 'SQLite quick_check failed.'
    [[ "$result" == ok ]] || die 'SQLite quick_check found database corruption.'
    ok 'SQLite database is consistent'
}

# Validate before extraction. Only managed paths and their parent directories are
# accepted; links cannot redirect extraction or target arbitrary OS/security files.
validate_archive() {
    gzip -t -- "$1" || die 'Archive gzip integrity check failed.'
    python3 - "$1" "$DB" "$XRAY_DIR" "${REQUIRED_PATHS[@]}" -- \
        "${RUNTIME_PATHS[@]:0:2}" "${TREE_PATHS[@]}" -- \
        /usr/bin/x-ui "${EXTRA_PATHS[@]}" <<'PY'
import ipaddress, json, posixpath, sys, tarfile
from pathlib import PurePosixPath
archive, db, xray_dir, *paths = sys.argv[1:]
split = paths.index('--')
required, paths = paths[:split], paths[split + 1:]
split = paths.index('--')
trees = [p.lstrip('/') for p in paths[:split]]
files = [p.lstrip('/') for p in paths[split + 1:]]
allowed = trees + files
required = ['files/' + p.lstrip('/') for p in required]
def fail(message):
    raise ValueError(message)
def clean(name):
    if name.startswith('./'):
        name = name[2:]
    name = name.rstrip('/')
    if not name or name.startswith('/') or any(ord(c) < 32 for c in name):
        fail('Unsafe archive member name')
    if any(p in ('', '.', '..') for p in name.split('/')):
        fail('Unsafe archive member path')
    return name
def managed(path):
    return path in files or any(path == p or path.startswith(p + '/') for p in trees)
def permitted(name, directory):
    if name == 'meta.json':
        return not directory
    if name == 'files':
        return directory
    if not name.startswith('files/'):
        return False
    path = name[6:]
    return managed(path) or (directory and any(p.startswith(path + '/') for p in allowed))
try:
    with tarfile.open(archive, 'r:gz') as tar:
        metadata = tar.getmember('meta.json')
        if not metadata.isfile() or metadata.size > 16384:
            fail('Invalid metadata file')
        meta = json.load(tar.extractfile(metadata))
        if isinstance(meta, dict) and type(meta.get('format_version')) is int and meta['format_version'] == 2:
            fail('Legacy backup format 2 is not supported by this release. Restore it with the matching older x-ui-backup version.')
        if not isinstance(meta, dict):
            fail('Metadata must be an object')
        if type(meta.get('adguard_home')) is not bool:
            fail('Missing boolean adguard_home metadata')
        agh_tree = '/opt/AdGuardHome'.lstrip('/')
        agh_helper = '/usr/local/lib/3x-ui-pro/managed-adguard.sh'.lstrip('/')
        agh_snippet = '/etc/nginx/snippets/x-ui-auto-optional/adguard.conf'.lstrip('/')
        if meta['adguard_home']:
            if meta.get('adguard_version') != 'v0.107.79' or meta.get('adguard_arch') not in ('amd64', 'arm64') or meta['adguard_arch'] != meta.get('arch'):
                fail('Invalid AdGuard Home release metadata')
            required += ['files/' + p for p in (agh_tree, agh_tree + '/AdGuardHome', agh_tree + '/AdGuardHome.yaml', agh_tree + '/managed.json', agh_helper, agh_snippet)]
        members = {}
        for member in tar:
            name = clean(member.name)
            if not meta['adguard_home'] and (name == 'files/' + agh_tree or name.startswith('files/' + agh_tree + '/') or name in ('files/' + agh_helper, 'files/' + agh_snippet)):
                fail('AdGuard Home state prohibited when metadata is false')
            if name.startswith('files/' + agh_tree + '/') and not (member.isfile() or member.isdir()):
                fail('AdGuard Home tree cannot contain archive links')
            if name in ('files/' + agh_helper, 'files/' + agh_snippet) and not member.isfile():
                fail('AdGuard Home helper/snippet must be regular files')
            if name == 'files/etc/systemd/system/AdGuardHome.service':
                fail('AdGuard Home service must be recreated, not archived')
            if name in members or not permitted(name, member.isdir()):
                fail('Unexpected or duplicate archive member')
            if name.startswith('files/') and name[6:] in files and not member.isfile():
                fail('Managed files must be regular files')
            if name.startswith('files/') and name[6:] in trees and not member.isdir():
                fail('Managed trees must be directories')
            if not (member.isdir() or member.isfile() or member.issym() or member.islnk()):
                fail('Special files are not allowed in backups')
            members[name] = member
        for name, member in members.items():
            for parent in PurePosixPath(name).parents:
                other = members.get(str(parent))
                if other and not other.isdir():
                    fail('Archive member traverses a non-directory')
            if member.issym():
                if not name.startswith('files/') or name[6:] in allowed:
                    fail('Managed roots must not be symbolic links')
                if any(ord(c) < 32 for c in member.linkname):
                    fail('Unsafe symbolic link')
                target = posixpath.normpath(posixpath.join('/' + posixpath.dirname(name[6:]), member.linkname))
                if not (managed(target.lstrip('/')) or target.startswith('/usr/share/nginx/modules-available/')
                        or target.startswith('/usr/lib/nginx/modules/')):
                    fail('Symbolic link points outside managed state')
            if member.islnk():
                target = members.get(clean(member.linkname))
                if not target or not target.isfile():
                    fail('Invalid hard link')
        for name in ['meta.json', *required]:
            if name not in members:
                fail('Archive is missing required managed state')
            if name in required and not (members[name].isdir() if name[6:] in trees else members[name].isfile()):
                fail('Required managed state has the wrong file type')
        for name in ('meta.json', 'files/' + db.lstrip('/')):
            if not members[name].isfile():
                fail('Metadata and DB must be regular files')
        xray_prefix = 'files/' + xray_dir.lstrip('/') + '/xray-linux-'
        if not any(name.startswith(xray_prefix) and member.isfile() and member.mode & 0o111
                   for name, member in members.items()):
            fail('Archive is missing the Xray executable')
        if members['meta.json'].size > 16384:
            fail('Unexpected metadata size')
        meta = json.load(tar.extractfile(members['meta.json']))
        if not isinstance(meta, dict):
            fail('Metadata must be an object')
        keys = ('created', 'hostname', 'os_id', 'os_version', 'arch', 'x_ui_version', 'xray_version', 'source_ipv4')
        if type(meta.get('format_version')) is not int or meta['format_version'] != 3:
            fail('Unsupported backup format; expected format_version=3')
        if any(not isinstance(meta.get(k), str) for k in keys):
            fail('Incomplete backup metadata')
        if meta['source_ipv4']:
            ipaddress.IPv4Address(meta['source_ipv4'])
except (ValueError, OSError, tarfile.TarError, KeyError, UnicodeError) as exc:
    print('[FAIL] Invalid or unsafe backup archive: ' + str(exc), file=sys.stderr)
    sys.exit(1)
PY
}

current_ipv4() {
    local candidate octet valid=1
    candidate=$(ip -4 route get 8.8.8.8 2>/dev/null | awk '{for(i=1;i<NF;i++) if($i=="src") {print $(i+1); exit}}') || candidate=
    if [[ ! "$candidate" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]]; then
        candidate=$(curl -4fsS --connect-timeout 5 --max-time 10 https://ipv4.icanhazip.com 2>/dev/null) || candidate=
    fi
    [[ "$candidate" =~ ^([0-9]{1,3}\.){3}[0-9]{1,3}$ ]] || return 1
    for octet in ${candidate//./ }; do
        (( 10#$octet <= 255 )) || valid=0
    done
    (( valid )) || return 1
    printf '%s\n' "$candidate"
}

collect_path() {
    local path=$1
    if [[ -e "$path" || -L "$path" ]]; then
        mkdir -p "$STAGING/files$(dirname "$path")"
        cp -a -- "$path" "$STAGING/files$path"
    fi
}

write_metadata() {
    local source_ip xui_version xray_version xray
    source_ip=$(current_ipv4) || { source_ip=; warn 'Cannot detect source IPv4; diagnostics IP migration will be skipped.'; }
    xui_version=$(/usr/local/x-ui/x-ui -v 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1) || xui_version=unknown
    xray_version=unknown
    for xray in "$XRAY_DIR"/xray-linux-*; do
        [[ -x "$xray" ]] || continue
        xray_version=$("$xray" version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' | head -n1) || xray_version=unknown
        break
    done
    python3 - "$STAGING/meta.json" "$OS_ID" "$OS_VERSION" "$ARCH" "$xui_version" "$xray_version" "$source_ip" "$ADGUARD_HOME" "$AGH_ARCH" <<'PY'
import datetime, json, socket, sys
path, os_id, os_version, arch, x_ui, xray, ipv4, agh, agh_arch = sys.argv[1:]
with open(path, 'w') as out:
    json.dump(dict(format_version=3, created=datetime.datetime.now(datetime.timezone.utc).isoformat(),
                   hostname=socket.gethostname(), os_id=os_id, os_version=os_version, arch=arch,
                   x_ui_version=x_ui, xray_version=xray, source_ipv4=ipv4, adguard_home=(agh == "true"),
                   **(dict(adguard_version="v0.107.79", adguard_arch=agh_arch) if agh == "true" else {})), out, indent=2)
    out.write('\n')
PY
}

cmd_backup() {
    stage 'Backup preflight'
    host_identity
    for path in "${REQUIRED_PATHS[@]}"; do
        [[ -e "$path" ]] || die "Required managed path is missing: $path"
    done
    command -v python3 >/dev/null && command -v sqlite3 >/dev/null || die 'Backup requires python3 and sqlite3.'
    check_python_dependencies
    local domains
    local -a names
    domains=$(restored_certificate_domains) || die 'Cannot detect project certificate domains.'
    mapfile -t names <<< "$domains"
    check_certificate_renewal "${names[0]}" "${names[1]}" || die 'Certificate renewal preflight failed; no archive was created.'
    load_adguard_backup_state
    pause_certbot_timer
    prepare_store backup
    local estimate available path initial_state existing=()
    for path in "${RUNTIME_PATHS[@]}" "${TREE_PATHS[@]}" "${EXTRA_PATHS[@]}"; do
        [[ ! -e "$path" && ! -L "$path" ]] || existing+=("$path")
    done
    estimate=$(du -skc -- "${existing[@]}" | tail -n1 | awk '{print $1}')
    available=$(df -Pk "$BACKUP_STORE" | awk 'NR==2 {print $4}')
    (( available >= estimate * 2 + 102400 )) || die 'Insufficient space for private staging and archive.'
    initial_state=$(systemctl is-active x-ui) || :
    case "$initial_state" in
        active)
            RESUME_XUI=1
            systemctl stop x-ui || die 'Cannot stop x-ui for a consistent snapshot.'
            ;;
        inactive|failed) ;;
        *) die 'x-ui is in a transitional or unknown state; retry when it is active or inactive.' ;;
    esac
    ok "Initial x-ui state: $initial_state"
    stage 'Collecting consistent runtime snapshot'
    quick_check "$DB"
    for path in "${RUNTIME_PATHS[@]}"; do collect_path "$path"; done
    if (( RESUME_XUI )); then
        systemctl start x-ui && systemctl is-active --quiet x-ui || die 'Cannot return x-ui to its original active state.'
        RESUME_XUI=0
        ok 'x-ui returned to its original active state'
    else
        ok 'x-ui remains inactive'
    fi
    if [[ "$ADGUARD_HOME" == true ]]; then
        initial_state=$(systemctl is-active AdGuardHome) || :
        case "$initial_state" in
            active) RESUME_AGH=1; systemctl stop AdGuardHome || die 'Cannot stop AdGuard Home for snapshot.' ;;
            inactive|failed) ;;
            *) die 'AdGuard Home service state is transitional or unknown.' ;;
        esac
        systemctl is-active --quiet AdGuardHome && die 'AdGuard Home is still running; snapshot refused.'
    fi
    stage 'Collecting managed configuration and web content'
    for path in "${TREE_PATHS[@]}" "${EXTRA_PATHS[@]}"; do collect_path "$path"; done
    if (( RESUME_AGH )); then
        systemctl start AdGuardHome && systemctl is-active --quiet AdGuardHome || die 'Cannot recover AdGuard Home after snapshot.'
        RESUME_AGH=0
    fi
    write_metadata
    stage 'Compressing and verifying archive'
    local name="x-ui-backup-$(date -u +%Y%m%d-%H%M%S)-${STAGING##*-}.tar.gz"
    OUTPUT="$BACKUP_STORE/.$name.partial"
    tar -czf "$OUTPUT" -C "$STAGING" meta.json files
    chown root:root "$OUTPUT"
    chmod 0600 "$OUTPUT"
    validate_archive "$OUTPUT"
    resume_certbot_timer || die 'Cannot restore certbot.timer after snapshot.'
    mv -- "$OUTPUT" "$BACKUP_STORE/$name"
    OUTPUT="$BACKUP_STORE/$name"
    BACKUP_FINISHED=1
    ok 'Archive integrity and required members verified'
    printf '\nBackup completed successfully.\n  File: %s\n  Size: %s\n' "$OUTPUT" "$(du -h "$OUTPUT" | cut -f1)"
    warn 'Backup contains secrets and is stored on this VPS. Copy it securely off-host for disaster recovery.'
}

check_compatibility() {
    python3 - "$STAGING/meta.json" "$OS_ID" "$OS_VERSION" "$ARCH" <<'PY'
import json, sys
with open(sys.argv[1]) as f:
    meta = json.load(f)
if [meta['os_id'], meta['os_version'], meta['arch']] != sys.argv[2:]:
    print('[FAIL] Restore requires the same OS ID, VERSION_ID and architecture.', file=sys.stderr)
    sys.exit(1)
PY
    ok "Backup format 3; OS $OS_ID $OS_VERSION; architecture $ARCH"
}

replace_managed_state() {
    local path source
    for path in "${RUNTIME_PATHS[@]}" "${TREE_PATHS[@]}" /var/www/diagnostics; do
        source="$STAGING/files$path"
        rm -rf -- "$path"
        mkdir -p "$(dirname "$path")"
        if [[ -e "$source" ]]; then cp -a -- "$source" "$path"; fi
    done
    for path in "${EXTRA_PATHS[@]:0:2}"; do
        mkdir -p "$(dirname "$path")"
        rm -f -- "$path"
        cp -a -- "$STAGING/files$path" "$path"
    done
    if [[ -f "$STAGING/files$SYSCTL_FILE" ]]; then
        mkdir -p "$(dirname "$SYSCTL_FILE")"
        install -o root -g root -m 0644 "$STAGING/files$SYSCTL_FILE" "$SYSCTL_FILE"
        sysctl -p "$SYSCTL_FILE" || die 'Cannot apply managed sysctl file.'
    fi
}

repair_panel_certificates() {
    local certificate domain
    certificate=$(sqlite3 "$DB" "SELECT value FROM settings WHERE key='webCertFile';")
    if [[ "$certificate" =~ ^/root/cert/([^/]+)/ ]]; then
        domain=${BASH_REMATCH[1]}
        [[ "$domain" != . && "$domain" != .. ]] || die 'Invalid panel certificate domain.'
        if [[ -d "/etc/letsencrypt/live/$domain" ]]; then
            mkdir -p "/root/cert/$domain"
            chmod 0755 /root/cert "/root/cert/$domain"
            for name in fullchain.pem privkey.pem; do
                if [[ ! -e "/root/cert/$domain/$name" ]]; then
                    ln -sfn "/etc/letsencrypt/live/$domain/$name" "/root/cert/$domain/$name"
                fi
            done
        fi
    fi
}

prepare_mtr_backend() {
    local binary
    if ! id mtr-backend >/dev/null 2>&1; then
        useradd --system --no-create-home --shell /usr/sbin/nologin mtr-backend
    fi
    chmod 0755 /usr/local/lib/3x-ui-pro /usr/local/lib/3x-ui-pro/mtr-backend.py
    for binary in mtr mtr-packet; do
        if command -v "$binary" >/dev/null; then
            if ! setcap cap_net_raw+ep "$(command -v "$binary")" 2>/dev/null; then
                warn "Could not set CAP_NET_RAW file capability on $binary; mtr-backend uses systemd AmbientCapabilities."
            fi
        fi
    done
}

regenerate_diagnostics() {
    local directory=/var/www/diagnostics/testfiles current source
    mkdir -p "$directory"
    dd if=/dev/zero of="$directory/test-15k.bin" bs=1024 count=15 status=none
    dd if=/dev/zero of="$directory/test-17k.bin" bs=1024 count=17 status=none
    dd if=/dev/zero of="$directory/test-100m.bin" bs=1048576 count=100 status=none
    dd if=/dev/zero of="$directory/test-1g.bin" bs=1048576 count=1024 status=none
    rm -f "$directory/test-512m.bin"
    source=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["source_ipv4"])' "$STAGING/meta.json")
    if current=$(current_ipv4); then
        if [[ -n "$source" && "$source" != "$current" ]]; then
            python3 - /var/www/diagnostics/index.html "$source" "$current" <<'PY'
import re, sys
path, old, new = sys.argv[1:]
with open(path) as f:
    html = f.read()
pattern = r'(<(?:span|code)\b[^>]*\bid=[\'"]server-ip(?:-step)?[\'"][^>]*>)([^<]*)(</(?:span|code)>)'
updated = re.sub(pattern, lambda m: m[1] + (new if m[2] == old else m[2]) + m[3], html)
if updated == html:
    print('[WARN] Diagnostics IP display was not recognized; saved display left unchanged.', file=sys.stderr)
else:
    with open(path, 'w') as f:
        f.write(updated)
PY
            warn 'Source VPS IP differs from current VPS IP. Point DNS to the new VPS separately.'
        elif [[ -z "$source" ]]; then
            warn 'Backup has no source IPv4; saved diagnostics display left unchanged.'
        fi
    else
        warn 'Cannot detect current IPv4; saved diagnostics display left unchanged.'
    fi
    chmod 0755 /var/www/diagnostics "$directory"
    chown -R www-data:www-data /var/www/diagnostics /var/www/html /var/www/subpage
}

prepare_acme_webroot() {
    install -d -o root -g root -m 0755 /var/www/acme /var/www/acme/.well-known /var/www/acme/.well-known/acme-challenge
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

restore_firewall() {
    for rule in 80/tcp 443/tcp 443/udp; do ufw allow "$rule"; done
    local status
    status=$(LC_ALL=C ufw status) || die 'Cannot read UFW status.'
    if ! grep -q '^Status: active$' <<< "$status"; then
        warn 'UFW is inactive; application rules added without enabling the firewall.'
    fi
}

restored_certificate_domains() {
    python3 - /etc/letsencrypt/renewal-hooks/deploy/3x-ui-auto-nginx <<'PYDOM'
import shlex, sys
values = {}
for line in open(sys.argv[1]):
    for key in ('PANEL_DOMAIN', 'REALITY_DOMAIN'):
        if line.startswith(key + '='):
            parts = shlex.split(line.strip())
            if len(parts) == 1:
                values[key] = parts[0].split('=', 1)[1]
names = [values.get('PANEL_DOMAIN'), values.get('REALITY_DOMAIN')]
if any(not name or '/' in name or any(c.isspace() for c in name) for name in names) or names[0] == names[1]:
    sys.exit('Missing or ambiguous project domains in deploy hook')
print('\n'.join(names))
PYDOM
}

check_health() {
    local service attempt failed=0
    stage 'Final checks'
    quick_check "$DB"
    if nginx -t > "$STAGING/nginx-test.log" 2>&1; then
        ok 'nginx configuration'
    else
        warn 'nginx configuration check failed; inspect nginx -t.'
        failed=1
    fi
    for service in x-ui nginx mtr-backend; do
        if systemctl is-active --quiet "$service"; then ok "$service is active";
        else printf '[FAIL] %s is not active\n' "$service" >&2; failed=1; fi
    done
    local domains
    domains=$(restored_certificate_domains) || die 'Cannot detect saved certificate domains.'
    local -a names
    mapfile -t names <<< "$domains"
    check_certificate_renewal "${names[0]}" "${names[1]}" || die 'Certificate renewal validation failed.'
    for attempt in {1..10}; do
        [[ -S "$XHTTP_SOCKET" ]] && break
        sleep 0.5
    done
    if [[ -S "$XHTTP_SOCKET" ]]; then ok 'XHTTP Unix socket';
    else printf '[FAIL] XHTTP Unix socket is missing\n' >&2; failed=1; fi
    if [[ "$ADGUARD_HOME" == true ]]; then
        agh_health || die 'AdGuard Home failed mandatory health checks.'
        ok 'AdGuard Home / HTTPS DoH; existing credentials preserved'
    fi
    (( ! failed )) || die 'Restore failed mandatory health checks; archive is unchanged. Fix the cause and rerun restore.'
}

# Trusted static AGH contract, synchronized with assets/adguard/managed.sh by tests.
# Embedded so restore never sources an archive-provided shell helper for preflight.
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

preflight_staged_adguard() (
    local AGH_VERSION=v0.107.79 AGH_DIR="$STAGING/files/opt/AdGuardHome"
    local AGH_DATA_ROOT=/opt/AdGuardHome
    local snippet="$STAGING/files/etc/nginx/snippets/x-ui-auto-optional/adguard.conf"
    # Authenticate using trusted host-architecture hashes before ANY staged execution.
    agh_verify_binary "$ARCH" ||
        die 'Staged AdGuard Home executable SHA256 mismatch; target state was not changed.'
    agh_config && [[ "$AGH_ARCH" == "$ARCH" ]] ||
        die 'Staged AdGuard Home configuration/binary contract is invalid; target state was not changed.'
    [[ -f "$snippet" && ! -L "$snippet" ]] && cmp -s "$snippet" <(agh_snippet) ||
        die 'Staged AdGuard Home nginx snippet is invalid; target state was not changed.'
)

cmd_restore() {
    stage 'Restore preflight'
    local archive=${1:-} service path
    [[ -n "$archive" && -f "$archive" ]] || die 'Usage: x-ui-backup restore <existing archive.tar.gz>'
    archive=$(readlink -f -- "$archive")
    for path in "${RUNTIME_PATHS[@]}" "${TREE_PATHS[@]}" "${EXTRA_PATHS[@]}" /var/www/diagnostics; do
        [[ "$archive" != "$path" && "$archive" != "$path/"* ]] ||
            die 'Move the archive outside managed paths (for example /var/backups/x-ui) before restore.'
    done
    host_identity
    # Bootstrap archive tools before validation/extraction; managed state is untouched.
    install_missing_packages python3 gzip tar
    gzip -t -- "$archive" || die 'Archive gzip integrity check failed.'
    validate_archive "$archive"
    prepare_store restore
    tar -xzf "$archive" -C "$STAGING" --same-owner
    check_compatibility
    install_missing_packages "${PACKAGES[@]}"
    check_python_dependencies
    quick_check "$STAGING/files$DB"
    ADGUARD_HOME=$(python3 -c 'import json,sys; print(str(json.load(open(sys.argv[1]))["adguard_home"]).lower())' "$STAGING/meta.json")
    if [[ "$ADGUARD_HOME" == true ]]; then
        preflight_staged_adguard
    fi
    pause_certbot_timer
    cleanup_adguard || die 'Cannot clean target AdGuard Home; core state was not replaced.'
    stage 'Stopping services and restoring managed state'
    for service in nginx x-ui mtr-backend; do
        if systemctl cat "$service" >/dev/null 2>&1; then
            systemctl stop "$service" || die "Cannot stop $service."
        fi
    done
    replace_managed_state
    install -d -o root -g root -m 0755 /etc/nginx/snippets/x-ui-auto-optional || die 'Cannot prepare optional nginx include directory.'
    if [[ "$ADGUARD_HOME" == true ]]; then
        chown -R root:root /opt/AdGuardHome
        chmod 0700 /opt/AdGuardHome
        chmod 0755 /opt/AdGuardHome/AdGuardHome
        chmod 0600 /opt/AdGuardHome/AdGuardHome.yaml /opt/AdGuardHome/managed.json
        . /usr/local/lib/3x-ui-pro/managed-adguard.sh
        agh_config && [[ "$AGH_ARCH" == "$ARCH" ]] && agh_install_service || die 'Cannot validate/recreate restored AdGuard Home service.'
    fi
    repair_panel_certificates
    prepare_mtr_backend
    regenerate_diagnostics
    rm -rf /var/www/acme/.well-known/acme-challenge
    prepare_acme_webroot
    remove_legacy_certbot_cron || die 'Cannot remove the exact legacy project renewal job.'
    restore_firewall
    stage 'Enabling and starting services'
    systemctl daemon-reload
    nginx -t > "$STAGING/nginx-test.log" 2>&1 || die 'nginx -t failed; nginx was not started. Fix the configuration and rerun restore.'
    rm -f -- "$XHTTP_SOCKET"
    for service in x-ui mtr-backend nginx; do
        systemctl enable "$service" || die "Cannot enable $service."
        systemctl start "$service" || die "Cannot start $service."
    done
    systemctl enable --now certbot.timer || die 'Cannot enable and start certbot.timer.'
    RESUME_CERTBOT_TIMER=0
    check_health
    printf '\nRestore completed successfully.\n'
    warn 'Recovery uses the saved domains. On a new VPS, point their DNS to this VPS separately.'
}

cmd_list() {
    local archive
    shopt -s nullglob
    local archives=("$BACKUP_STORE"/*.tar.gz)
    if (( ! ${#archives[@]} )); then printf 'No backups in %s\n' "$BACKUP_STORE"; return; fi
    for archive in "${archives[@]}"; do
        printf '%-52s %8s\n' "${archive##*/}" "$(du -h "$archive" | cut -f1)"
    done
}

main() {
    require_root
    export LC_ALL=C
    trap cleanup EXIT
    trap 'printf "[FAIL] Operation failed during: %s\n" "$STAGE" >&2; exit 1' ERR
    trap 'exit 130' INT
    trap 'exit 143' TERM
    case ${1:-} in
        backup) cmd_backup ;;
        restore) cmd_restore "${2:-}" ;;
        list) cmd_list ;;
        *) die 'Usage: x-ui-backup {backup|restore <archive>|list}' ;;
    esac
}

if [[ ${BASH_SOURCE[0]} == "$0" ]]; then main "$@"; fi
