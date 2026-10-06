"""AGH snapshot/restore coverage reuses the existing private Backup fixtures."""
import io
import json
import os
from pathlib import Path
import tarfile
import unittest
from certificate_fixtures import relocate
from test_adguard import BINARY, HELPER


class AdGuardBackup(unittest.TestCase):
    def setUp(self):
        # Instantiate the fixture, without inheriting/rerunning its 22 tests.
        from test_personal_backup import PersonalBackup
        self.fixture=PersonalBackup('runTest');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.root=self.fixture.root
        self.fixture.env.update(ROOT=str(self.root), CALLS=str(self.root/'binary-calls'))

    def install_fixture(self, active=True):
        f=self.fixture
        f.write('/opt/AdGuardHome/AdGuardHome',BINARY,0o755)
        f.write('/opt/AdGuardHome/AdGuardHome.yaml', '''http:
  address: 127.0.0.1:18081
  doh:
    insecure_enabled: true
    routes:
      - GET /dns-query
      - POST /dns-query
users:
  - name: admin
    password: '$2y$12$00000000000000000000000000000000000000000000000000000'
dns:
  bind_hosts:
    - 127.0.0.1
  port: 18082
tls:
  enabled: false
schema_version: 34
''',0o600)
        f.write('/opt/AdGuardHome/managed.json',json.dumps(dict(version='v0.107.79',arch='amd64',domain='example.com',path='adg-ABCDEFGHIJKL',web_port=18081,dns_port=18082)),0o600)
        f.write('/opt/AdGuardHome/data/querylog.json','persistent query state')
        f.write('/usr/local/lib/3x-ui-pro/managed-adguard.sh',relocate(HELPER,self.root),0o600)
        # Generate the exact snippet from the same managed helper.
        import subprocess
        r=subprocess.run(['bash','-c','source "$1"; AGH_WEB_PORT=18081; AGH_PATH=adg-ABCDEFGHIJKL; agh_snippet','fixture',str(f.path('/usr/local/lib/3x-ui-pro/managed-adguard.sh'))],capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr)
        f.write('/etc/nginx/snippets/x-ui-auto-optional/adguard.conf',r.stdout,0o600)
        f.write('/etc/systemd/system/AdGuardHome.service',f'ExecStart={self.root}/opt/AdGuardHome/AdGuardHome -c {self.root}/opt/AdGuardHome/AdGuardHome.yaml -w {self.root}/opt/AdGuardHome -s run --no-check-update\n')
        services=json.loads((self.root/'services.json').read_text());services['AdGuardHome']='active' if active else 'inactive';(self.root/'services.json').write_text(json.dumps(services))
        enabled=json.loads((self.root/'enabled.json').read_text());enabled['AdGuardHome']=True;(self.root/'enabled.json').write_text(json.dumps(enabled))

    def test_absent_metadata_and_restore_remove_target_adguard(self):
        f=self.fixture;archive=f.backup()
        with tarfile.open(archive) as tar:
            self.assertIs(json.load(tar.extractfile('meta.json'))['adguard_home'],False)
            self.assertFalse(any('/opt/AdGuardHome' in m.name for m in tar))
        self.install_fixture()
        r=f.run_tool('restore',archive)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertFalse(f.path('/opt/AdGuardHome').exists())
        self.assertFalse(f.path('/etc/systemd/system/AdGuardHome.service').exists())
        self.assertFalse(f.path('/etc/nginx/snippets/x-ui-auto-optional/adguard.conf').exists())
        self.assertNotIn('binary -c', (self.root/'binary-calls').read_text() if (self.root/'binary-calls').exists() else '')

    def test_present_snapshot_order_state_and_workdir(self):
        f=self.fixture
        for active in (True,False):
            self.install_fixture(active);(self.root/'commands').write_text('')
            archive=f.backup();calls=f.commands()
            collect=next(i for i,c in enumerate(calls) if c[:2]==['cp','-a'] and str(f.path('/opt/AdGuardHome')) in c)
            if active:
                self.assertLess(calls.index(['systemctl','stop','AdGuardHome']),collect)
                self.assertGreater(calls.index(['systemctl','start','AdGuardHome']),collect)
            else:self.assertNotIn(['systemctl','start','AdGuardHome'],calls)
            self.assertEqual(json.loads((self.root/'services.json').read_text())['AdGuardHome'],'active' if active else 'inactive')
            with tarfile.open(archive) as tar:
                meta=json.load(tar.extractfile('meta.json'))
                self.assertIs(meta['adguard_home'],True);self.assertEqual(meta['adguard_version'],'v0.107.79')
                self.assertEqual(meta['adguard_arch'],'amd64')
                self.assertIn('files/' + str(f.path('/opt/AdGuardHome/data/querylog.json')).lstrip('/'),tar.getnames())
                self.assertNotIn('files/' + str(f.path('/etc/systemd/system/AdGuardHome.service')).lstrip('/'),tar.getnames())
            archive.unlink()

    def test_backup_failure_recovers_adguard_and_certbot_timer(self):
        f=self.fixture;self.install_fixture()
        r=f.run_tool('backup',FAIL_AGH_COPY='1')
        self.assertNotEqual(r.returncode,0)
        self.assertNotIn('Backup completed successfully.',r.stdout)
        state=json.loads((self.root/'services.json').read_text())
        self.assertEqual(state['AdGuardHome'],'active');self.assertEqual(state['certbot.timer'],'active')
        self.assertFalse(list(f.path('/var/backups/x-ui').glob('*.tar.gz')))

    def test_partial_state_and_conditional_archive_validation(self):
        f=self.fixture
        f.write('/etc/nginx/snippets/x-ui-auto-optional/adguard.conf','partial')
        r=f.run_tool('backup');self.assertNotEqual(r.returncode,0);self.assertNotIn('Backup completed successfully.',r.stdout)
        f.path('/etc/nginx/snippets/x-ui-auto-optional/adguard.conf').unlink()
        archive=f.backup()
        file=tarfile.TarInfo('files/' + str(f.path('/opt/AdGuardHome/unexpected')).lstrip('/'));file.size=3
        bad=f.changed_archive(archive,extra=file)
        r=f.run_tool('restore',bad);self.assertNotEqual(r.returncode,0);self.assertIn('prohibited',r.stderr)
        bad=f.changed_archive(archive,metadata={'adguard_home':True,'adguard_version':'v0.107.79','adguard_arch':'amd64'})
        r=f.run_tool('restore',bad);self.assertNotEqual(r.returncode,0);self.assertIn('missing required',r.stderr)
        bad=f.changed_archive(archive,metadata={'adguard_home':'false'})
        r=f.run_tool('restore',bad);self.assertNotEqual(r.returncode,0);self.assertIn('boolean',r.stderr)

    def test_present_restore_recreates_service_and_requires_health(self):
        f=self.fixture;self.install_fixture();archive=f.backup()
        f.write('/opt/AdGuardHome/data/querylog.json','changed')
        (self.root/'commands').write_text('')
        r=f.run_tool('restore',archive)
        self.assertEqual(r.returncode,0,r.stdout+r.stderr)
        self.assertIn('existing credentials preserved',r.stdout)
        self.assertEqual(f.path('/opt/AdGuardHome/data/querylog.json').read_text(),'persistent query state')
        self.assertTrue(f.path('/etc/systemd/system/AdGuardHome.service').exists())
        calls=f.commands()
        self.assertIn(['systemctl','enable','--now','AdGuardHome'],calls)
        self.assertTrue(any(c[0]=='curl' and '--resolve' in c and 'example.com:443:127.0.0.1' in c for c in calls))
        for fail in ({'FAIL':'version'},{'FAIL':'config'},{'FAIL':'service'},{'FAIL_AGH_HEALTH':'1'}):
            r=f.run_tool('restore',archive,**fail)
            self.assertNotEqual(r.returncode,0,r.stdout+r.stderr)
            self.assertNotIn('Restore completed successfully.',r.stdout)


if __name__=='__main__':unittest.main(verbosity=2)
