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
    "gid_col": "", "gid_reality": "", "gid_ws": "", "gid_xhttp": "", "gid_trojan": "",
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


def inbounds(**variables):
    sql = render("sqlite3 $XUIDB", **variables)
    blocks = re.findall(r'^INSERT INTO "inbounds"\s.*?^\);', sql, re.M | re.S)
    if len(blocks) != 4:
        raise AssertionError(f"Expected four inbound INSERTs, found {len(blocks)}")
    columns = re.search(r'\(("user_id".*?)\)\s*VALUES', blocks[0], re.S).group(1)
    with sqlite3.connect(":memory:") as db:
        db.row_factory = sqlite3.Row
        db.execute("CREATE TABLE inbounds (" + columns + ")")
        for block in blocks:
            db.execute(block)
        return {row["remark"].split()[-1]: dict(row) for row in db.execute("SELECT * FROM inbounds")}


SHARED = "cat > /etc/nginx/snippets/includes.conf"
MAIN = 'cat > "/etc/nginx/sites-available/${domain}"'


class PersonalXHTTP(unittest.TestCase):
    def test_shell_syntax(self):
        subprocess.run(["bash", "-n", str(ROOT / "x-ui-latest.sh")], check=True)

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
        self.assertEqual(set(rows), {"reality", "ws", "xhttp", "trojan-grpc"})
        self.assertEqual(rows["reality"]["enable"], "1")
        self.assertEqual(rows["ws"]["enable"], "0")
        self.assertEqual(rows["trojan-grpc"]["enable"], "0")
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
