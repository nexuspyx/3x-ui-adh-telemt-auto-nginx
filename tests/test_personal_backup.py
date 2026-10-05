"""Exercise the Bash utility on private fixtures, with host services mocked.

Only filesystem roots are relocated in a temporary copy of the source. tar/gzip,
SQLite and (when NGINX_BIN is set) nginx are real; no installer is executed.
"""
import copy
import io
import json
import os
import pwd
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "assets/backup/x-ui-backup.sh").read_text()
INSTALLER = (ROOT / "x-ui-latest.sh").read_text()
CRON = ('@monthly certbot renew --non-interactive --pre-hook "systemctl stop nginx" '
        '--post-hook "systemctl start nginx" > /dev/null 2>&1')
PARAMS = [
    "net.core.default_qdisc=fq", "net.ipv4.tcp_congestion_control=bbr", "fs.file-max=2097152",
    "net.ipv4.tcp_timestamps=1", "net.ipv4.tcp_sack=1", "net.ipv4.tcp_window_scaling=1",
    "net.core.rmem_max=16777216", "net.core.wmem_max=16777216",
    "net.ipv4.tcp_rmem=4096 87380 16777216", "net.ipv4.tcp_wmem=4096 65536 16777216",
]


def relocated(source, root):
    # Apply a single pass so replacements cannot recursively match themselves.
    return re.sub(r"/(?:etc|root|var|usr/local|usr/bin/x-ui|dev/shm)(?=[/\s\"';,)]|$)",
                  lambda m: str(root) + m[0], source)


def function(name):
    matches = re.findall(r"^" + re.escape(name) + r"\(\)\s*\{.*?^\}", INSTALLER, re.M | re.S)
    if len(matches) != 1:
        raise AssertionError(f"Expected one {name} function")
    return matches[0]


