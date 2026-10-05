"""Validate fresh-install templates without sourcing or running the installer."""

import json
import hashlib
import tarfile
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import threading
import time
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "x-ui-latest.sh").read_text()
PATCH = (ROOT / "x-ui-patch.sh").read_text()
MONTHLY = ('@monthly certbot renew --non-interactive --pre-hook "systemctl stop nginx" '
           '--post-hook "systemctl start nginx" > /dev/null 2>&1')
FIXTURE = {
    "domain": "deploy.example", "reality_domain": "cover.example",
    "xhttp_path": "Session7AbC", "sub_path": "subscription", "json_path": "jsonsub",
    "panel_path": "panel", "ws_path": "websocket", "trojan_path": "trojan",
    "sub_port": "10001", "panel_port": "10002", "ws_port": "10003",
    "trojan_port": "10004", "mtr_backend_port": "10005",
    "ws_route": "/10003/websocket", "trojan_route": "/10004/trojan",
    "diag_path": "/diagnostics/", "diag_token": "test-token", "emoji_flag": "test",
    "private_key": "test-private", "public_key": "test-public",
    "sub_uri": "https://deploy.example/subscription/",
    "json_uri": "https://deploy.example/jsonsub?name=",
    "gid_col": "", "gid_reality": "", "gid_ws": "", "gid_xhttp": "", "gid_trojan": "", "gid_hysteria": "",
    "http2_listen": " http2", "http2_on": "",
}


def template(target, source=SOURCE):
    """Extract one original heredoc; never execute its surrounding function."""
    matches = re.findall(r"^\s*" + re.escape(target) + r" <<EOF\n(.*?)^EOF$", source, re.M | re.S)
    if len(matches) != 1:
        raise AssertionError(f"Expected one heredoc for {target}, found {len(matches)}")
    return matches[0]


def render(target, source=SOURCE, **variables):
    script = "shor=(a b c d e f g h)\ncat <<EOF\n" + template(target, source) + "EOF\n"
    return subprocess.check_output(
        ["bash", "-eu", "-c", script], text=True,
        env={**os.environ, **FIXTURE, **variables},
    )


def seed(group_id=False, **variables):
    """Execute generated inbound/Host INSERTs against both supported Host schemas."""
    setup = re.search(r"    local gid_col=.*?^    fi", function("configure_xui_db"), re.M | re.S).group()
    script = (
        'sqlite3() { echo "$HOST_TEST_SCHEMA"; }\n' + function("gen_group_id") +
        '\nemit() {\n' + setup + '\nshor=(a b c d e f g h)\ncat <<EOF\n' +
        template("sqlite3 $XUIDB") + 'EOF\n}\nemit\n'
    )
    sql = subprocess.check_output(["bash", "-u", "-c", script], text=True, env={
        **os.environ, **FIXTURE, **variables, "XUIDB": "/fixture/x-ui.db",
        "HOST_TEST_SCHEMA": "group_id" if group_id else "id",
    })
    blocks = re.findall(r'^INSERT INTO "inbounds"\s.*?^\);', sql, re.M | re.S)
    if len(blocks) != 5:
        raise AssertionError(f"Expected five inbound INSERTs, found {len(blocks)}")
    columns = re.search(r'\(("user_id".*?)\)\s*VALUES', blocks[0], re.S).group(1)
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("CREATE TABLE inbounds (id INTEGER PRIMARY KEY," + columns + ")")
        db.execute('''CREATE TABLE hosts (
            id INTEGER PRIMARY KEY, inbound_id INTEGER NOT NULL REFERENCES inbounds(id),
            sort_order INTEGER, remark TEXT, address TEXT, port INTEGER, security TEXT,
            fingerprint TEXT, alpn TEXT, is_disabled INTEGER DEFAULT 0,
            sni TEXT DEFAULT '', final_mask TEXT DEFAULT '', mux_params TEXT DEFAULT '',
            sockopt_params TEXT DEFAULT '' ''' + (", group_id TEXT NOT NULL" if group_id else "") + ")")
        for block in blocks:
            db.execute(block)
        db.execute(re.search(r'^INSERT INTO "hosts".*?;', sql, re.M | re.S).group())
        rows = {row["remark"].split()[-1]: dict(row) for row in db.execute("SELECT * FROM inbounds")}
        return rows, [dict(row) for row in db.execute("SELECT * FROM hosts")]


def inbounds(**variables):
    return seed(**variables)[0]


SHARED = "cat > /etc/nginx/snippets/includes.conf"
MAIN = 'cat > "/etc/nginx/sites-available/${domain}"'


def function(name):
    matches = re.findall(r"^" + re.escape(name) + r"\(\)\s*\{.*?^\}", SOURCE, re.M | re.S)
    if len(matches) != 1:
        raise AssertionError(f"Expected one {name} function")
    return matches[0]


