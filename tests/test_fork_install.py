"""Exercise the README installer's wizard without installing or using the network."""

import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "install.sh"
TOKEN = "local_fixture_" + "x" * 26
SUCCESS = {"success": True, "errors": [], "messages": []}


def cloudflare(result):
    return json.dumps({**SUCCESS, "result": result}, indent=2)


def run_wizard(answers="", **overrides):
    source, boundary, _ = SCRIPT.read_text().partition("# [2] SYSTEM DEPENDENCIES")
    assert boundary
    source = re.sub(r"^\[\[ \$EUID -ne 0 \]\].*?\n", "", source, count=1, flags=re.M)
    with tempfile.TemporaryDirectory() as fixture:
        release = Path(fixture, "os-release")
        release.write_text('ID=ubuntu\nVERSION_ID=24.04\nPRETTY_NAME="Ubuntu 24.04"\n')
        source = source.replace("/etc/os-release", str(release))
        env = {
            "PATH": os.environ["PATH"],
            "HOME": fixture,
            "LC_ALL": "C",
            "DOMAIN": "sub.example.com",
            "CF_TOKEN": TOKEN,
            "PATH_CHOICE": "1",
            "MGMT_CHOICE": "1",
            "CF_VERIFY_REPLY": cloudflare({"status": "active"}),
            "CF_ZONE_REPLY": cloudflare([{"id": "a" * 32, "name": "example.com"}]),
            "CF_DNS_REPLY": cloudflare([]),
            "IP_REPLY": "8.8.4.4",
            **overrides,
        }
        stubs = r'''
systemctl() { printf '255\n'; }
uname() { printf 'x86_64\n'; }
df() { printf 'Available\n10000000\n'; }
mkdir() { :; }
ss() { printf '%s' "${TEST_LISTENERS:-}"; return "${TEST_SS_STATUS:-0}"; }
curl() {
    local args="$*" reply code=200
    case "$args" in
        *icanhazip.com*|*ifconfig.me*) printf '%s\n' "$IP_REPLY"; return ;;
    esac
    case "$args" in
        *local_fixture_*) return 97 ;;
    esac
    if [[ "${TEST_NETWORK_FAILURE:-}" == yes ]]; then
        return 28
    fi
    if [[ "$args" == *'--header @-'* ]]; then
        local auth
        IFS= read -r auth || return 96
        [[ "$auth" == 'Authorization: Bearer local_fixture_'* ]] || return 95
    fi
    case "$args" in
        */tokens/verify*) reply=$CF_VERIFY_REPLY; code=${VERIFY_CODE:-200} ;;
        */zones/*/dns_records*) reply=$CF_DNS_REPLY ;;
        */zones*name=example.com*) reply=$CF_ZONE_REPLY ;;
        */zones*name=xn--e1afmkfd.xn--p1ai*) reply='{"success": true, "result": [{"id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", "name": "xn--e1afmkfd.xn--p1ai"}]}' ;;
        */zones*) reply='{"success": true, "result": []}' ;;
        *) return 94 ;;
    esac
    printf '%s' "$reply"
    if [[ "$args" == *'%{http_code}'* ]]; then printf '\n%s' "$code"; fi
    return 0
}
'''
        done = r'''
printf '\nREADY|%s|%s|%s|%s|%s|%s\n' "$DOMAIN" "$PANEL_PORT" "$MGMT_PORT" "$PANEL_PATH" "$SUB_PATH" "$CASCADE_SUFFIX"
printf 'PORTS|%s\n' "$PANEL_PORT $MGMT_PORT $SUB_PORT $WS_PORT $CASCADE_WS_PORT $TROJAN_PORT"
for length in 4 6 8 10 14 16; do
    for (( sample=0; sample<8; sample++ )); do
        value=$(gen_random_string "$length")
        [[ ${#value} -eq $length && "$value" =~ ^[a-zA-Z0-9]+$ ]] || exit 91
    done
done
'''
        return subprocess.run(
            ["bash", "-c", stubs + source + done],
            input=answers,
            env=env,
            capture_output=True,
            text=True,
            timeout=12,
            start_new_session=True,
        )