# These stubs model outcomes/record calls, not production systemd or firewall.
MOCK = r'''#!/usr/bin/env python3
import json, os, pathlib, socket, subprocess, sys
name = pathlib.Path(sys.argv[0]).name
args = sys.argv[1:]
root = pathlib.Path(os.environ['FIXTURE_ROOT'])
with open(root / 'commands', 'a') as log:
    log.write(json.dumps([name, *args]) + '\n')
if name == 'systemctl':
    path = root / 'services.json'
    services = json.loads(path.read_text())
    command, service = args[0], args[-1]
    enabled_path = root / 'enabled.json'
    enabled = json.loads(enabled_path.read_text()) if enabled_path.exists() else {}
    if command == 'enable':
        if service == 'cron' and os.environ.get('FAIL_CRON_ENABLE'):
            sys.exit(1)
        enabled[service] = True
        enabled_path.write_text(json.dumps(enabled))
        if '--now' in args:
            services[service] = 'active'
            path.write_text(json.dumps(services))
    if command == 'is-enabled':
        sys.exit(0 if enabled.get(service) else 1)
    if command == 'is-active':
        active = services.get(service) == 'active'
        if '--quiet' in args and os.environ.get('FAIL_HEALTH_SERVICE') == service:
            active = False
        if '--quiet' not in args:
            print('active' if active else 'inactive')
        sys.exit(0 if active else 3)
    if command == 'cat':
        sys.exit(0 if (root / ('etc/systemd/system/' + service + '.service')).exists() or service == 'nginx' else 1)
    if command in ('start', 'stop'):
        services[service] = 'active' if command == 'start' else 'inactive'
        path.write_text(json.dumps(services))
        if command == 'start' and service == 'nginx':
            broken = os.environ.get('BREAK_FINAL_CRON')
            if broken == 'active':
                services['cron'] = 'inactive'
                path.write_text(json.dumps(services))
            elif broken == 'enabled':
                enabled['cron'] = False
                enabled_path.write_text(json.dumps(enabled))
            elif broken in ('missing', 'duplicate'):
                cron = root / 'crontab'
                cron.write_text('' if broken == 'missing' else cron.read_text() * 2)
        if command == 'start' and service == 'x-ui':
            sock = root / 'dev/shm/uds2023.sock'
            sock.parent.mkdir(parents=True, exist_ok=True)
            sock.unlink(missing_ok=True)
            if not os.environ.get('MISSING_SOCKET'):
                s = socket.socket(socket.AF_UNIX)
                s.bind(str(sock))
                s.close()
            if os.environ.get('CORRUPT_RUNNING_DB'):
                (root / 'etc/x-ui/x-ui.db').write_bytes(b'corrupt')
elif name == 'crontab':
    path = root / 'crontab'
    if args == ['-l']:
        if not path.exists():
            print('no crontab for root', file=sys.stderr)
            sys.exit(1)
        print(path.read_text(), end='')
    elif args == ['-']:
        if os.environ.get('FAIL_CRON_WRITE'):
            sys.exit(1)
        path.write_text(sys.stdin.read())
    else:
        sys.exit(2)
elif name == 'ip':
    if os.environ.get('NO_IP'):
        sys.exit(1)
    print('8.8.8.8 via 192.0.2.1 src ' + os.environ.get('TEST_IPV4', '192.0.2.10'))
elif name == 'curl':
    sys.exit(1)
elif name == 'dpkg-query':
    if args[-1] in os.environ.get('MISSING_PACKAGES', '').split():
        sys.exit(1)
    print('install ok installed', end='')
elif name == 'apt-get':
    if os.environ.get('FAIL_APT'):
        sys.exit(1)
elif name == 'ufw':
    if args == ['status']:
        print('Status: ' + os.environ.get('UFW_STATE', 'inactive'))
elif name == 'nginx':
    if os.environ.get('FAIL_NGINX'):
        sys.exit(1)
    if os.environ.get('NGINX_BIN'):
        sys.exit(subprocess.call([os.environ['NGINX_BIN'], *args, '-p', str(root) + '/',
                                 '-c', str(root / 'etc/nginx/nginx.conf')]))
elif name == 'cp':
    if os.environ.get('FAIL_COPY') and any(a == str(root / 'usr/local/x-ui') for a in args):
        sys.exit(1)
    sys.exit(subprocess.call([os.environ['REAL_CP'], *args]))
elif name == 'dd':
    # Real zero data with sparse allocation keeps repetitive fixture tests small.
    sys.exit(subprocess.call([os.environ['REAL_DD'], *args, 'conv=sparse']))
elif name == 'install':
    # Non-root runs keep real copy/mode semantics while skipping root chown.
    filtered = []
    while args:
        arg = args.pop(0)
        if arg in ('-o', '-g'):
            args.pop(0)
        else:
            filtered.append(arg)
    sys.exit(subprocess.call([os.environ['REAL_INSTALL'], *filtered]))
elif name == 'tar':
    result = subprocess.call([os.environ['REAL_TAR'], *args])
    if not result and args[0] == '-czf' and os.environ.get('CORRUPT_ARCHIVE'):
        pathlib.Path(args[1]).write_bytes(b'bad archive')
    sys.exit(result)
elif name == 'id':
    sys.exit(1 if os.environ.get('MTR_ABSENT') else 0)
elif name == 'sysctl':
    sys.exit(1 if os.environ.get('FAIL_SYSCTL') else 0)
elif name == 'setcap':
    sys.exit(1 if pathlib.Path(args[-1]).name in os.environ.get('FAIL_SETCAP', '').split() else 0)
# useradd and non-root chown only record calls; no host mutation.
'''