class PersonalXHTTP(unittest.TestCase):
    def test_shell_syntax(self):
        subprocess.run(["bash", "-n", str(ROOT / "x-ui-latest.sh")], check=True)

    def firewall(self, status, connection="", sshd="", fail="", sshd_exit="0"):
        # Run only setup_firewall; mock all UFW/sshd operations on private files.
        mock = '''ufw() {
    printf '%s\\n' "$*" >> "$FIREWALL_TEST_ROOT/calls"
    [[ "$*" == "$FAIL_UFW" ]] && return 1
    case "$*" in
        status)
            [[ "$LC_ALL" == C ]] || return 1
            printf 'Status: %s\\n' "$(cat "$FIREWALL_TEST_ROOT/status")" ;;
        allow*)
            grep -qxF "$2" "$FIREWALL_TEST_ROOT/rules" || printf '%s\\n' "$2" >> "$FIREWALL_TEST_ROOT/rules" ;;
        '--force enable') echo active > "$FIREWALL_TEST_ROOT/status" ;;
        *) return 2 ;;
    esac
}
sshd() {
    echo "sshd $*" >> "$FIREWALL_TEST_ROOT/calls"
    printf '%s\\n' "$SSHD_OUTPUT"
    return "$SSHD_EXIT"
}
msg_err() { echo "$1" >&2; }
'''
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "status").write_text(status)
            (root / "rules").write_text("2222/tcp\n")
            result = subprocess.run(
                ["bash", "-u", "-c", mock + function("setup_firewall") + "\nsetup_firewall\n"],
                text=True, capture_output=True, env={
                    **os.environ, "FIREWALL_TEST_ROOT": tmp, "SSH_CONNECTION": connection,
                    "SSHD_OUTPUT": sshd, "SSHD_EXIT": sshd_exit, "FAIL_UFW": fail, "panel_port": FIXTURE["panel_port"],
                },
            )
            return result, (root / "calls").read_text().splitlines(), (root / "rules").read_text().splitlines(), (root / "status").read_text().strip()

    def test_firewall_active_preserves_existing_policy(self):
        result, calls, rules, status = self.firewall("active", sshd="port 22")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, ["status", "allow 80/tcp", "allow 443/tcp", "allow 443/udp"])
        self.assertEqual(rules, ["2222/tcp", "80/tcp", "443/tcp", "443/udp"])
        self.assertEqual(status, "active")

    def test_firewall_inactive_uses_connection_ssh_port(self):
        for port in ("2222", "22", "00022", "65535"):
            with self.subTest(port=port):
                result, calls, _, status = self.firewall("inactive", f"198.51.100.10 54321 203.0.113.5 {port}", "port 9999")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls, ["status", "allow 80/tcp", "allow 443/tcp", "allow 443/udp", f"allow {int(port)}/tcp", "--force enable"])
                self.assertEqual(status, "active")

    def test_firewall_inactive_falls_back_to_effective_sshd_ports(self):
        for connection in ("", "invalid", "a b c 0", "a b c 65536", "a b c 99999999999999999999", "a b c 22 extra"):
            with self.subTest(connection=connection):
                result, calls, rules, status = self.firewall("inactive", connection, "port 2222\nport 2200\nport 2222\nport 0\nport 65536\nport invalid\npermitrootlogin yes")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls, ["status", "allow 80/tcp", "allow 443/tcp", "allow 443/udp", "sshd -T", "allow 2200/tcp", "allow 2222/tcp", "--force enable"])
                self.assertNotIn("22/tcp", rules)
                self.assertEqual(status, "active")

    def test_firewall_inactive_unknown_ssh_warns_without_enabling(self):
        for output, code in (("", "0"), ("port 0\nport 65536\nport invalid", "0"), ("port 22", "1")):
            with self.subTest(output=output, code=code):
                result, calls, _, status = self.firewall("inactive", "invalid", output, sshd_exit=code)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(calls, ["status", "allow 80/tcp", "allow 443/tcp", "allow 443/udp", "sshd -T"])
                self.assertEqual(status, "inactive")
                self.assertIn("WARNING", result.stderr)
                self.assertIn("SSH port could not be detected", result.stderr)
                self.assertIn("UFW was not enabled automatically", result.stderr)

    def test_firewall_required_operation_failures_are_fatal(self):
        commands = ["status", "allow 80/tcp", "allow 443/tcp", "allow 443/udp", "allow 2200/tcp", "--force enable"]
        for index, command in enumerate(commands):
            with self.subTest(command=command):
                result, calls, _, _ = self.firewall("inactive", "a b c 2200", fail=command)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(calls, commands[:index + 1])
        result, calls, _, _ = self.firewall("unexpected")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(calls, ["status"])

    def test_firewall_never_disables_resets_or_hardcodes_ssh(self):
        for line in SOURCE.splitlines():
            if re.search(r"\bufw\b", line):
                executable = " ".join(shlex.split(line, comments=True))
                self.assertNotRegex(executable, r"\bufw\s+(?:disable|reset|default|delete|allow\s+22/tcp)\b")

    def test_firewall_precedes_certbot_and_stops_installer_on_failure(self):
        main = function("main")
        self.assertEqual(len(re.findall(r"^\s*setup_firewall\b", main, re.M)), 1)
        self.assertLess(main.index("install_packages"), main.index("setup_firewall"))
        self.assertLess(main.index("setup_firewall"), main.index("get_ssl_certs"))
        self.assertRegex(main, r"setup_firewall\s*\|\|[^\n]*exit 1")

    def test_panel_and_cli_download_use_the_same_release_tag(self):
        panel = function("install_panel")
        self.assertNotIn("raw.githubusercontent.com/MHSanaei/3x-ui", panel)
        self.assertIn('install -m 0755 x-ui.sh /usr/bin/x-ui', panel)
        self.assertLess(panel.index("_download_panel_archive"), panel.index("tar zxvf"))
        self.assertLess(panel.index("tar zxvf"), panel.index("install -m 0755 x-ui.sh"))
        download = re.search(r"    _download_panel_archive .*?\n        \"\$archive\" \|\| return 1", panel, re.S).group()
        mock = '''_arch() { echo amd64; }
_download_panel_archive() { printf '%s\\n' "$1"; }
check() {
'''
        for tag in ("v3.8.0", "v3.9.0", "v9.8.7"):
            with self.subTest(tag=tag):
                result = subprocess.check_output(["bash", "-eu", "-c", mock + download + "\n}\ncheck"], text=True,
                                                 env={**os.environ, "tag_version": tag, "archive": "/fixture/archive"})
                self.assertEqual(result.strip(), f"https://github.com/MHSanaei/3x-ui/releases/download/{tag}/x-ui-linux-amd64.tar.gz")

    def test_setup_cron_without_scheduled_restart(self):
        functions = re.findall(r"^setup_cron\(\)\s*\{.*?^\}", SOURCE, re.M | re.S)
        self.assertEqual(len(functions), 1, "Expected one setup_cron function")
        monthly = (
            '@monthly certbot renew --non-interactive --pre-hook "systemctl stop nginx" '
            '--post-hook "systemctl start nginx" > /dev/null 2>&1'
        )
        preserved = [
            "@hourly /usr/local/bin/backup",
            "0 3 * * * /opt/x-ui-metrics",
            "@weekly certbot certificates > /root/cert-report",
            "@daily /opt/cloudflareips",
            "# Administrator's certbot/x-ui tasks",
        ]
        existing = "\n".join(preserved + [monthly, monthly]) + "\n"
        for initial, expected in (("", [monthly]), (existing, preserved + [monthly])):
            with self.subTest(initial=initial):
                result, cron = self.cron_setup(initial, runs=2)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                # No replacement restart/reload job may be added, at any schedule.
                self.assertEqual(cron.splitlines(), expected)

    def cron_setup(self, initial, failure="", runs=1):
        # Only isolated cron functions execute; no real crontab/systemd changes.
        mock = r'''msg_err() { echo "$*" >&2; }
command() {
    [[ "$FAIL_CRON" == missing && "$*" == '-v crontab' ]] && return 1
    builtin command "$@"
}
systemctl() { [[ "$FAIL_CRON" != "$1" ]]; }
crontab() {
    case "$1" in
        -l)
            [[ "$FAIL_CRON" == read ]] && { echo 'permission denied' >&2; return 1; }
            [[ -f "$CRON_TEST_FILE" ]] || { echo 'no crontab for root' >&2; return 1; }
            cat "$CRON_TEST_FILE" ;;
        -)
            [[ "$FAIL_CRON" == write ]] && return 1
            cat > "$CRON_TEST_FILE"
            [[ "$FAIL_CRON" == dropped ]] && : > "$CRON_TEST_FILE"
            [[ "$FAIL_CRON" == duplicate ]] && printf '%s\n' "$MANAGED_CRON" >> "$CRON_TEST_FILE"
            return 0 ;;
        *) return 2 ;;
    esac
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            cron = Path(tmp) / "crontab"
            if initial is not None:
                cron.write_text(initial)
            script = mock + function("check_cron") + "\n" + function("setup_cron")
            script += "\n" + "setup_cron || exit 1\n" * runs + "echo cron-success\n"
            result = subprocess.run(["bash", "-u", "-c", script], capture_output=True, text=True, env={
                **os.environ, "MANAGED_CRON": MONTHLY, "CRON_TEST_FILE": str(cron), "FAIL_CRON": failure,
            })
            return result, cron.read_text() if cron.exists() else ""

    def test_cron_setup_fails_closed_and_handles_absent_root_crontab(self):
        self.assertIn("MANAGED_CRON=" + shlex.quote(MONTHLY), SOURCE)
        self.assertRegex(function("main"), r"setup_cron\s*\|\|.*exit 1")
        result, cron = self.cron_setup(None)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(cron, MONTHLY + "\n")
        for failure in ("missing", "read", "write", "dropped", "duplicate", "is-active", "is-enabled"):
            with self.subTest(failure=failure):
                result, _ = self.cron_setup("@hourly /opt/unrelated\n", failure)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("cron-success", result.stdout)

    def test_ws_host_uses_only_http11_alpn(self):
        for grouped in (False, True):
            with self.subTest(group_id=grouped):
                rows, hosts = seed(group_id=grouped)
                ws = [h for h in hosts if h["inbound_id"] == rows["ws"]["id"]]
                self.assertEqual(len(ws), 1)
                self.assertEqual(json.loads(ws[0]["alpn"]), ["http/1.1"])
                for name in ("xhttp", "trojan-grpc"):
                    host = next(h for h in hosts if h["inbound_id"] == rows[name]["id"])
                    self.assertEqual(json.loads(host["alpn"]), ["h2", "http/1.1"])

    def test_installer_dependencies_and_cron_activation_are_required(self):
        mock = r'''msg_err() { echo "$*" >&2; }