class ForkInstallerTest(unittest.TestCase):
    def ready(self, result):
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        match = re.search(r"^READY\|(.+)$", result.stdout, re.M)
        self.assertIsNotNone(match, result.stdout)
        return match.group(1).split("|")

    def stopped(self, result, message):
        self.assertNotEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("READY|", result.stdout)
        self.assertIn(message, result.stdout + result.stderr)

    def test_automatic_prefix_reaches_installation_with_exact_random_lengths(self):
        for _ in range(3):
            result = run_wizard()
            self.assertRegex(self.ready(result)[-1], r"^[A-Za-z]{5}$")
            ports = re.search(r"^PORTS\|(.+)$", result.stdout, re.M).group(1).split()
            self.assertEqual(len(ports), len(set(ports)))
            self.assertTrue(all(port not in {"18080", "18082", "43935"} for port in ports))

    def test_manual_retries_menu_and_defaults_on_enter(self):
        result = run_wizard("3\n2\n" + "\n" * 8 + "2\n" + "\n" * 4,
                            PATH_CHOICE="", MGMT_CHOICE="")
        self.assertEqual(self.ready(result)[1:5], ["2053", "54892", "/xui/", "/sub/"])
        self.assertIn("1 или 2", result.stdout)

    def test_eof_stops_instead_of_repeating_required_question(self):
        self.stopped(run_wizard(DOMAIN=""), "EOF")

    def test_normalizes_idn_url_outer_invisible_spaces_and_dns_dot(self):
        result = run_wizard(DOMAIN="\u00a0\u200bHTTPS://пример.рф.:00443/a?b=1\ufeff")
        self.assertEqual(self.ready(result)[0], "xn--e1afmkfd.xn--p1ai")

    def test_rejects_invalid_hosts_without_rewriting_them(self):
        for domain in ("sub.ex ample.com", "192.0.2.10", "user@sub.example.com",
                       "https://sub.example.com:bad/", "sub.example.com.."):
            with self.subTest(domain=domain):
                self.stopped(run_wizard(DOMAIN=domain), "доменное имя")

    def test_manual_ports_are_decimal_and_conflicting_paths_retry(self):
        result = run_wizard("2\n/same\n\u00a002058\u00a0\n/same/\n/sub/\n" + "\n" * 5
                            + "2\n43935\n2058\n054892\n" + "\n" * 3,
                            PATH_CHOICE="", MGMT_CHOICE="")
        self.assertEqual(self.ready(result)[1:5], ["2058", "54892", "/same/", "/sub/"])

    def test_bad_overrides_and_failed_socket_snapshot_stop_before_installation(self):
        fixtures = [
            ({"PANEL_PORT": "43935"}, "порт"),
            ({"PANEL_PORT": "2053", "TEST_LISTENERS": "tcp LISTEN 0 64 127.0.0.1:2053 0.0.0.0:*"}, "порт"),
            ({"TEST_LISTENERS": "udp UNCONN 0 0 127.0.0.1:57998 0.0.0.0:*"}, "порт"),
            ({"TEST_SS_STATUS": "1"}, "порт"),
            ({"PANEL_PATH": " / "}, "путь"),
            ({"PANEL_PATH": "/assets/status"}, "путь"),
            ({"TG_PATH": "/api", "WS_PATH": "/api2"}, "путь"),
            ({"ADH_DOH_PATH": "/do h"}, "путь"),
        ]
        for overrides, message in fixtures:
            with self.subTest(overrides=overrides):
                self.stopped(run_wizard(**overrides), message)

    def test_cloudflare_rejects_invalid_http_json_status_zone_and_network(self):
        fixtures = [
            {"VERIFY_CODE": "403"},
            {"CF_VERIFY_REPLY": "not JSON"},
            {"CF_VERIFY_REPLY": cloudflare({"status": "disabled"})},
            {"CF_ZONE_REPLY": cloudflare([])},
            {"CF_DNS_REPLY": '{"success": false, "result": null}'},
            {"TEST_NETWORK_FAILURE": "yes"},
        ]
        for overrides in fixtures:
            with self.subTest(overrides=overrides):
                result = run_wizard(**overrides)
                self.stopped(result, "Ошибка Cloudflare")
                self.assertNotIn(TOKEN, result.stdout + result.stderr)

    def test_noninteractive_uses_private_token_file_and_rejects_public_mode(self):
        with tempfile.TemporaryDirectory() as fixture:
            token_file = Path(fixture, "token")
            token_file.write_text(f'\ufeff"{TOKEN}"\u00a0\n')
            options = {
                "INSTALL_NONINTERACTIVE": "1", "CF_TOKEN": "", "CF_TOKEN_FILE": str(token_file),
                "PATH_CHOICE": "", "MGMT_CHOICE": "",
            }
            token_file.chmod(0o600)
            self.ready(run_wizard(**options))
            token_file.chmod(0o644)
            self.stopped(run_wizard(**options), "CF_TOKEN_FILE")

    def test_invalid_public_ip_fails_instead_of_using_loopback(self):
        self.stopped(run_wizard(IP_REPLY="127.0.0.1"), "IPv4")


if __name__ == "__main__":
    unittest.main()