class PersonalBackup(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="personal-backup-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "host"
        self.root.mkdir()
        self.bin = Path(self.temp.name) / "mock-bin"
        self.bin.mkdir()
        commands = ['systemctl', 'crontab', 'ip', 'curl', 'dpkg-query', 'apt-get',
                    'ufw', 'nginx', 'cp', 'dd', 'tar', 'id', 'useradd', 'setcap', 'sysctl', 'mtr', 'mtr-packet']
        if os.geteuid() != 0:
            commands += ['chown', 'install']
        for name in commands:
            path = self.bin / name
            path.write_text(MOCK)
            path.chmod(0o755)
        sqlite = os.environ.get('SQLITE3_BIN') or shutil.which('sqlite3')
        if not sqlite:
            self.fail('sqlite3 CLI is required (CI installs it; local runs may set SQLITE3_BIN).')
        (self.bin / 'sqlite3').symlink_to(sqlite)
        self.script = Path(self.temp.name) / 'backup.sh'
        self.script.write_text(relocated(SOURCE, self.root))
        self.env = {**os.environ, 'PATH': str(self.bin) + ':' + os.environ['PATH'],
                    'FIXTURE_ROOT': str(self.root), 'REAL_CP': shutil.which('cp'),
                    'REAL_DD': shutil.which('dd'), 'REAL_INSTALL': shutil.which('install'), 'REAL_TAR': shutil.which('tar')}
        self.write('/etc/os-release', Path('/etc/os-release').read_text())
        self.write('/etc/nginx/nginx.conf', f'pid {self.root}/nginx.pid;\nerror_log stderr;\nevents {{}}\nhttp {{}}\n')
        for path in ('/usr/local/x-ui/bin', '/etc/letsencrypt/live/example.com',
                     '/root/cert/example.com', '/var/www/html', '/var/www/subpage'):
            self.path(path).mkdir(parents=True)
        db = self.path('/etc/x-ui/x-ui.db')
        db.parent.mkdir(parents=True)
        with sqlite3.connect(db) as conn:
            conn.execute('CREATE TABLE settings (key TEXT, value TEXT)')
            conn.execute('INSERT INTO settings VALUES (?, ?)',
                         ('webCertFile', str(self.path('/root/cert/example.com/fullchain.pem'))))
            conn.execute('CREATE TABLE clients (uuid TEXT)')
            conn.execute("INSERT INTO clients VALUES ('fixture-secret-not-for-output')")
        self.write('/usr/local/x-ui/x-ui', '#!/bin/sh\necho 3.9.0\n', 0o755)
        self.write('/usr/local/x-ui/bin/xray-linux-amd64', '#!/bin/sh\necho Xray 26.9.30\n', 0o755)
        self.write('/usr/bin/x-ui', '#!/bin/sh\necho 3.9.0\n', 0o755)
        self.write('/usr/local/lib/3x-ui-pro/mtr-backend.py', '# backend\n', 0o755)
        self.write('/etc/systemd/system/x-ui.service', '[Service]\nExecStart=/usr/local/x-ui/x-ui\n')
        self.write('/etc/systemd/system/mtr-backend.service', '[Service]\nNoNewPrivileges=yes\n')
        for name in ('fullchain.pem', 'privkey.pem'):
            self.write('/etc/letsencrypt/live/example.com/' + name, 'fixture-cert\n', 0o600)
            self.path('/root/cert/example.com/' + name).symlink_to(self.path('/etc/letsencrypt/live/example.com/' + name))
        self.write('/var/www/html/index.html', '192.0.2.10 unchanged')
        self.write('/var/www/subpage/clash.yaml', '192.0.2.10 unchanged')
        self.write('/var/www/diagnostics/index.html',
                   '<span id="server-ip">192.0.2.10</span><code id="server-ip-step">192.0.2.10</code>'
                   '<a href="https://192.0.2.10/">unchanged</a>')
        for name in ('speedtest.js', 'speedtest_worker.js'):
            self.write('/var/www/diagnostics/' + name, 'fixture-js')
        for name, size in (('test-1g.bin', 1024**3), ('test-512m.bin', 512*1024**2)):
            path = self.path('/var/www/diagnostics/testfiles/' + name)
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(path, 'wb') as f:
                f.truncate(size)
        self.write('/etc/sysctl.d/99-3x-ui-pro.conf', '\n'.join(PARAMS) + '\n')
        self.write('/etc/sysctl.d/99-proms-network.conf', 'bootstrap preserved\n')
        self.write('/etc/sysctl.conf', 'legacy sysctl preserved\n')
        self.write('/etc/ssh/sshd_config', 'bootstrap preserved\n')
        self.write('/etc/ufw/user.rules', 'SSH preserved\n')
        self.write('/etc/cron.d/unrelated', 'unrelated preserved\n')
        (self.root / 'crontab').write_text(CRON + '\n@hourly /opt/unrelated\n')
        (self.root / 'services.json').write_text(json.dumps({'x-ui': 'active', 'nginx': 'active', 'mtr-backend': 'active'}))

    def path(self, absolute):
        return self.root / absolute.lstrip('/')

    def write(self, path, contents, mode=0o644):
        path = self.path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(contents)
        path.chmod(mode)

    def run_tool(self, *args, **env):
        # Sourcing allows a root-check stub for unprivileged fixture execution.
        result = subprocess.run(['bash', '-c', 'source "$1"; shift; require_root() { :; }; main "$@"',
                                 'fixture', str(self.script), *map(str, args)],
                                env={**self.env, **env}, text=True, capture_output=True)
        self.assertNotIn('fixture-secret-not-for-output', result.stdout + result.stderr)
        return result

    def commands(self):
        path = self.root / 'commands'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def backup(self, **env):
        result = self.run_tool('backup', **env)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Backup completed successfully.', result.stdout)
        return next(self.path('/var/backups/x-ui').glob('*.tar.gz'))

    def member(self, path):
        return 'files/' + str(self.path(path)).lstrip('/')

    def changed_archive(self, archive, metadata=None, extra=None, db=None, omit=None, managed_cron=None):
        fd, target = tempfile.mkstemp(dir=self.temp.name, suffix='.tar.gz')
        os.close(fd)
        target = Path(target)
        with tarfile.open(archive) as src, tarfile.open(target, 'w:gz') as dst:
            for item in src:
                if omit and (item.name == omit or item.name.startswith(omit + '/')):
                    continue
                data = src.extractfile(item).read() if item.isfile() else None
                if item.name == 'meta.json' and metadata:
                    meta = json.loads(data)
                    meta.update(metadata)
                    data = json.dumps(meta).encode()
                if item.name == self.member('/etc/x-ui/x-ui.db') and db is not None:
                    data = db
                if item.name == 'managed-root-cron' and managed_cron is not None:
                    data = managed_cron
                item = copy.copy(item)
                if data is not None:
                    item.size = len(data)
                dst.addfile(item, io.BytesIO(data) if data is not None else None)
            if extra:
                dst.addfile(extra, io.BytesIO(b'bad') if extra.isfile() else None)
        return target

    def test_installer_integration(self):
        tuning = function('tune_system')
        self.assertNotIn('/etc/sysctl.conf', tuning)
        self.assertNotIn('sysctl --system', tuning)
        self.assertIn('/etc/sysctl.d/99-3x-ui-pro.conf', tuning)
        run = relocated(tuning, self.root) + '\ntune_system\n'
        result = subprocess.run(['bash', '-eu', '-c', run], env=self.env, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        path = self.path('/etc/sysctl.d/99-3x-ui-pro.conf')
        self.assertEqual(path.read_text().splitlines(), PARAMS)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o644)
        self.assertEqual([c for c in self.commands() if c[0] == 'sysctl'], [['sysctl', '-p', str(path)]])
        tool = function('install_backup_tool')
        self.assertIn('${GITHUB_RAW}/assets/backup/x-ui-backup.sh', tool)
        self.assertNotIn('mozaroc', tool)
        self.assertIn('    install_backup_tool || exit 1\n', function('main'))
        download = Path(self.temp.name) / 'download.sh'
        download.write_text('#!/bin/bash\necho backup-tool\n')
        mock = ('curl() { [[ "$2" == "$GITHUB_RAW/assets/backup/x-ui-backup.sh" ]] || return 9; '
                'cp "$DOWNLOAD" "$4"; }\nmsg_err() { echo "$*" >&2; }\n')
        install = relocated(tool, self.root)
        result = subprocess.run(['bash', '-eu', '-c', mock + install + '\ninstall_backup_tool\n'],
                                env={**self.env, 'GITHUB_RAW': 'https://fixture.example/personal', 'DOWNLOAD': str(download)},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        binary = self.path('/usr/local/bin/x-ui-backup')
        self.assertEqual(binary.read_text(), download.read_text())
        self.assertEqual(stat.S_IMODE(binary.stat().st_mode), 0o755)
        # Failed downloads preserve an already installed executable.
        result = subprocess.run(['bash', '-eu', '-c', mock + install + '\ncurl() { return 22; }; install_backup_tool\n'],
                                env={**self.env, 'GITHUB_RAW': 'https://fixture.example/personal'},
                                capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(binary.read_text(), download.read_text())

    def test_archive_scope_permissions_and_active_service(self):
        archive = self.backup()
        self.assertEqual(stat.S_IMODE(archive.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)
        if os.geteuid() == 0:
            self.assertEqual((archive.stat().st_uid, archive.stat().st_gid), (0, 0))
            self.assertEqual((archive.parent.stat().st_uid, archive.parent.stat().st_gid), (0, 0))
        with tarfile.open(archive) as tar:
            names = tar.getnames()
            for path in ('/etc/x-ui/x-ui.db', '/usr/local/x-ui/x-ui', '/usr/bin/x-ui',
                         '/etc/nginx/nginx.conf', '/root/cert/example.com/privkey.pem',
                         '/etc/systemd/system/mtr-backend.service', '/etc/sysctl.d/99-3x-ui-pro.conf'):
                self.assertIn(self.member(path), names)
            self.assertFalse(any('testfiles' in n or '/etc/ufw' in n or '/etc/cron.d' in n or '/etc/ssh' in n
                                 or '99-proms-network' in n for n in names))
            self.assertEqual(tar.extractfile('managed-root-cron').read().decode(), CRON + '\n')
            self.assertEqual(json.load(tar.extractfile('meta.json'))['format_version'], 2)
        calls = self.commands()
        stop = calls.index(['systemctl', 'stop', 'x-ui'])
        start = calls.index(['systemctl', 'start', 'x-ui'])
        later_copy = next(i for i, c in enumerate(calls) if c[0] == 'cp' and str(self.path('/etc/nginx')) in c)
        self.assertLess(stop, start)
        self.assertLess(start, later_copy)
        self.assertEqual(json.loads((self.root / 'services.json').read_text())['x-ui'], 'active')
        self.assertEqual(self.run_tool('list').returncode, 0)

    def test_inactive_service_is_not_started(self):
        state = self.root / 'services.json'
        state.write_text(json.dumps({'x-ui': 'inactive'}))
        self.backup()
        self.assertNotIn(['systemctl', 'start', 'x-ui'], self.commands())
        self.assertNotIn(['systemctl', 'stop', 'x-ui'], self.commands())
        self.assertEqual(json.loads(state.read_text())['x-ui'], 'inactive')

    def test_incomplete_final_archive_is_not_kept(self):
        result = self.run_tool('backup', CORRUPT_ARCHIVE='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertNotIn('Backup completed successfully.', result.stdout)
        self.assertEqual(list(self.path('/var/backups/x-ui').iterdir()), [])
        self.assertEqual(json.loads((self.root / 'services.json').read_text())['x-ui'], 'active')

    def test_backup_failures_restore_service_and_remove_archive(self):
        for failure in ('collection', 'database'):
            with self.subTest(failure=failure):
                if failure == 'database':
                    self.path('/etc/x-ui/x-ui.db').write_bytes(b'corrupt')
                result = self.run_tool('backup', FAIL_COPY='1' if failure == 'collection' else '')
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('Backup completed successfully.', result.stdout)
                self.assertEqual(json.loads((self.root / 'services.json').read_text())['x-ui'], 'active')
                self.assertIn(['systemctl', 'start', 'x-ui'], self.commands())
                self.assertEqual(list(self.path('/var/backups/x-ui').iterdir()), [])

    def test_restore_preflight_rejects_before_replacement(self):
        archive = self.backup()
        corrupt = Path(self.temp.name) / 'corrupt.tar.gz'
        corrupt.write_bytes(b'not gzip')
        cases = [(corrupt, {}), (archive, {'format_version': 1}), (archive, {'os_id': 'other'}),
                 (archive, {'os_version': '99'}), (archive, {'arch': 'other'})]
        cases.append((self.changed_archive(archive, omit=self.member('/usr/local/x-ui/bin/xray-linux-amd64')), {}))
        unsafe_location = self.path('/etc/nginx/input.tar.gz')
        shutil.copy2(archive, unsafe_location)
        cases.append((unsafe_location, {}))
        marker = self.path('/etc/x-ui/preserved')
        marker.write_text('untouched')
        for file, meta in cases:
            with self.subTest(metadata=meta, file=file.name):
                (self.root / 'commands').write_text('')
                candidate = self.changed_archive(file, metadata=meta) if meta else file
                result = self.run_tool('restore', candidate)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(marker.read_text(), 'untouched')
                self.assertFalse(any(c[:2] == ['systemctl', 'stop'] or c[0] == 'apt-get' for c in self.commands()))
        bad_db = self.changed_archive(archive, db=b'corrupt')
        result = self.run_tool('restore', bad_db)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(marker.read_text(), 'untouched')

    def test_archive_paths_and_links_are_validated(self):
        archive = self.backup()
        for name, target in (('../escape', None), ('files/etc/ssh/injected', None),
                             (self.member('/etc/nginx/evil'), '/etc/ssh')):
            with self.subTest(name=name):
                extra = tarfile.TarInfo(name)
                if target:
                    extra.type = tarfile.SYMTYPE
                    extra.linkname = target
                else:
                    extra.size = 3
                result = self.run_tool('restore', self.changed_archive(archive, extra=extra))
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('Restore completed successfully.', result.stdout)

    def test_round_trip_cron_firewall_diagnostics_and_sysctl(self):
        archive = self.backup()
        self.write('/var/www/html/index.html', 'modified')
        self.write('/etc/nginx/stale', 'stale')
        self.path('/root/cert/example.com/fullchain.pem').unlink()
        preserved = {p: self.path(p).read_bytes() for p in ('/etc/ssh/sshd_config', '/etc/ufw/user.rules',
                    '/etc/cron.d/unrelated', '/etc/sysctl.d/99-proms-network.conf', '/etc/sysctl.conf',
                    '/etc/systemd/system/mtr-backend.service')}
        (self.root / 'crontab').write_text('@hourly /opt/current-unrelated\n' + CRON + '\n')
        (self.root / 'commands').write_text('')
        result = self.run_tool('restore', archive, TEST_IPV4='198.51.100.20', MTR_ABSENT='1')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('Restore completed successfully.', result.stdout)
        self.assertFalse(self.path('/etc/nginx/stale').exists())
        self.assertEqual(self.path('/var/www/html/index.html').read_text(), '192.0.2.10 unchanged')
        self.assertEqual(self.path('/var/www/subpage/clash.yaml').read_text(), '192.0.2.10 unchanged')
        for path, content in preserved.items():
            self.assertEqual(self.path(path).read_bytes(), content)
        self.assertEqual((self.root / 'crontab').read_text(), '@hourly /opt/current-unrelated\n' + CRON + '\n')
        html = self.path('/var/www/diagnostics/index.html').read_text()
        self.assertIn('id="server-ip">198.51.100.20<', html)
        self.assertIn('id="server-ip-step">198.51.100.20<', html)
        self.assertIn('https://192.0.2.10/', html)
        for name, size in (('test-15k.bin', 15*1024), ('test-17k.bin', 17*1024),
                           ('test-100m.bin', 100*1024**2), ('test-1g.bin', 1024**3)):
            self.assertEqual(self.path('/var/www/diagnostics/testfiles/' + name).stat().st_size, size)
        self.assertFalse(self.path('/var/www/diagnostics/testfiles/test-512m.bin').exists())
        calls = self.commands()
        self.assertEqual([c for c in calls if c[0] == 'ufw'], [
            ['ufw', 'allow', '80/tcp'], ['ufw', 'allow', '443/tcp'], ['ufw', 'allow', '443/udp'], ['ufw', 'status']])
        self.assertEqual([c for c in calls if c[0] == 'sysctl'],
                         [['sysctl', '-p', str(self.path('/etc/sysctl.d/99-3x-ui-pro.conf'))]])
        self.assertFalse(any(c[0] == 'apt-get' for c in calls))
        self.assertIn(['useradd', '--system', '--no-create-home', '--shell', '/usr/sbin/nologin', 'mtr-backend'], calls)
        self.assertEqual(len([c for c in calls if c[0] == 'setcap']), 2)
        self.assertEqual(stat.S_IMODE(self.path('/etc/sysctl.d/99-3x-ui-pro.conf').stat().st_mode), 0o644)
        if os.geteuid() == 0:
            managed = self.path('/etc/sysctl.d/99-3x-ui-pro.conf').stat()
            self.assertEqual((managed.st_uid, managed.st_gid), (0, 0))
            diagnostics = self.path('/var/www/diagnostics/index.html').stat()
            self.assertEqual(diagnostics.st_uid, pwd.getpwnam('www-data').pw_uid)
        self.assertTrue(stat.S_ISSOCK(self.path('/dev/shm/uds2023.sock').stat().st_mode))
        self.assertIn('[WARN] UFW is inactive', result.stderr)
        # Same archive is safe to apply twice, and is never modified.
        digest = archive.read_bytes()
        again = self.run_tool('restore', archive, TEST_IPV4='198.51.100.20')
        self.assertEqual(again.returncode, 0, again.stdout + again.stderr)
        self.assertEqual(archive.read_bytes(), digest)

    def test_package_failure_is_before_managed_replacement(self):
        archive = self.backup()
        self.write('/etc/x-ui/marker', 'preserve')
        (self.root / 'commands').write_text('')
        result = self.run_tool('restore', archive, MISSING_PACKAGES='sqlite3 mtr', FAIL_APT='1')
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.path('/etc/x-ui/marker').exists())
        self.assertFalse(any(c[:2] == ['systemctl', 'stop'] for c in self.commands()))
        result = self.run_tool('restore', archive, MISSING_PACKAGES='sqlite3 mtr')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        installs = [c for c in self.commands() if c[:2] == ['apt-get', 'install']]
        self.assertEqual(installs, [['apt-get', 'install', '-y', '--no-install-recommends', 'sqlite3', 'mtr']])

    def test_clean_host_restore_repairs_missing_panel_links(self):
        archive = self.changed_archive(self.backup(), omit=self.member('/root/cert'))
        for path in ('/etc/x-ui', '/usr/local/x-ui', '/etc/nginx', '/etc/letsencrypt', '/root/cert',
                     '/usr/local/lib/3x-ui-pro', '/var/www/html', '/var/www/subpage', '/var/www/diagnostics'):
            shutil.rmtree(self.path(path))
        for path in ('/usr/bin/x-ui', '/etc/systemd/system/x-ui.service', '/etc/systemd/system/mtr-backend.service'):
            self.path(path).unlink()
        (self.root / 'crontab').unlink()
        result = self.run_tool('restore', archive, NO_IP='1', MTR_ABSENT='1')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue(self.path('/root/cert/example.com/fullchain.pem').is_symlink())
        self.assertTrue(self.path('/root/cert/example.com/privkey.pem').exists())
        self.assertIn('192.0.2.10', self.path('/var/www/diagnostics/index.html').read_text())
        self.assertIn('Cannot detect current IPv4', result.stderr)
        self.assertEqual((self.root / 'crontab').read_text(), CRON + '\n')

    def test_restore_dependencies_and_cron_service_contract(self):
        archive = self.backup()
        missing = 'cron procps iproute2 tar gzip tzdata'
        (self.root / 'commands').write_text('')
        # Even archives from an install with no managed renewal job must recover it.
        archive = self.changed_archive(archive, managed_cron=b'')
        (self.root / 'crontab').write_text('@hourly /opt/unrelated\n' + CRON + '\n' + CRON + '\n')
        result = self.run_tool('restore', archive, MISSING_PACKAGES=missing)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        calls = self.commands()
        packages = next(c[4:] for c in calls if c[:2] == ['apt-get', 'install'])
        self.assertCountEqual(packages, missing.split())
        activation = calls.index(['systemctl', 'enable', '--now', 'cron'])
        self.assertLess(activation, calls.index(['systemctl', 'stop', 'x-ui']))
        for command in ('is-active', 'is-enabled'):
            self.assertIn(['systemctl', command, '--quiet', 'cron'], calls)
            # Checks occur after writing cron and again at the final health gate.
            self.assertGreater(calls.index(['systemctl', command, '--quiet', 'cron'],
                                          calls.index(['systemctl', 'start', 'nginx'])), activation)
        self.assertEqual((self.root / 'crontab').read_text(), '@hourly /opt/unrelated\n' + CRON + '\n')
        self.assertEqual(json.loads((self.root / 'services.json').read_text())['cron'], 'active')
        self.assertTrue(json.loads((self.root / 'enabled.json').read_text())['cron'])

    def test_restore_cron_failures_never_report_success(self):
        archive = self.backup()
        for env in ({'FAIL_CRON_ENABLE': '1'}, {'FAIL_CRON_WRITE': '1'}, {'FAIL_HEALTH_SERVICE': 'cron'},
                    *({'BREAK_FINAL_CRON': state} for state in ('active', 'enabled', 'missing', 'duplicate'))):
            with self.subTest(failure=env):
                (self.root / 'commands').write_text('')
                result = self.run_tool('restore', archive, **env)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('Restore completed successfully.', result.stdout)
                if 'BREAK_FINAL_CRON' in env:
                    self.assertIn(['systemctl', 'start', 'nginx'], self.commands())
                    self.assertIn('[FAIL]', result.stderr)

    def test_setcap_failure_is_best_effort_but_service_health_is_required(self):
        archive = self.backup()
        expected_calls = [['setcap', 'cap_net_raw+ep', str(self.bin / name)] for name in ('mtr', 'mtr-packet')]
        for binary in ('mtr', 'mtr-packet'):
            with self.subTest(binary=binary):
                (self.root / 'commands').write_text('')
                result = self.run_tool('restore', archive, FAIL_SETCAP=binary)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('Restore completed successfully.', result.stdout)
                self.assertIn(f'[WARN] Could not set CAP_NET_RAW file capability on {binary};', result.stderr)
                self.assertIn('systemd AmbientCapabilities', result.stderr)
                self.assertNotIn('[FAIL]', result.stdout + result.stderr)
                calls = self.commands()
                self.assertEqual([c for c in calls if c[0] == 'setcap'], expected_calls)
                health = calls.index(['systemctl', 'is-active', '--quiet', 'mtr-backend'])
                self.assertGreater(health, calls.index(['systemctl', 'start', 'mtr-backend']))

        result = self.run_tool('restore', archive, FAIL_SETCAP='mtr mtr-packet', FAIL_HEALTH_SERVICE='mtr-backend')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('[FAIL] mtr-backend is not active', result.stderr)
        self.assertNotIn('Restore completed successfully.', result.stdout)

    def test_mandatory_health_and_sysctl_failures_never_report_success(self):
        archive = self.backup()
        for env in ({'FAIL_NGINX': '1'}, {'FAIL_HEALTH_SERVICE': 'mtr-backend'},
                    {'CORRUPT_RUNNING_DB': '1'}, {'MISSING_SOCKET': '1'}, {'FAIL_SYSCTL': '1'}):
            with self.subTest(failure=env):
                (self.root / 'commands').write_text('')
                result = self.run_tool('restore', archive, **env)
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn('Restore completed successfully.', result.stdout)
                if 'FAIL_NGINX' in env:
                    self.assertNotIn(['systemctl', 'start', 'nginx'], self.commands())


if __name__ == '__main__':
    unittest.main(verbosity=2)