apt() { echo "apt $*" >> "$TEST_LOG"; [[ "$FAIL_DEP" != apt ]]; }
apt-get() { echo "apt-get $*" >> "$TEST_LOG"; [[ "$FAIL_DEP" != apt ]]; }
systemctl() { echo "systemctl $*" >> "$TEST_LOG"; [[ "$FAIL_DEP" != cron || "$*" != 'enable --now cron' ]]; }
command() { [[ "$*" != "-v $FAIL_DEP" ]]; }
'''
        required = {"cron", "openssl", "procps", "psmisc", "iproute2", "tar", "gzip", "tzdata", "ca-certificates",
                    "curl", "wget", "jq", "bash", "sudo", "nginx-full", "certbot", "python3-certbot-nginx",
                    "sqlite3", "ufw", "netcat-openbsd", "mtr", "python3", "libcap2-bin"}
        self.assertRegex(function("main"), r"install_packages\s*\|\|.*exit 1")
        for failure in ("", "apt", "crontab", "sysctl", "ip", "fuser", "cron"):
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                log = Path(tmp) / "calls"
                result = subprocess.run(["bash", "-u", "-c", mock + function("install_packages") + "\ninstall_packages\n"],
                                        capture_output=True, text=True, env={**os.environ, "Pak": "apt", "INSTALL": "y",
                                                                            "TEST_LOG": str(log), "FAIL_DEP": failure})
                self.assertEqual(result.returncode, 1 if failure else 0, result.stderr)
                if not failure:
                    calls = log.read_text().splitlines()
                    packages = next(c.split()[3:] for c in calls if c.startswith("apt -y install "))
                    self.assertTrue(required.issubset(packages))
                    self.assertEqual(calls[-1], "systemctl enable --now cron")

    def test_installer_final_health_gate_precedes_success_output(self):
        main = function("main")
        self.assertRegex(main, r"check_installation\s*\|\|.*exit 1")
        self.assertLess(main.index("check_installation"), main.index("show_results"))
        mock = r'''msg_err() { echo "$*" >&2; }
