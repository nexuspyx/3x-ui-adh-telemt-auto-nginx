"""Focused v1.5 audit regressions, using the existing isolated fixtures."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import ssl
import subprocess
import tarfile
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from certificate_fixtures import relocate

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / 'x-ui-latest.sh').read_text()
BACKUP = (ROOT / 'assets/backup/x-ui-backup.sh').read_text()


def function(name, source=INSTALLER):
    return re.findall(r'^' + name + r'\(\)\s*\{.*?^\}', source, re.M | re.S)[0]


class AuditInstaller(unittest.TestCase):
    def flag(self, response, failure=False):
        script = ('curl() { printf "%s" "$RESPONSE"; return "$CURL_RESULT"; };\n' +
                  function('country_flag') + '\ncountry_flag')
        result = subprocess.run(['bash', '-eu', '-c', script], capture_output=True,
                                env={**os.environ, 'LC_ALL': 'C', 'RESPONSE': response,
                                     'CURL_RESULT': '1' if failure else '0'})
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.decode('utf-8')

    def test_country_iso_and_nonfatal_fallback(self):
        for code, expected in [('FI', '🇫🇮'), ('US', '🇺🇸'), ('DE', '🇩🇪')]:
            self.assertEqual(self.flag(json.dumps({'success': True, 'country_code': code})), expected)
        for payload in ('null', '[]', 'bad JSON', '{}', '{"success":false,"country_code":"FI"}'):
            self.assertEqual(self.flag(payload), '🌐')
        self.assertEqual(self.flag('{"success":true,"country_code":"FI"}', failure=True), '🌐')

    def test_hostile_country_cannot_inject_sqlite_cli(self):
        self.assertIn('emoji_flag=$(country_flag)',function('configure_xui_db'))
        self.assertNotIn('.flag.emoji',function('configure_xui_db'))
        from test_personal_xhttp import render
        sqlite = shutil.which('sqlite3')
        self.assertIsNotNone(sqlite, 'sqlite3 CLI is required for the injection regression')
        with tempfile.TemporaryDirectory() as tmp:
            marker = Path(tmp) / 'executed'
            hostile = ["FI'", 'FI\n.shell touch ' + str(marker),
                       "');\n.shell touch " + str(marker) + '\n--', '$(touch ' + str(marker) + ')']
            for value in hostile:
                with self.subTest(value=value):
                    emoji = self.flag(json.dumps({'success': True, 'country_code': value,
                                                  'flag': {'emoji': value}}))
                    self.assertEqual(emoji, '🌐')
                    sql = render('sqlite3 $XUIDB', emoji_flag=emoji)
                    blocks = re.findall(r'^INSERT INTO "inbounds"\s.*?^\);', sql, re.M | re.S)
                    columns = re.search(r'\(("user_id".*?)\)\s*VALUES', blocks[0], re.S)[1]
                    db = Path(tmp) / 'seed.db'
                    db.unlink(missing_ok=True)
                    result = subprocess.run([sqlite, str(db)], input='CREATE TABLE inbounds (id INTEGER PRIMARY KEY,' +
                                            columns + ');\n' + '\n'.join(blocks), text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    with sqlite3.connect(db) as conn:
                        self.assertEqual(conn.execute('SELECT count(*) FROM inbounds').fetchone()[0], 5)
                        self.assertTrue(all(row[0].startswith('🌐 ') for row in conn.execute('SELECT remark FROM inbounds')))
                    self.assertFalse(marker.exists())

    def test_initial_panel_loopback_persisted_before_start(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            binary = root / 'usr/local/x-ui/x-ui'
            binary.parent.mkdir(parents=True)
            db = root / 'x-ui.db'
            with sqlite3.connect(db) as conn:
                conn.executescript('CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT); CREATE TABLE settings(key TEXT,value TEXT);')
            # Model the actual setting/migrate CLI DB contract, not a fictional listen option.
            binary.write_text('''#!/usr/bin/env python3
import os,sqlite3,sys
if sys.argv[1]=='setting':
    args=sys.argv[2:]; values=dict(zip(args[::2],args[1::2]))
    with sqlite3.connect(os.environ['XUIDB']) as db:
        db.execute('INSERT INTO users(username) VALUES(?)',(values['-username'],))
        db.executemany('INSERT INTO settings VALUES(?,?)',[('webListen',''),('webPort',values['-port']),('webBasePath','/'+values['-webBasePath']+'/')])
''')
            binary.chmod(0o755)
            result = subprocess.run(['bash', '-eu', '-c', relocate(function('_panel_initial_config'), root) +
                                     '\n_panel_initial_config\n'], capture_output=True, text=True, env={
                                         **os.environ, 'XUIDB': str(db), 'config_username': 'random-user',
                                         'config_password': 'fixture-password', 'panel_port': '18443', 'panel_path': 'random-path'})
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            with sqlite3.connect(db) as conn:
                self.assertEqual(conn.execute("SELECT value FROM settings WHERE key='webListen'").fetchall(), [('127.0.0.1',)])
            self.assertNotRegex(function('install_panel'), r'systemctl\s+(?:start|restart)\s+x-ui')
            self.assertIn("'127.0.0.1'", function('configure_xui_db'))

    def test_xray_health_requires_live_sockets_and_managed_same_pid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            executable = root / 'usr/local/x-ui/bin/xray-linux-amd64'
            executable.parent.mkdir(parents=True)
            shutil.copy2(shutil.which('sleep'), executable)
            proc = subprocess.Popen([str(executable), '60'])
            self.addCleanup(lambda: proc.wait())
            self.addCleanup(proc.terminate)
            sockpath = root / 'dev/shm/uds2023.sock'
            sockpath.parent.mkdir(parents=True)
            sock = socket.socket(socket.AF_UNIX); sock.bind(str(sockpath)); sock.close()
            # The inode remains, but all health evidence comes from ss and /proc/exe.
            pid = proc.pid
            tcp = f'tcp LISTEN 0 128 127.0.0.1:8443 *:* users:(("xray",pid={pid},fd=2))'
            udp = f'udp UNCONN 0 128 *:443 *:* users:(("xray",pid={pid},fd=3))'
            unix = f'u_str LISTEN 0 128 {sockpath} 0 users:(("xray",pid={pid},fd=4))'
            body = function('check_xray_runtime').replace('/usr/local/x-ui/bin', str(executable.parent)).replace('/dev/shm/uds2023.sock', str(sockpath))
            ss = root / 'ss'
            ss.write_text('#!/bin/sh\nif [ "$2" = -lxnp ]; then printf "%s\\n" "$UNIX"; else printf "%s\\n" "$NETWORK"; fi\n')
            ss.chmod(0o755)
            script = ('systemctl() { return 0; }; sleep() { :; };\n' + body + '\ncheck_xray_runtime\n')
            cases = [(tcp + '\n' + udp, unix, True), ('', '', False), (udp, unix, False),
                     (tcp, unix, False), (tcp + '\n' + udp, '', False),
                     (tcp + '\n' + udp.replace(f'pid={pid}', 'pid=1'), unix, False)]
            for network, uds, expected in cases:
                with self.subTest(network=network, unix=uds):
                    result = subprocess.run(['bash', '-eu', '-c', script], text=True, capture_output=True,
                                            env={**os.environ, 'PATH': str(root) + ':' + os.environ['PATH'], 'NETWORK': network, 'UNIX': uds})
                    self.assertEqual(result.returncode == 0, expected, result.stderr)
                    self.assertTrue(sockpath.exists())

    def test_canonical_health_used_after_restart_resume_and_rollback(self):
        self.assertEqual(function('check_xray_runtime'), function('check_xray_runtime', BACKUP))
        self.assertEqual(function('render_certificate_hook'), function('render_certificate_hook', BACKUP))
        for name, source in [('check_installation', INSTALLER), ('check_health', BACKUP),
                             ('cmd_backup', BACKUP), ('cleanup', BACKUP), ('recover_restore_target', BACKUP)]:
            self.assertIn('check_xray_runtime', function(name, source))
        self.assertIn('declare -f check_xray_runtime', function('render_certificate_hook'))
        self.assertIn('if (( panel )); then check_xray_runtime; fi', function('render_certificate_hook'))

    def test_panel_renewal_fails_when_xray_does_not_resume(self):
        from certificate_fixtures import certificates
        from test_certificate_renewal import CertificateRenewal, MOCK
        f = CertificateRenewal('runTest'); f.setUp(); self.addCleanup(f.doCleanups)
        hook = certificates(f.root)
        (f.root/'health-bin/ss').write_text('#!/bin/sh\nexit 0\n')
        result = subprocess.run(['bash','-c',MOCK+'\nsleep() { :; }; source "$HOOK"'],
                                capture_output=True,text=True,env={**f.env,'HOOK':str(hook),
                                'RENEWED_DOMAINS':'example.com','RENEWED_LINEAGE':str(f.root/'etc/letsencrypt/live/example.com')})
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Xray must own',result.stderr)
        self.assertIn('systemctl restart x-ui',f.calls.read_text())

    def test_real_nginx_verified_https_api_token_and_http_subscriptions(self):
        from test_personal_xhttp import render, SHARED, MAIN
        nginx = os.environ.get('NGINX_BIN') or shutil.which('nginx')
        self.assertIsNotNone(nginx, 'real nginx is required')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(['openssl','req','-x509','-newkey','rsa:2048','-nodes','-days','1',
                            '-subj','/CN=deploy.example','-addext','subjectAltName=DNS:deploy.example',
                            '-keyout',str(root/'key.pem'),'-out',str(root/'cert.pem')], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            requests = []
            class Backend(BaseHTTPRequestHandler):
                def do_GET(self):
                    requests.append((self.server.kind, self.path, self.headers.get('Authorization'), self.headers.get('Host')))
                    body = json.dumps({'backend':self.server.kind,'path':self.path}).encode()
                    self.send_response(200); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
                def log_message(self, *args): pass
            panel = ThreadingHTTPServer(('127.0.0.1',0), Backend); panel.kind = 'panel-https'
            tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER); tls.load_cert_chain(root/'cert.pem',root/'key.pem')
            panel.socket = tls.wrap_socket(panel.socket, server_side=True)
            sub = ThreadingHTTPServer(('127.0.0.1',0), Backend); sub.kind = 'sub-http'
            for server in (panel, sub):
                thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
                self.addCleanup(server.server_close); self.addCleanup(server.shutdown)
            with socket.socket() as s:
                s.bind(('127.0.0.1',0)); public_port = s.getsockname()[1]
            shared = render(SHARED, panel_port=str(panel.server_port), sub_port=str(sub.server_port))
            self.assertEqual(shared.count(f'proxy_pass http://127.0.0.1:{sub.server_port};'),7)
            self.assertNotIn(f'proxy_pass https://127.0.0.1:{sub.server_port};',shared)
            (root/'includes.conf').write_text(shared)
            vhost = render(MAIN, panel_port=str(panel.server_port), sub_port=str(sub.server_port))
            self.assertIn(f'proxy_pass https://127.0.0.1:{panel.server_port};',vhost)
            # Fixture direct TLS entry bypasses only the outer stream/proxy-protocol hop.
            vhost = re.sub(r'127\.0\.0\.1:7443 ssl(?: http2)? proxy_protocol', f'127.0.0.1:{public_port} ssl', vhost)
            vhost = re.sub(r'/etc/letsencrypt/live/[^/]+/fullchain.pem', str(root/'cert.pem'), vhost)
            vhost = re.sub(r'/etc/letsencrypt/live/[^/]+/privkey.pem', str(root/'key.pem'), vhost)
            vhost = vhost.replace('/etc/nginx/snippets/includes.conf',str(root/'includes.conf')).replace('/etc/nginx/snippets/x-ui-auto-optional',str(root/'optional'))
            maps = render('cat > /etc/nginx/sites-available/00-maps.conf')
            (root/'nginx.conf').write_text(f'pid {root}/nginx.pid; error_log {root}/error.log; events {{}} http {{ access_log off; {maps}\n{vhost}\n}}')
            cmd = [nginx,'-p',str(root)+'/', '-c',str(root/'nginx.conf')]
            syntax = subprocess.run([*cmd,'-t'],capture_output=True,text=True)
            self.assertEqual(syntax.returncode,0,syntax.stderr)
            process = subprocess.Popen([*cmd,'-g','daemon off;'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            self.addCleanup(lambda: process.wait()); self.addCleanup(process.terminate)
            for _ in range(50):
                try:
                    with socket.create_connection(('127.0.0.1', public_port), timeout=.1): break
                except OSError: time.sleep(.02)
            for path, kind in [('/panel/panel/api/inbounds/list?node=Fixture&token=query','panel-https'),
                               ('/subscription/fixture','sub-http'),('/jsonsub?name=Fixture','sub-http')]:
                result = subprocess.run(['curl','--noproxy','*','-fsS','--max-time','5',
                                         '--cacert',str(root/'cert.pem'),'--resolve',f'deploy.example:{public_port}:127.0.0.1',
                                         '-H','Authorization: Bearer fixture-api-token',f'https://deploy.example:{public_port}{path}'],capture_output=True,text=True)
                self.assertEqual(result.returncode,0,result.stderr)
                self.assertEqual(json.loads(result.stdout), {'backend':kind,'path':path})
                self.assertEqual(requests[-1], (kind,path,'Bearer fixture-api-token','deploy.example'))
            self.assertNotIn('wrong version number',(root/'error.log').read_text().lower())


class AuditRecovery(unittest.TestCase):
    def setUp(self):
        from test_personal_backup import PersonalBackup
        self.fixture = PersonalBackup('runTest'); self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.env['NGINX_BIN'] = os.environ.get('NGINX_BIN') or shutil.which('nginx')
        self.fixture.write('/var/www/html/recovery-marker','original')

    def modified(self, archive, updates):
        f = self.fixture
        target = Path(f.temp.name) / ('modified-' + str(time.time_ns()) + '.tar.gz')
        with tarfile.open(archive) as src, tarfile.open(target,'w:gz') as dst:
            for entry in src:
                entry = copy.copy(entry)
                data = src.extractfile(entry).read() if entry.isfile() else None
                if entry.name in updates: data = updates[entry.name]
                if data is not None: entry.size = len(data)
                dst.addfile(entry, io.BytesIO(data) if data is not None else None)
        return target

    def assert_untouched(self, result, services):
        f = self.fixture
        self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertEqual(f.path('/var/www/html/recovery-marker').read_text(),'original')
        self.assertEqual(json.loads((f.root/'services.json').read_text()),services)
        self.assertFalse(any(c[:2] == ['systemctl','stop'] for c in f.commands()))
        self.assertNotIn('Restore completed successfully.',result.stdout)

    def test_broken_staged_nginx_rejected_before_any_service_stop(self):
        f = self.fixture; archive = f.backup()
        bad = self.modified(archive,{f.member('/etc/nginx/nginx.conf'):b'invalid_nginx_directive;\n'})
        digest = hashlib.sha256(bad.read_bytes()).hexdigest()
        services = json.loads((f.root/'services.json').read_text()); (f.root/'commands').write_text('')
        result = f.run_tool('restore',bad)
        self.assert_untouched(result,services)
        self.assertIn('Staged nginx -t failed',result.stderr)
        self.assertEqual(hashlib.sha256(bad.read_bytes()).hexdigest(),digest)

    def test_broken_staged_tls_and_hook_rejected_before_mutation(self):
        f = self.fixture; archive = f.backup()
        cases = [('/etc/letsencrypt/live/example.com/fullchain.pem',b'invalid PEM'),
                 ('/etc/letsencrypt/live/example.com/privkey.pem',f.path('/etc/letsencrypt/live/reality.example.com/privkey.pem').read_bytes()),
                 ('/etc/letsencrypt/live/example.com/fullchain.pem',f.path('/etc/letsencrypt/live/reality.example.com/fullchain.pem').read_bytes()),
                 ('/etc/letsencrypt/renewal/example.com.conf',b'authenticator = standalone'),
                 ('/etc/letsencrypt/renewal-hooks/deploy/3x-ui-auto-nginx',b'#!/bin/bash\ntouch /tmp/never-execute-incoming-hook\n')]
        for path, data in cases:
            with self.subTest(path=path):
                bad = self.modified(archive,{f.member(path):data})
                services = json.loads((f.root/'services.json').read_text()); (f.root/'commands').write_text('')
                self.assert_untouched(f.run_tool('restore',bad),services)
        self.assertFalse(Path('/tmp/never-execute-incoming-hook').exists())

    def test_old_v3_normalization_and_new_v3_idempotency(self):
        f = self.fixture
        # Original v1.5.0 hook lacks the new runtime health, but its exact managed body is trusted.
        result = subprocess.run(['bash','-eu','-c',f.script.read_text().split('cmd_restore() {')[0] +
                                 '\nrender_certificate_hook example.com reality.example.com legacy\n'],env=f.env,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
        f.write('/etc/letsencrypt/renewal-hooks/deploy/3x-ui-auto-nginx',result.stdout.decode(),0o755)
        archive = f.backup(); digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        for _ in range(2):
            result = f.run_tool('restore',archive)
            self.assertEqual(result.returncode,0,result.stdout+result.stderr)
            with sqlite3.connect(f.path('/etc/x-ui/x-ui.db')) as db:
                self.assertEqual(db.execute("SELECT value FROM settings WHERE key='webListen'").fetchone()[0],'127.0.0.1')
            includes = f.path('/etc/nginx/snippets/includes.conf').read_text()
            self.assertEqual(includes.count('proxy_pass http://127.0.0.1:10003;'),7)
            self.assertNotIn('proxy_pass https://127.0.0.1:10003;',includes)
            self.assertIn('proxy_pass https://127.0.0.1:10002;',f.path('/etc/nginx/nginx.conf').read_text())
            self.assertIn('check_xray_runtime',f.path('/etc/letsencrypt/renewal-hooks/deploy/3x-ui-auto-nginx').read_text())
            self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),digest)
            archive.unlink(); archive = f.backup(); digest = hashlib.sha256(archive.read_bytes()).hexdigest()

    def test_post_replacement_dead_xray_restores_and_verifies_old_state(self):
        f = self.fixture; archive = f.backup()
        incoming = self.modified(archive,{f.member('/var/www/html/recovery-marker'):b'incoming'})
        old_db = f.path('/etc/x-ui/x-ui.db').read_bytes()
        old_nginx = f.path('/etc/nginx/snippets/includes.conf').read_bytes()
        old_states = (f.root/'services.json').read_bytes()
        result = f.run_tool('restore',incoming,FAIL_INCOMING_XRAY='1')
        self.assertNotEqual(result.returncode,0)
        self.assertIn('Previous managed state/service states restored and checked',result.stderr)
        self.assertEqual(f.path('/var/www/html/recovery-marker').read_text(),'original')
        self.assertEqual(f.path('/etc/x-ui/x-ui.db').read_bytes(),old_db)
        self.assertEqual(f.path('/etc/nginx/snippets/includes.conf').read_bytes(),old_nginx)
        self.assertEqual(json.loads((f.root/'services.json').read_bytes()),json.loads(old_states))
        self.assertFalse(list(f.path('/var/backups/x-ui').glob('.rollback-*')))
        self.assertNotIn('Restore completed successfully.',result.stdout)

    def test_unverified_rollback_keeps_private_material_and_reports_exact_path(self):
        f = self.fixture; archive = f.backup()
        result = f.run_tool('restore',archive,DEAD_XRAY='1')
        self.assertNotEqual(result.returncode,0)
        material = list(f.path('/var/backups/x-ui').glob('.rollback-*'))
        self.assertEqual(len(material),1,result.stdout+result.stderr)
        self.assertEqual(material[0].stat().st_mode & 0o777,0o700)
        self.assertTrue((material[0]/'ready').is_file())
        self.assertIn(str(material[0]),result.stderr)
        self.assertIn('could not be verified',result.stderr)
        self.assertNotIn('Restore completed successfully.',result.stdout)

    def test_post_replacement_failure_preserves_previous_agh_and_requires_its_health(self):
        from test_adguard_backup import AdGuardBackup
        agh = AdGuardBackup('runTest'); agh.setUp(); self.addCleanup(agh.doCleanups)
        agh.install_fixture()
        f = agh.fixture
        f.env['NGINX_BIN'] = os.environ.get('NGINX_BIN') or shutil.which('nginx')
        f.write('/var/www/html/recovery-marker','original')
        archive = f.backup()
        # Use this fixture's archive member paths, retaining the authenticated AGH binary fixture.
        original = self.fixture; self.fixture = f
        incoming = self.modified(archive,{f.member('/var/www/html/recovery-marker'):b'incoming'})
        self.fixture = original
        config = f.path('/opt/AdGuardHome/AdGuardHome.yaml').read_bytes()
        result = f.run_tool('restore',incoming,FAIL_INCOMING_XRAY='1')
        self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('Previous managed state/service states restored and checked',result.stderr)
        self.assertEqual(f.path('/opt/AdGuardHome/AdGuardHome.yaml').read_bytes(),config)
        self.assertEqual(f.path('/opt/AdGuardHome/data/querylog.json').read_text(),'persistent query state')
        self.assertEqual(json.loads((f.root/'services.json').read_text())['AdGuardHome'],'active')
        self.assertIn('binary --version',(f.root/'binary-calls').read_text())
        self.assertNotIn('Restore completed successfully.',result.stdout)
