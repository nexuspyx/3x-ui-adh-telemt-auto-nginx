import contextlib
import importlib.util
import io
import json
from pathlib import Path
import runpy
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch


ASSETS = Path(__file__).resolve().parents[1] / "assets/server-manager"
PYTHON = ASSETS / "server_manager.py"
SPEC = importlib.util.spec_from_file_location("server_manager", PYTHON)
manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


class ForkServerManager(unittest.TestCase):
    def setUp(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.config = root / "config.json"
        self.timer = root / "auto_lock.timer"
        self.cert_root = root / "certificates"
        self.config.write_text(json.dumps({
            "domain": "example.test", "mgmt_port": 54892, "mgmt_secret_path": "cfg-admin",
            "auth_user": "admin", "auth_pass_hash": "a" * 64,
        }))
        self.enterContext(patch.multiple(manager, CONFIG_FILE=str(self.config), AUTO_LOCK_FILE=str(self.timer)))
        self.enterContext(patch.object(manager, "TLS_CERT_ROOT", str(self.cert_root), create=True))
        self.log = self.enterContext(contextlib.redirect_stdout(io.StringIO()))
        self.errors = self.enterContext(contextlib.redirect_stderr(io.StringIO()))
        self.ufw_active = True
        self.daemon_active = True
        self.fail_add = False
        self.fail_delete = False
        self.commands = []
        self.rules = {"2200/tcp": "SSH", "443/tcp": "Nginx"}
        self.enterContext(patch.object(manager.subprocess, "run", side_effect=self.system_command))

    def system_command(self, args, **kwargs):
        self.commands.append(args)
        status, output, error = 0, "", ""
        if args == ["systemctl", "is-active", "server-manager"]:
            status = 0 if self.daemon_active else 3
            output = "active\n" if self.daemon_active else "inactive\n"
        elif args == ["ufw", "status", "numbered"]:
            output = "Status: active\n" if self.ufw_active else "Status: inactive\n"
            if self.ufw_active:
                for index, (rule, comment) in enumerate(self.rules.items(), 1):
                    source = "Anywhere (v6)" if rule.endswith(" (v6)") else "Anywhere"
                    output += f"[{index:2d}] {rule:25} ALLOW IN   {source:25} # {comment}\n"
        elif args == ["ufw", "allow", "54892/tcp", "comment", "Server Manager"]:
            self.assertTrue(self.timer.exists(), "A timer must exist before UFW can open the port")
            self.rules["54892/tcp"] = "Server Manager"
            if self.fail_add:
                status, error = 1, "fixture: partial add failure"
        elif args[:5] == ["ufw", "delete", "allow", "proto", "tcp"] and len(args) == 11:
            address = args[6]
            rule = "54892/tcp (v6)" if address == "::/0" else "54892/tcp"
            self.assertEqual(args[5:], ["from", address, "to", "any", "port", "54892"])
            self.assertIn(address, ("0.0.0.0/0", "::/0"))
            if self.fail_delete:
                status, error = 1, "fixture: delete failure"
            else:
                del self.rules[rule]
        elif args in (["nginx", "-t"], ["systemctl", "reload", "nginx"],
                      ["systemctl", "reset-failed", "x-ui"], ["systemctl", "restart", "x-ui"]):
            pass
        else:
            status, error = 1, "unexpected command in fixture"
        result = subprocess.CompletedProcess(args, status, output, error)
        if kwargs.get("check"):
            result.check_returncode()
        return result

    def test_missing_corrupt_or_invalid_config_fails_closed(self):
        for config in ('{"unclosed":', '[]', '{}', '{"mgmt_port": true}', None):
            with self.subTest(config=config):
                if config is None:
                    self.config.rename(self.config.with_suffix(".saved"))
                else:
                    self.config.write_text(config)
                with self.assertRaisesRegex(ValueError, "config.json"):
                    manager.load_master_config()

    def test_legacy_numeric_string_and_plain_credentials(self):
        cfg = json.loads(self.config.read_text())
        cfg["mgmt_port"] = "054892"
        cfg["auth_pass_hash"] = ""
        cfg["auth_pass_plain"] = "fixture-password"
        self.config.write_text(json.dumps(cfg))
        parsed = manager.load_master_config()
        self.assertEqual(parsed["mgmt_port"], 54892)
        self.assertEqual(parsed["auth_pass_plain"], "fixture-password")

    def test_missing_and_unloadable_tls_never_bind_or_start_monitor(self):
        live = self.cert_root / "example.test"
        live.mkdir(parents=True)
        for missing in (True, False):
            with self.subTest(missing=missing), patch.object(manager, "ThreadingHTTPServer") as httpd, \
                    patch.object(manager.threading, "Thread") as monitor:
                if not missing:
                    (live / "fullchain.pem").write_text("unloadable certificate fixture")
                    (live / "privkey.pem").write_text("unloadable key fixture")
                self.assertEqual(manager.run_server(), 1)
                self.assertIn("TLS", self.errors.getvalue())
                httpd.assert_not_called()
                monitor.assert_not_called()

    def test_server_wraps_listener_before_serving(self):
        with patch.object(manager.ssl, "SSLContext") as ssl_context, \
                patch.object(manager, "ThreadingHTTPServer") as httpd, \
                patch.object(manager.threading, "Thread") as monitor:
            server = httpd.return_value.__enter__.return_value
            context = ssl_context.return_value
            listener = server.socket
            server.serve_forever.side_effect = lambda: self.assertIs(server.socket, context.wrap_socket.return_value)
            self.assertEqual(manager.run_server(), 0)
            context.load_cert_chain.assert_called_once_with(
                certfile=str(self.cert_root / "example.test/fullchain.pem"),
                keyfile=str(self.cert_root / "example.test/privkey.pem"),
            )
            context.wrap_socket.assert_called_once_with(listener, server_side=True)
            server.serve_forever.assert_called_once()
            monitor.return_value.start.assert_called_once()

    def test_active_ufw_open_records_actual_timer_and_preserves_ssh(self):
        start = time.time()
        self.assertTrue(manager.unlock_firewall(2))
        self.assertGreaterEqual(float(self.timer.read_text()), start + 120)
        self.assertEqual(self.rules, {
            "2200/tcp": "SSH", "443/tcp": "Nginx", "54892/tcp": "Server Manager",
        })

    def test_inactive_ufw_or_daemon_cannot_open_port(self):
        for self.ufw_active, self.daemon_active in ((False, True), (True, False)):
            with self.subTest(ufw=self.ufw_active, daemon=self.daemon_active):
                self.assertFalse(manager.unlock_firewall())
                self.assertFalse(self.timer.exists())
                self.assertNotIn("54892/tcp", self.rules)

    def test_untagged_management_allow_is_neither_claimed_nor_deleted(self):
        self.rules["54892/tcp"] = "Operator allow"
        self.assertFalse(manager.unlock_firewall())
        self.assertTrue(manager.lock_firewall())
        self.assertFalse(self.timer.exists())
        self.assertEqual(self.rules["54892/tcp"], "Operator allow")

    def test_lock_removes_only_tagged_management_allow_for_its_address_family(self):
        self.rules.update({"54892/tcp": "Server Manager", "54892/tcp (v6)": "Operator allow"})
        self.timer.write_text("1")
        self.assertTrue(manager.lock_firewall())
        self.assertFalse(self.timer.exists())
        self.assertEqual(self.rules, {"2200/tcp": "SSH", "443/tcp": "Nginx", "54892/tcp (v6)": "Operator allow"})

    def test_failed_close_preserves_timer_for_retry(self):
        self.rules["54892/tcp (v6)"] = "Server Manager"
        self.timer.write_text("1")
        self.fail_delete = True
        self.assertFalse(manager.lock_firewall())
        self.assertEqual(self.timer.read_text(), "1")
        self.assertIn("54892/tcp (v6)", self.rules)
        self.fail_delete = False
        self.assertTrue(manager.lock_firewall())
        self.assertFalse(self.timer.exists())
        self.assertNotIn("54892/tcp (v6)", self.rules)

    def test_partial_open_failure_leaves_expired_timer_for_cleanup(self):
        self.fail_add = True
        self.assertFalse(manager.unlock_firewall())
        self.assertIn("54892/tcp", self.rules)
        self.assertLessEqual(float(self.timer.read_text()), time.time())

    def test_logout_reports_failed_lock_and_ends_session(self):
        handler = object.__new__(manager.ServerManagerHandler)
        handler.path = "/cfg-admin/api/logout"
        handler.headers = {"Cookie": "mgmt_session=fixture-session"}
        handler.send_json = Mock()
        self.rules["54892/tcp"] = "Server Manager"
        self.timer.write_text("1")
        self.fail_delete = True
        sessions = {"fixture-session": {"expires": time.time() + 60}}
        with patch.object(manager, "SESSIONS", sessions), patch.object(manager.threading, "Thread"):
            handler.do_POST()
        self.assertEqual(handler.send_json.call_args.kwargs.get("code", 200), 503)
        self.assertFalse(handler.send_json.call_args.args[0]["success"])
        self.assertEqual(sessions, {})
        self.assertEqual(self.timer.read_text(), "1")

    def test_renewed_timer_is_not_closed_by_monitor(self):
        self.assertTrue(manager.unlock_firewall(2))
        self.assertTrue(manager.lock_firewall(expired_only=True))
        self.assertEqual(self.rules["54892/tcp"], "Server Manager")
        self.assertTrue(self.timer.exists())

    def test_python_command_exits_nonzero_on_failed_firewall_operation(self):
        self.ufw_active = False
        script = self.config.with_name("manager.py")
        script.write_text(PYTHON.read_text().replace("/etc/server-manager/", str(self.config.parent) + "/"))
        for action in ("open", "close"):
            with self.subTest(action=action), patch.object(sys, "argv", [str(script), action]):
                with self.assertRaises(SystemExit) as raised:
                    runpy.run_path(str(script), run_name="__main__")
                self.assertEqual(raised.exception.code, 1)

    def test_shell_command_propagates_failure_and_never_bypasses_python(self):
        failure_stub = self.config.with_suffix(".py")
        failure_stub.write_text('raise SystemExit("fixture: operation failed")\n')
        source = (ASSETS / "server-mgr").read_text()
        source = source.replace("$EUID -ne 0", "$EUID -lt 0").replace(
            "/etc/server-manager/config.json", str(self.config)).replace(
            "/usr/local/bin/server-manager.py", str(failure_stub))
        source = "ufw() { echo 'unexpected shell UFW call'; return 1; }\n" + source
        for action in ("open", "close"):
            with self.subTest(action=action):
                result = subprocess.Popen(["bash", "-c", source, "server-mgr", action],
                                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                stdout, stderr = result.communicate(timeout=5)
                self.assertEqual(result.returncode, 1, stdout)
                self.assertIn("fixture: operation failed", stderr)
                self.assertNotIn("unexpected shell UFW call", stdout)
                self.assertNotIn("Авто-таймер", stdout)

    def test_update_panel_port_preserves_nginx_upstream_scheme(self):
        root = self.config.parent
        db = root / "x-ui.db"
        nginx = root / "nginx"
        nginx.mkdir()
        vhost = nginx / "example.test.conf"
        environment = {"__name__": "relocated_manager"}
        source = PYTHON.read_text().replace("/etc/nginx/sites-available", str(nginx)).replace(
            "/tmp/server_mgr_backup_", str(root / "backup_")).replace("/etc/x-ui/x-ui.db", str(db)).replace(
            "/etc/telemt/config.toml", str(root / "telemt.toml")).replace(
            "/opt/AdGuardHome/AdGuardHome.yaml", str(root / "adguard.yaml"))
        exec(compile(source, str(PYTHON), "exec"), environment)
        with contextlib.closing(sqlite3.connect(db)) as conn, conn:
            conn.execute("CREATE TABLE settings(key TEXT, value TEXT)")
            conn.execute("CREATE TABLE inbounds(id INTEGER, protocol TEXT, port INTEGER, remark TEXT, settings TEXT, stream_settings TEXT, enable INTEGER)")
            conn.execute("INSERT INTO settings VALUES('webPort', '2053'), ('webBasePath', '/panel/')")
        for scheme in ("http", "https"):
            with self.subTest(scheme=scheme):
                vhost.write_text(f"# === [1] 3X-UI WEB PANEL ===\nproxy_pass {scheme}://127.0.0.1:2053;\n")
                success, message = environment["apply_service_changes"]("xui_panel", 2153, None, {"domain": "example.test"})
                self.assertTrue(success, message)
                self.assertIn(f"proxy_pass {scheme}://127.0.0.1:2153;", vhost.read_text())
                self.assertEqual(environment["detect_live_services"]({"domain": "example.test"})["xui_panel"]["current_port"], "2153")


if __name__ == "__main__":
    unittest.main()