systemctl() { [[ "$FAIL_HEALTH" != "$1:$3" ]]; }
nginx() { [[ "$FAIL_HEALTH" != nginx-test ]]; }
crontab() {
    [[ "$FAIL_HEALTH" == cron-read ]] && return 1
    [[ "$FAIL_HEALTH" == cron-missing ]] && return 0
    printf '%s\n' "$MANAGED_CRON"
    [[ "$FAIL_HEALTH" == cron-duplicate ]] && printf '%s\n' "$MANAGED_CRON"
    return 0
}
sleep() { :; }
'''
        failures = ("", "is-active:x-ui", "is-active:nginx", "is-active:mtr-backend", "is-active:cron",
                    "is-enabled:cron", "nginx-test", "cron-read", "cron-missing", "cron-duplicate", "socket")
        for failure in failures:
            with self.subTest(failure=failure), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "xhttp.sock"
                with socket.socket(socket.AF_UNIX) as uds:
                    if failure != "socket":
                        uds.bind(str(path))
                    script = mock + function("check_cron") + "\n" + function("check_installation").replace("/dev/shm/uds2023.sock", str(path))
                    result = subprocess.run(["bash", "-u", "-c", script + "\ncheck_installation || exit 1\necho final-success\n"],
                                            capture_output=True, text=True, env={**os.environ, "MANAGED_CRON": MONTHLY, "FAIL_HEALTH": failure})
                    self.assertEqual(result.returncode, 1 if failure else 0, result.stderr)
                    self.assertEqual("final-success" in result.stdout, not bool(failure))

    def test_inbound_and_sniffing(self):
        rows = inbounds()
        self.assertEqual(set(rows), {"reality", "ws", "xhttp", "trojan-grpc", "hysteria2"})
        self.assertEqual(rows["reality"]["enable"], "1")
        self.assertEqual(rows["ws"]["enable"], "0")
        self.assertEqual(rows["trojan-grpc"]["enable"], "0")
        self.assertEqual({name: row["protocol"] for name, row in rows.items()}, {
            "reality": "vless", "ws": "vless", "xhttp": "vless",
            "trojan-grpc": "trojan", "hysteria2": "hysteria",
        })
        for row in rows.values():
            json.loads(row["settings"])
            json.loads(row["stream_settings"])
            self.assertEqual(json.loads(row["sniffing"]), {
                "enabled": True, "destOverride": ["http", "tls", "quic", "fakedns"],
                "metadataOnly": False, "routeOnly": False,
            })
        xhttp = rows["xhttp"]
        self.assertEqual(xhttp["enable"], "1")
        self.assertEqual(xhttp["listen"], "/dev/shm/uds2023.sock,0666")
        self.assertEqual(xhttp["port"], "0")
        self.assertEqual(xhttp["protocol"], "vless")
        # Exact minimal profile also excludes legacy tuning, XMUX and generic Mux.
        self.assertEqual(json.loads(xhttp["stream_settings"]), {
            "network": "xhttp", "security": "none",
            "xhttpSettings": {"path": "/Session7AbC", "mode": "stream-up"},
            "sockopt": {"trustedXForwardedFor": ["X-Forwarded-For"]},
        })

    def test_hysteria2_minimal_server_profile(self):
        row = inbounds()["hysteria2"]
        self.assertEqual({key: row[key] for key in ("enable", "protocol", "listen", "port", "tag")}, {
            "enable": "1", "protocol": "hysteria", "listen": "", "port": "443", "tag": "inbound-443-udp",
        })
        self.assertEqual(json.loads(row["settings"]), {"version": 2, "clients": []})
        # Exact profile excludes auth/useFile/fingerprint, FinalMask and all custom QUIC/mask/hopping tuning.
        self.assertEqual(json.loads(row["stream_settings"]), {
            "network": "hysteria", "security": "tls",
            "hysteriaSettings": {"version": 2, "udpIdleTimeout": 60},
            "tlsSettings": {
                "serverName": "deploy.example", "alpn": ["h3"],
                "certificates": [{
                    "certificateFile": "/root/cert/deploy.example/fullchain.pem",
                    "keyFile": "/root/cert/deploy.example/privkey.pem",
                    "ocspStapling": 0, "oneTimeLoading": False,
                    "usage": "encipherment", "buildChain": False,
                }],
            },
        })
        self.assertNotIn(FIXTURE["reality_domain"], row["stream_settings"])

    def test_hysteria2_host_with_and_without_group_id(self):
        for grouped in (False, True):
            with self.subTest(group_id=grouped):
                rows, hosts = seed(group_id=grouped)
                self.assertEqual(len(hosts), 5)
                matched = [host for host in hosts if host["inbound_id"] == rows["hysteria2"]["id"]]
                self.assertEqual(len(matched), 1)
                host = matched[0]
                self.assertEqual({key: host[key] for key in ("remark", "address", "port", "security", "is_disabled")}, {
                    "remark": "hysteria2", "address": "deploy.example", "port": 443,
                    "security": "same", "is_disabled": 0,
                })
                self.assertEqual(json.loads(host["alpn"]), [])
                for key in ("fingerprint", "sni", "final_mask", "mux_params", "sockopt_params"):
                    self.assertEqual(host[key], "")
                if grouped:
                    self.assertEqual(len({host["group_id"] for host in hosts}), 5)
                    for host in hosts:
                        self.assertRegex(host["group_id"], r"^[a-z0-9]{16}$")
                else:
                    self.assertNotIn("group_id", host)

    def test_reality_profile_is_preserved(self):
        row = inbounds()["reality"]
        self.assertEqual((row["port"], row["listen"], row["tag"]), ("8443", "127.0.0.1", "inbound-8443"))
        self.assertEqual(json.loads(row["settings"]), {"clients": [], "decryption": "none", "fallbacks": []})
        self.assertEqual(json.loads(row["stream_settings"]), {
            "network": "tcp", "security": "reality",
            "realitySettings": {
                "show": False, "xver": 0, "target": "127.0.0.1:9443",
                "serverNames": ["cover.example"], "privateKey": "test-private",
                "minClient": "", "maxClient": "", "maxTimediff": 0,
                "shortIds": list("abcdefgh"),
                "settings": {"publicKey": "test-public", "fingerprint": "firefox", "serverName": "", "spiderX": "/"},
            },
            "tcpSettings": {"acceptProxyProtocol": True, "header": {"type": "none"}},
        })

    def test_nginx_prefix_and_headers(self):
        self.assertIn("xhttp_path=$(gen_random_string 10)", SOURCE)
        for path in ("Session7AbC", "1234567890"):
            with self.subTest(path=path):
                shared = render(SHARED, xhttp_path=path)
                location = re.search(r"location \^~ /" + path + r"/ \{([^{}]*)\}", shared)
                self.assertIsNotNone(location, "XHTTP session subpaths need a protected prefix")
                directives = [shlex.split(part.strip()) for part in location[1].split(";") if part.strip()]
                self.assertCountEqual(directives, [
                    ["client_max_body_size", "0"], ["client_body_timeout", "1h"],
                    ["grpc_read_timeout", "1h"], ["grpc_send_timeout", "1h"],
                    ["grpc_set_header", "Connection", ""], ["grpc_set_header", "Host", "$host"],
                    ["grpc_set_header", "X-Real-IP", "$remote_addr"],
                    ["grpc_set_header", "X-Forwarded-For", "$remote_addr"],
                    ["grpc_pass", "unix:/dev/shm/uds2023.sock"],
                ])
                stream = json.loads(inbounds(xhttp_path=path)["xhttp"]["stream_settings"])
                self.assertEqual(stream["xhttpSettings"]["path"] + "/", f"/{path}/")

    def test_http2_and_real_ip_chain(self):
        main = render(MAIN)
        for directive in (
            "http2_max_concurrent_streams 256;", "http2_body_preread_size 128k;",
            "client_body_buffer_size 512k;", "real_ip_header proxy_protocol;",
            "set_real_ip_from 127.0.0.1;", "listen 127.0.0.1:7443 ssl http2 proxy_protocol;",
        ):
            self.assertIn(directive, main)
        stream = render("cat > /etc/nginx/stream-enabled/stream.conf")
        self.assertIn("proxy_protocol on;", stream)
        self.assertIn("cover.example    xray;", stream)
        self.assertIn("deploy.example            www;", stream)

    def test_fixed_transport_routes_and_backend_authorities(self):
        rows = inbounds()
        ws_path = json.loads(rows["ws"]["stream_settings"])["wsSettings"]["path"]
        grpc = json.loads(rows["trojan-grpc"]["stream_settings"])["grpcSettings"]
        self.assertFalse(grpc["multiMode"])
        # Xray 26.9.30 grpc/config.go + encoding/customSeviceName.go:
        # custom /service/method paths are used verbatim, without a /Tun suffix.
        grpc_path = grpc["serviceName"]
        allowed_ports = {FIXTURE[key] for key in ("ws_port", "trojan_port", "panel_port", "sub_port", "mtr_backend_port")}
        for source in (SOURCE, PATCH):
            with self.subTest(script="installer" if source == SOURCE else "patch"):
                shared = render(SHARED, source)
                self.assertNotIn("(?<fwdport>", shared)
                self.assertNotIn("$fwdport", shared)
                fragments = [shared, render(MAIN, source), render('cat > "/etc/nginx/sites-available/${reality_domain}"', source)]
                for fragment in fragments:
                    for target in re.findall(r"\b(?:proxy_pass|grpc_pass)\s+([^;]+);", fragment):
                        if target.startswith("unix:") or target.startswith("grpc://unix:"):
                            self.assertNotIn("$", target)
                            continue
                        authority = re.match(r"^(?:https?|grpc)://127\.0\.0\.1:([0-9]+)(?:/.*)?$", target)
                        self.assertIsNotNone(authority, f"Backend authority must be a fixed loopback port: {target}")
                        self.assertIn(authority[1], allowed_ports)
                def exact(path):
                    matches = re.findall(r"location = " + re.escape(path) + r" \{\n(.*?)\n    \}", shared, re.S)
                    self.assertEqual(len(matches), 1)
                    return matches[0]
                ws = exact(ws_path)
                for directive in (
                    "proxy_pass http://127.0.0.1:10003;", "proxy_http_version 1.1;",
                    "proxy_set_header Upgrade $http_upgrade;", 'proxy_set_header Connection "upgrade";',
                    "proxy_set_header Host $host;", "proxy_set_header X-Real-IP $remote_addr;",
                    "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
                    "proxy_buffering off;", "proxy_request_buffering off;",
                    "proxy_socket_keepalive on;", "proxy_read_timeout 1d;",
                ):
                    self.assertIn(directive, ws)
                trojan = exact(grpc_path)
                for directive in (
                    "grpc_pass grpc://127.0.0.1:10004;", "grpc_read_timeout 1d;",
                    "grpc_socket_keepalive on;", "client_body_timeout 1d;", "grpc_set_header Host $host;",
                ):
                    self.assertIn(directive, trojan)
                # The only regex route remains the existing subscription handler.
                self.assertEqual(re.findall(r"location ~[^\n]+", shared), ["location ~ ^/subscription/(?<clash_sub_id>[^/]+)$ {"])

    def test_patch_reads_only_valid_seeded_transport_routes(self):
        block = PATCH.split("# Read only the installer-managed WS/gRPC routes", 1)[1].split("\n", 1)[1].split("# ── detect domains", 1)[0]
        mock = '''db() { python3 -c 'import os,sqlite3,sys; db=sqlite3.connect(os.environ["ROUTE_TEST_DB"]); print("\\n".join("|".join(map(str,row)) for row in db.execute(sys.argv[1])))' "$1"; }
