"""Validate fresh-install templates without sourcing or running the installer."""

import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "x-ui-latest.sh").read_text()
FIXTURE = {
    "domain": "deploy.example", "reality_domain": "cover.example",
    "xhttp_path": "Session7AbC", "sub_path": "subscription", "json_path": "jsonsub",
    "panel_path": "panel", "ws_path": "websocket", "trojan_path": "trojan",
    "sub_port": "10001", "panel_port": "10002", "ws_port": "10003",
    "trojan_port": "10004", "mtr_backend_port": "10005",
    "diag_path": "/diagnostics/", "diag_token": "test-token", "emoji_flag": "test",
    "private_key": "test-private", "public_key": "test-public",
    "sub_uri": "https://deploy.example/subscription/",
    "json_uri": "https://deploy.example/jsonsub?name=",
    "gid_col": "", "gid_reality": "", "gid_ws": "", "gid_xhttp": "", "gid_trojan": "", "gid_hysteria": "",
    "http2_listen": " http2", "http2_on": "",
}


def template(target):
    """Extract one original heredoc; never execute its surrounding function."""
    matches = re.findall(r"^\s*" + re.escape(target) + r" <<EOF\n(.*?)^EOF$", SOURCE, re.M | re.S)
    if len(matches) != 1:
        raise AssertionError(f"Expected one heredoc for {target}, found {len(matches)}")
    return matches[0]


def render(target, **variables):
    script = "shor=(a b c d e f g h)\ncat <<EOF\n" + template(target) + "EOF\n"
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
                    "SSHD_OUTPUT": sshd, "SSHD_EXIT": sshd_exit, "FAIL_UFW": fail,
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
        self.assertNotIn("https://raw.githubusercontent.com/MHSanaei/3x-ui/main/x-ui.sh", panel)
        self.assertIn('https://raw.githubusercontent.com/MHSanaei/3x-ui/${tag_version}/x-ui.sh', panel)
        downloads = re.search(r"    wget -N .*?(?=\n    \[\[ -d /usr/local/x-ui/)", panel, re.S).group()
        mock = '''_arch() { echo amd64; }
wget() {
    local url="${@: -1}"
    echo "$url" >> "$DOWNLOAD_TEST_LOG"
    [[ "$FAIL_CLI" != 1 || "$url" != */x-ui.sh ]]
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "urls"
            for tag, fail in (("v3.9.0", "0"), ("v9.8.7", "0"), ("v9.8.7", "1")):
                with self.subTest(tag=tag, fail=fail):
                    log.write_text("")
                    result = subprocess.run(["bash", "-u", "-c", mock + downloads + "\ntrue\n"], text=True, capture_output=True,
                                            env={**os.environ, "tag_version": tag, "FAIL_CLI": fail, "DOWNLOAD_TEST_LOG": str(log)})
                    self.assertEqual(result.returncode, int(fail), result.stdout + result.stderr)
                    self.assertEqual(log.read_text().splitlines(), [
                        f"https://github.com/MHSanaei/3x-ui/releases/download/{tag}/x-ui-linux-amd64.tar.gz",
                        f"https://raw.githubusercontent.com/MHSanaei/3x-ui/{tag}/x-ui.sh",
                    ])

    def test_setup_cron_without_scheduled_restart(self):
        functions = re.findall(r"^setup_cron\(\)\s*\{.*?^\}", SOURCE, re.M | re.S)
        self.assertEqual(len(functions), 1, "Expected one setup_cron function")
        monthly = (
            '@monthly certbot renew --non-interactive --pre-hook "systemctl stop nginx" '
            '--post-hook "systemctl start nginx" > /dev/null 2>&1'
        )
        preserved = "@hourly /usr/local/bin/backup"
        legacy = (
            "@daily x-ui restart > /dev/null 2>&1 && nginx -s reload\n"
            "0 3 * * * systemctl restart x-ui\n"
            "@monthly certbot renew --old-option\n"
            "@daily /opt/cloudflareips\n" + preserved + "\n"
        )
        # Mock only crontab; run the isolated function, never the installer/main.
        mock = '''crontab() {
    case "$1" in
        -l) cat "$CRON_TEST_FILE" ;;
        -) cat > "$CRON_TEST_FILE.new"; mv "$CRON_TEST_FILE.new" "$CRON_TEST_FILE" ;;
        *) return 2 ;;
    esac
}
'''
        for initial, expected in (("", [monthly]), (legacy, [preserved, monthly])):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as tmp:
                cron = Path(tmp) / "crontab"
                cron.write_text(initial)
                subprocess.run(
                    ["bash", "-eu", "-c", mock + functions[0] + "\nsetup_cron\n"],
                    check=True, env={**os.environ, "CRON_TEST_FILE": str(cron)},
                )
                # No replacement restart/reload job may be added, at any schedule.
                self.assertEqual(cron.read_text().splitlines(), expected)

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
        self.assertEqual((row["port"], row["listen"], row["tag"]), ("8443", "", "inbound-8443"))
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
            "set_real_ip_from 127.0.0.1;", "listen 7443 ssl http2 proxy_protocol;",
        ):
            self.assertIn(directive, main)
        stream = render("cat > /etc/nginx/stream-enabled/stream.conf")
        self.assertIn("proxy_protocol on;", stream)
        self.assertIn("cover.example    xray;", stream)
        self.assertIn("deploy.example            www;", stream)

    def test_personal_runtime_source(self):
        for name in ("x-ui-latest.sh", "x-ui-patch.sh"):
            source = (ROOT / name).read_text()
            self.assertEqual(re.findall(r'^GITHUB_RAW="(.*)"$', source, re.M), [
                "https://raw.githubusercontent.com/xPROMSx/3x-ui-pro/personal",
            ])
            for line in source.splitlines():
                if "raw.githubusercontent.com/mozaroc/3x-ui-pro" in line:
                    self.assertTrue(line.lstrip().startswith("#"), "Unexpected upstream runtime source")

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
            (root / "includes.conf").write_text(render(SHARED))
            fragments = [render("cat > /etc/nginx/sites-available/00-maps.conf")]
            for target in (MAIN, 'cat > "/etc/nginx/sites-available/${reality_domain}"'):
                fragment = render(target, **http2)
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