die() { echo "$*" >&2; exit 1; }
'''
        for invalid in (None, "missing", "/3000/wrong", "/10003/path;bad"):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "x-ui.db"
                with sqlite3.connect(path) as db:
                    db.execute("CREATE TABLE inbounds (id INTEGER PRIMARY KEY, port INTEGER, protocol TEXT, tag TEXT, stream_settings TEXT)")
                    for row in inbounds().values():
                        if invalid == "missing" and row["protocol"] == "trojan":
                            continue
                        stream = json.loads(row["stream_settings"])
                        if invalid and invalid != "missing" and stream["network"] == "ws":
                            stream["wsSettings"]["path"] = invalid
                        db.execute("INSERT INTO inbounds VALUES (?,?,?,?,?)", (row["id"], row["port"], row["protocol"], row["tag"], json.dumps(stream)))
                before = path.read_bytes()
                result = subprocess.run(["bash", "-eu", "-c", mock + block + '\nprintf "%s %s %s %s\\n" "$ws_port" "$ws_route" "$trojan_port" "$trojan_route"'],
                                        text=True, capture_output=True, env={**os.environ, "ROUTE_TEST_DB": str(path)})
                self.assertEqual(result.returncode, 0 if invalid is None else 1, result.stdout + result.stderr)
                if invalid is None:
                    self.assertEqual(result.stdout.strip(), "10003 /10003/websocket 10004 /10004/trojan")
                self.assertEqual(path.read_bytes(), before, "Patch route discovery must be read-only")

    def test_nginx_runtime_rejects_arbitrary_ports_and_preserves_ws(self):
        nginx = os.environ.get("NGINX_BIN") or shutil.which("nginx")
        if not nginx:
            self.skipTest("nginx unavailable; CI requires this runtime check")

        class Backend(ThreadingHTTPServer):
            def get_request(self):
                result = super().get_request()
                self.connections += 1
                return result

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_GET(self):
                self.server.hits.append((self.path, self.request_version, dict(self.headers)))
                upgrade = self.headers.get("Upgrade") == "websocket"
                self.send_response(101 if upgrade else 200)
                if upgrade:
                    self.send_header("Upgrade", "websocket")
                    self.send_header("Connection", "upgrade")
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_POST = do_GET

            def log_message(self, *args):
                pass

        allowed = Backend(("127.0.0.1", 0), Handler)
        forbidden = Backend(("127.0.0.1", 0), Handler)
        for backend in (allowed, forbidden):
            backend.hits, backend.connections = [], 0
            threading.Thread(target=backend.serve_forever, daemon=True).start()
        try:
            port = str(allowed.server_port)
            for source in (SOURCE, PATCH):
                with self.subTest(script="installer" if source == SOURCE else "patch"), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    with socket.socket() as reserved:
                        reserved.bind(("127.0.0.1", 0))
                        public_port = reserved.getsockname()[1]
                    shared = render(SHARED, source, ws_port=port, ws_path="websocket", ws_route=f"/{port}/websocket")
                    (root / "includes.conf").write_text(shared)
                    temp_paths = "".join(f"{kind}_temp_path {root}/{kind};\n" for kind in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi"))
                    config = root / "nginx.conf"
                    config.write_text(
                        f"pid {root}/nginx.pid;\nerror_log stderr;\nevents {{ worker_connections 32; }}\n"
                        "http { access_log off;\n" + temp_paths +
                        f"server {{ listen 127.0.0.1:{public_port}; server_name deploy.example cover.example;\n"
                        f"root {root}; set $hack 0; set $serve_clash_yaml 0; include {root}/includes.conf; }} }}\n"
                    )
                    with (root / "nginx.log").open("w+") as log:
                        process = subprocess.Popen([nginx, "-p", tmp + "/", "-c", str(config), "-g", "daemon off; master_process off;"], stdout=log, stderr=log)
                        try:
                            for _ in range(60):
                                self.assertIsNone(process.poll(), "nginx exited during startup")
                                try:
                                    with socket.create_connection(("127.0.0.1", public_port), timeout=0.1):
                                        break
                                except OSError:
                                    time.sleep(0.025)
                            else:
                                self.fail("nginx did not start")
                            def request(host, path, method="GET", **headers):
                                connection = http.client.HTTPConnection("127.0.0.1", public_port, timeout=3)
                                try:
                                    connection.request(method, path, headers={"Host": host, **headers})
                                    response = connection.getresponse()
                                    response.read()
                                    return response.status
                                finally:
                                    connection.close()
                            before = allowed.connections
                            for host in ("deploy.example", "cover.example"):
                                for path in ("/3000/test", "/9090/api", f"/{forbidden.server_port}/api/mtr", f"/{port}/wrong", f"/{port}/websocket/extra", "/10004/trojan/Tun"):
                                    for method, headers in (("GET", {}), ("GET", {"Upgrade": "websocket", "Connection": "upgrade"}), ("POST", {"Content-Type": "application/grpc"})):
                                        self.assertEqual(request(host, path, method, **headers), 404)
                            self.assertEqual(forbidden.connections, 0, "Unmanaged backend must never receive a connection")
                            self.assertEqual(allowed.connections, before, "Wrong paths must not reach the managed backend")
                            for host in ("deploy.example", "cover.example"):
                                self.assertEqual(request(host, f"/{port}/websocket?token=test", Upgrade="websocket", Connection="upgrade"), 101)
                                path, version, headers = allowed.hits[-1]
                                self.assertEqual(path, f"/{port}/websocket?token=test")
                                self.assertEqual(version, "HTTP/1.1")
                                self.assertEqual(headers["Host"], host)
                                self.assertEqual(headers["Upgrade"], "websocket")
                                self.assertEqual(headers["Connection"], "upgrade")
                                self.assertEqual(headers["X-Real-IP"], "127.0.0.1")
                                self.assertEqual(headers["X-Forwarded-For"], "127.0.0.1")
                        finally:
                            process.terminate()
                            process.wait(timeout=5)
        finally:
            for backend in (allowed, forbidden):
                backend.shutdown()
                backend.server_close()

    def test_stack_runtime_source(self):
        for name in ("x-ui-latest.sh", "x-ui-patch.sh"):
            source = (ROOT / name).read_text()
            self.assertEqual(re.findall(r'^GITHUB_RAW="(.*)"$', source, re.M), [
                "https://raw.githubusercontent.com/xPROMSx/3x-ui-auto-nginx/main",
            ])
            for line in source.splitlines():
                if "raw.githubusercontent.com/mozaroc/3x-ui-pro" in line:
                    self.assertTrue(line.lstrip().startswith("#"), "Unexpected upstream runtime source")

    def test_panel_bootstrap_uses_final_random_config_without_starting_services(self):
        self.assertNotIn("asdfasdf", SOURCE)
        self.assertNotIn('"2096"', SOURCE)
        initial = function("_panel_initial_config")
        for name in ("install_panel", "configure_xui_db"):
            self.assertNotRegex(function(name), r"(?m)^\s*(?:systemctl\s+(?:start|restart)\s+x-ui|x-ui\s+(?:start|restart))\b")
        main = function("main")
        self.assertEqual(len(re.findall(r"(?m)^\s*x-ui restart\b", main)), 1)
        self.assertGreater(main.index("x-ui restart"), main.index("configure_xui_db"))
        self.assertRegex(main, r"install_panel\s*\|\|\s*exit 1")
        self.assertRegex(main, r"configure_xui_db\s*\|\|\s*exit 1")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            log = root / "calls"
            executable = root / "panel"
            executable.write_text('''#!/usr/bin/env python3
import json, os, sys
with open(os.environ["PANEL_TEST_LOG"], "a") as log: log.write(json.dumps(sys.argv[1:]) + "\\n")
sys.exit(1 if sys.argv[1] == os.environ["FAIL_PANEL_COMMAND"] else 0)
''')
            executable.chmod(0o755)
            script = 'sqlite3() { echo "$PANEL_SAVED_STATE"; }\nmsg_err() { echo "$*" >&2; }\n' + initial.replace("/usr/local/x-ui/x-ui", str(executable)) + "\n_panel_initial_config\n"
            for failed in ("", "setting", "migrate", "unsaved-state"):
                with self.subTest(failed=failed):
                    log.write_text("")
                    result = subprocess.run(["bash", "-u", "-c", script], capture_output=True, text=True, env={
                        **os.environ, "PANEL_TEST_LOG": str(log), "FAIL_PANEL_COMMAND": failed,
                        "config_username": "fixture-user", "config_password": "fixture-secret",
                        "panel_port": "10002", "panel_path": "fixture-path", "XUIDB": str(root / "db"),
                        "PANEL_SAVED_STATE": "admin|0.0.0.0|2053|/" if failed == "unsaved-state" else "fixture-user||10002|/fixture-path/",
                    })
                    calls = [json.loads(line) for line in log.read_text().splitlines()]
                    self.assertEqual(calls[0], ["setting", "-username", "fixture-user", "-password", "fixture-secret",
                                                "-port", "10002", "-webBasePath", "fixture-path"])
                    self.assertEqual(calls[1:], [] if failed == "setting" else [["migrate"]])
                    self.assertEqual(result.returncode, 1 if failed else 0)

    def test_firewall_does_not_open_panel_port_for_mtls_automatically(self):
        for status, connection, expected in (
            ("active", "", ["80/tcp", "443/tcp", "443/udp"]),
            ("inactive", "198.51.100.10 54321 203.0.113.5 2222", ["80/tcp", "443/tcp", "443/udp", "2222/tcp"]),
            ("inactive", "", ["80/tcp", "443/tcp", "443/udp"]),
        ):
            with self.subTest(status=status, connection=connection):
                result, calls, _, _ = self.firewall(status, connection)
                self.assertEqual(result.returncode, 0, result.stderr)
                allowed = [call.split()[1] for call in calls if call.startswith("allow ")]
                self.assertCountEqual(allowed, expected)
                self.assertNotIn(FIXTURE["panel_port"], [rule.split("/")[0] for rule in allowed])

    def test_panel_version_minimum_is_checked_before_destructive_actions(self):
        start = SOURCE.index('while [ "$#" -gt 0 ]; do')
        end = SOURCE.index("# ─── Detect package manager")
        preflight = SOURCE[start:end]
        self.assertLess(end, SOURCE.index('    uninstall_xui\n'))
        for version, accepted in (("v3.7.0", False), ("v3.7.99", False), ("v3.8.0", True), ("v3.8.5", True),
                                  ("v3.9.0", True), ("3.10.0", True), ("v4.0.0", True),
                                  ("v3.08.0", False), ("v3.8", False), ("latest", False), ("", False)):
            with self.subTest(version=version):
                script = 'PANEL_VERSION=""\nmsg_err() { echo "$*" >&2; }\n' + preflight + '\necho destructive-actions\n'
                result = subprocess.run(["bash", "-u", "-c", script, "fixture", "-version", version], capture_output=True, text=True)
                self.assertEqual(result.returncode, 0 if accepted else 1, result.stderr)
                self.assertEqual("destructive-actions" in result.stdout, accepted)

    def test_panel_mtls_bind_and_managed_backend_listeners(self):
        rows = inbounds()
        for name in ("reality", "ws", "trojan-grpc"):
            self.assertEqual(rows[name]["listen"], "127.0.0.1", name)
        self.assertEqual((rows["hysteria2"]["listen"], rows["hysteria2"]["port"], rows["hysteria2"]["enable"]), ("", "443", "1"))
        self.assertEqual(rows["xhttp"]["listen"], "/dev/shm/uds2023.sock,0666")
        sql = render("sqlite3 $XUIDB", XUIDB="/fixture/x-ui.db").split('INSERT INTO "inbounds"', 1)[0]
        with sqlite3.connect(":memory:") as db:
            db.execute('CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT)')
            db.executemany('INSERT INTO settings VALUES (?,?)', [("webListen", "0.0.0.0"), ("subListen", "::")])
            db.executescript(sql)
            values = dict(db.execute('SELECT key,value FROM settings'))
        self.assertEqual(values["webListen"], "", "Native node mTLS needs an external-capable panel listener")
        self.assertNotIn("-listenIP", function("_panel_initial_config") + function("configure_xui_db"))
        self.assertEqual(values["subListen"], "127.0.0.1")

    def test_nginx_internal_tls_listeners_are_loopback_only(self):
        for target, port in ((MAIN, "7443"), ('cat > "/etc/nginx/sites-available/${reality_domain}"', "9443")):
            with self.subTest(port=port):
                self.assertEqual(re.findall(r"(?m)^\s*listen\s+(\S+)", render(target)), ["127.0.0.1:" + port])
        stream = render("cat > /etc/nginx/stream-enabled/stream.conf")
        self.assertRegex(stream, r"\blisten\s+443;")
        self.assertIn("127.0.0.1:8443", stream)
        self.assertIn("127.0.0.1:7443", stream)

    def release_stage(self, failure=""):
        # Execute only the isolated download/extraction block, relocated into a
        # private directory. curl/systemctl are mocked; tar and sha256sum are real.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            destination = root / "local"
            destination.mkdir()
            (destination / "x-ui").mkdir()
            (destination / "x-ui" / "old-state").write_text("preserve until verified")
            cli = root / "x-ui.sh"
            cli.write_text("#!/bin/bash\necho verified-release\n")
            archive = root / "fixture.tar.gz"
            with tarfile.open(archive, "w:gz") as bundle:
                bundle.add(cli, arcname="x-ui/x-ui.sh")
            checksum = root / "fixture.sha256"
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            checksum.write_text(("0" * 64 if failure == "mismatch" else "bad-hash" if failure == "malformed" else digest) + "  x-ui-linux-amd64.tar.gz\n")
            log = root / "calls"
            log.write_text("")
            panel = function("install_panel")
            block = panel[panel.index('    archive=$(mktemp'):panel.index('    cd x-ui')]
            block = block.replace("/usr/local", str(destination))
            mock = '''_arch() { echo amd64; }
msg_err() { echo "$*" >&2; }
systemctl() { :; }
curl() {
    local url="" output=""
    while [ "$#" -gt 0 ]; do
        case "$1" in
            -o) output="$2"; shift 2 ;;
            https://*) url="$1"; shift ;;
            *) shift ;;
        esac
    done
    printf '%s\\n' "$url" >> "$RELEASE_LOG"
    if [[ "$url" == *.sha256 ]]; then
        [[ "$RELEASE_FAILURE" != checksum-download ]] || { echo partial > "$output"; return 22; }
        cp "$CHECKSUM_FIXTURE" "$output"
    else
        [[ "$RELEASE_FAILURE" != archive-download ]] || { echo partial > "$output"; return 22; }
        cp "$ARCHIVE_FIXTURE" "$output"
    fi
}
tar() { echo extract >> "$RELEASE_LOG"; command tar "$@"; }
stage() {
'''
            result = subprocess.run(["bash", "-u", "-c", mock + function("_download_panel_archive") + "\n" + block + "\n}\nstage"],
                                    capture_output=True, text=True, env={**os.environ,
                                    "RELEASE_LOG": str(log), "RELEASE_FAILURE": failure,
                                    "ARCHIVE_FIXTURE": str(archive), "CHECKSUM_FIXTURE": str(checksum), "tag_version": "v3.9.0"})
            calls = log.read_text().splitlines()
            self.assertFalse(list(destination.glob("x-ui-release.*")), "Candidates/sidecars must not survive failure or extraction")
            if failure:
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("extract", calls, "Unverified archive must not reach tar")
                self.assertTrue((destination / "x-ui" / "old-state").exists())
            else:
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertEqual((destination / "x-ui" / "x-ui.sh").read_bytes(), cli.read_bytes())
                self.assertEqual(calls[-1], "extract")
            return calls

    def test_release_checksum_success_extracts_verified_bundled_cli(self):
        self.release_stage()

    def test_release_checksum_mismatch_or_invalid_digest_aborts_before_extraction(self):
        for failure in ("mismatch", "malformed"):
            with self.subTest(failure=failure):
                self.release_stage(failure)

    def test_release_download_failure_aborts_before_extraction(self):
        for failure in ("archive-download", "checksum-download"):
            with self.subTest(failure=failure):
                self.release_stage(failure)

    def test_ip_discovery_fallback_is_https_bounded_and_validated(self):
        self.assertNotRegex(SOURCE, r"(?<!https://)(?:ipv4|ipv6)\.icanhazip\.com")
        declarations = '\n'.join(line for line in SOURCE.splitlines() if line.startswith(('IP4_REGEX=', 'IP6_REGEX=')))
        mock = '''ip() { return 1; }
curl() {
    printf '%s\\n' "$*" >> "$IP_TEST_LOG"
    [[ "$IP_BAD_RESULT" == 0 ]] || { echo invalid; return 22; }
    case "${@: -1}" in
        https://ipv4.icanhazip.com) echo 203.0.113.5 ;;
        https://ipv6.icanhazip.com) echo 2001:db8::5 ;;
        *) return 1 ;;
    esac
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "calls"
            for bad in ("0", "1"):
                with self.subTest(bad=bad):
                    log.write_text("")
                    result = subprocess.check_output(["bash", "-u", "-c", mock + declarations + '\n' + function("get_server_ip") + '\nget_server_ip\nprintf "%s|%s" "$IP4" "$IP6"'],
                                                     text=True, env={**os.environ, "IP_TEST_LOG": str(log), "IP_BAD_RESULT": bad})
                    self.assertEqual(result, "203.0.113.5|2001:db8::5" if bad == "0" else "|")
                    for call in log.read_text().splitlines():
                        self.assertIn("-fsS", call)
                        self.assertIn("--connect-timeout 5", call)
                        self.assertIn("--max-time 10", call)

    def test_nginx_syntax(self):
        nginx = os.environ.get("NGINX_BIN") or shutil.which("nginx")
        if not nginx:
            self.skipTest("nginx unavailable; CI sets NGINX_BIN to require this check")
        version = subprocess.run([nginx, "-v"], check=True, capture_output=True, text=True)
        number = tuple(map(int, re.search(r"nginx/(\d+)\.(\d+)\.(\d+)", version.stderr).groups()))
        http2 = {"http2_listen": "", "http2_on": "http2 on;"} if number >= (1, 25, 1) else {}
        with tempfile.TemporaryDirectory(prefix="personal-xhttp-") as tmp:
            root = Path(tmp)
            subprocess.run([
                "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                "-subj", "/CN=deploy.example", "-keyout", str(root / "key.pem"),
                "-out", str(root / "cert.pem"),
            ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            for source in (SOURCE, PATCH):
                (root / "includes.conf").write_text(render(SHARED, source))
                fragments = [render("cat > /etc/nginx/sites-available/00-maps.conf", source)]
                for target in (MAIN, 'cat > "/etc/nginx/sites-available/${reality_domain}"'):
                    fragment = render(target, source, **http2)
                    fragment = re.sub(r"/etc/letsencrypt/live/[^/]+/fullchain.pem", str(root / "cert.pem"), fragment)
                    fragment = re.sub(r"/etc/letsencrypt/live/[^/]+/privkey.pem", str(root / "key.pem"), fragment)
                    fragments.append(fragment.replace("/etc/nginx/snippets/includes.conf", str(root / "includes.conf")))
                # Relocate only filesystem dependencies; generated HTTP directives stay intact.
                temp_paths = "".join(
                    f"{kind}_temp_path {root}/{kind};\n"
                    for kind in ("client_body", "proxy", "fastcgi", "uwsgi", "scgi")
                )
                (root / "nginx.conf").write_text(
                    f"pid {root}/nginx.pid;\nerror_log stderr;\nevents {{ worker_connections 32; }}\n"
                    "http {\naccess_log off;\n" + temp_paths + "\n".join(fragments) + "\n}\n"
                )
                result = subprocess.run(
                    [nginx, "-t", "-p", str(root) + "/", "-c", str(root / "nginx.conf")],
                    capture_output=True, text=True,
                )
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                print(f"\nnginx {'.'.join(map(str, number))}: generated HTTP vhosts syntax OK")


if __name__ == "__main__":
    unittest.main(verbosity=2)
