"""Release selection, pre-mutation preflight and Canary controller contracts."""
from contextlib import redirect_stdout
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def function(source, name):
    match = re.search(r'^'+re.escape(name)+r'\(\)\s*\{.*?^\}', source, re.M | re.S)
    if not match:
        raise ValueError('Missing function '+name)
    return match.group()


class VerifiedRelease(unittest.TestCase):
    def test_default_does_not_follow_hypothetical_new_upstream(self):
        source = (ROOT/'x-ui-latest.sh').read_text()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'local').mkdir()
            archive = root/'fixture.tar.gz'
            with tarfile.open(archive, 'w:gz') as bundle:
                for name in ('x-ui','x-ui.sh','x-ui.service.debian','bin/xray-linux-amd64'):
                    entry = tarfile.TarInfo('x-ui/'+name)
                    entry.mode = 0o755
                    payload = b'#!/bin/sh\nexit 0\n'
                    entry.size = len(payload)
                    bundle.addfile(entry, io.BytesIO(payload))
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            metadata = {'version':'v3.9.0','xray':'26.9.30','archives':{'amd64':digest},
                        'integration_architectures':['amd64']}
            mock = r'''
msg_err() { echo "$*" >&2; }
msg_warn() { :; }
msg_ok() { :; }
_arch() { echo amd64; }
bootstrap_panel_dependencies() { :; }
verified_panel_release() { printf '%s' "$MANIFEST"; }
apt-get() { :; }
curl() { echo '{"tag_name": "v9.9.9"}'; }
_download_panel_archive() { printf '%s\n' "$1" >> "$LOG"; cp "$FIXTURE" "$2"; }
systemctl() { :; }
install() { :; }
_panel_initial_config() { :; }
cp() { command cp "$@"; }
'''
            if 'preflight_panel_release()' in source:
                names = ('_validate_panel_version','_validate_panel_archive','release_panel_preflight','preflight_panel_release')
                script = mock+'\n'+'\n'.join(function(source,n) for n in names)+'\npreflight_panel_release'
            else:
                script = mock+'\n'+function(source,'_validate_panel_version')+'\n'+function(source,'install_panel').replace('/usr/local',str(root/'local')).replace('/etc/systemd/system/x-ui.service',str(root/'service'))+'\ninstall_panel'
            result = subprocess.run(['bash','-u','-c',script],capture_output=True,text=True,
                                    env={**os.environ,'MANIFEST':json.dumps(metadata),'PANEL_VERSION':'',
                                         'FIXTURE':str(archive),'LOG':str(root/'calls')})
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertIn('/v3.9.0/x-ui-linux-amd64.tar.gz',(root/'calls').read_text())
            self.assertNotIn('v9.9.9',(root/'calls').read_text())

    def test_preflight_failure_preserves_existing_managed_state(self):
        source = (ROOT/'x-ui-latest.sh').read_text()
        names = re.findall(r'^\s*([a-z_]+)(?:\s|$)',function(source,'main'),re.M)
        # Execute the actual lifecycle, injecting the same archive-integrity failure
        # at the old installation stage or the new release preflight stage.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root/'managed-state'
            target.write_bytes(b'previous healthy installation')
            mock = '\n'.join(f'{n}() {{ :; }}' for n in set(names) if n not in ('main','local','if','return','fi'))
            mock += r'''
msg_err() { echo "$*" >&2; }
preflight_panel_release() { echo verification-failed >> "$LOG"; return 1; }
install_panel() { echo verification-failed >> "$LOG"; return 1; }
cleanup_adguard() { echo cleanup >> "$LOG"; rm -f "$TARGET"; }
clean_previous_install() { echo cleanup >> "$LOG"; rm -f "$TARGET"; }
'''
            result = subprocess.run(['bash','-u','-c',mock+'\n'+function(source,'main')+'\nmain'],
                                    capture_output=True,text=True,env={**os.environ,'TARGET':str(target),
                                    'LOG':str(root/'calls'),'INSTALL_AGH':'n'})
            self.assertNotEqual(result.returncode,0)
            self.assertIn('verification-failed',(root/'calls').read_text())
            self.assertNotIn('cleanup',(root/'calls').read_text())
            self.assertEqual(target.read_bytes(),b'previous healthy installation')


    def preflight(self, version='', failure='', consume=False):
        source = (ROOT/'x-ui-latest.sh').read_text()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/'local/x-ui').mkdir(parents=True)
            old = root/'local/x-ui/old-state'
            old.write_bytes(b'healthy')
            archive = root/'fixture.tar.gz'
            with tarfile.open(archive,'w:gz') as bundle:
                for name in ('x-ui','x-ui.sh','x-ui.service.debian','bin/xray-linux-amd64'):
                    entry = tarfile.TarInfo('x-ui/'+name)
                    entry.mode = 0o755
                    data = b'#!/bin/sh\nexit 0\n'
                    entry.size = len(data)
                    bundle.addfile(entry,io.BytesIO(data))
                if failure == 'unsafe-member':
                    entry = tarfile.TarInfo('../outside'); entry.size=1
                    bundle.addfile(entry,io.BytesIO(b'x'))
            if failure == 'invalid-archive':
                archive.write_bytes(b'not a tar archive')
            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            manifest = {'version':'v3.9.0','xray':'26.9.30','archives':{'amd64':digest},'integration_architectures':['amd64']}
            if failure == 'pinned-mismatch':
                manifest['archives']['amd64'] = '0'*64
            selected = 'v'+version.removeprefix('v') if version else 'v3.9.0'
            release = {'tag_name':selected,'prerelease':failure=='prerelease','draft':failure=='draft',
                       'assets':[{'name':'x-ui-linux-amd64.tar.gz','digest':'sha256:'+digest},
                                 {'name':'x-ui-linux-amd64.tar.gz.sha256'}]}
            if failure == 'missing-asset': release['assets'].pop()
            if failure == 'unknown-digest': release['assets'][0].pop('digest')
            sidecar = root/'fixture.sha256'
            sidecar.write_text(digest+'  x-ui-linux-amd64.tar.gz\n')
            mock = r'''
msg_err() { echo "$*" >&2; }
msg_ok() { echo "$*"; }
msg_warn() { echo "$*"; }
_arch() { echo amd64; }
bootstrap_panel_dependencies() { [[ "$FAILURE" != dependencies ]]; }
verified_panel_release() { printf '%s' "$MANIFEST"; }
curl() {
    local url='' output='' previous='' argument
    for argument in "$@"; do
        [[ "$argument" != https://* ]] || url=$argument
        [[ "$previous" != -o ]] || output=$argument
        previous=$argument
    done
    echo "$url" >> "$LOG"
    [[ "$FAILURE" != download ]] || return 22
    case "$url" in
        https://api.github.com/*) [[ "$FAILURE" != nonexistent ]] || return 22; printf '%s' "$METADATA" > "$output" ;;
        *.sha256) cp "$SIDECAR" "$output" ;;
        *) cp "$ARCHIVE" "$output" ;;
    esac
}
systemctl() { :; }
install() { :; }
_panel_initial_config() { :; }
'''
            names = ('_validate_panel_version','release_panel_preflight','_validate_panel_archive','_download_panel_archive','preflight_panel_release')
            script = mock+'\n'+'\n'.join(function(source,n) for n in names)+'\npreflight_panel_release || exit 1\n'
            if consume:
                script += function(source,'install_panel').replace('/usr/local',str(root/'local')).replace('/etc/systemd/system/x-ui.service',str(root/'service'))+'\ninstall_panel || exit 1\n'
            result = subprocess.run(['bash','-u','-c',script],capture_output=True,text=True,
                                    env={**os.environ,'PANEL_VERSION':version,'MANIFEST':json.dumps(manifest),
                                         'METADATA':json.dumps(release),'FAILURE':failure,'LOG':str(root/'calls'),
                                         'ARCHIVE':str(archive),'SIDECAR':str(sidecar)})
            calls = (root/'calls').read_text().splitlines() if (root/'calls').exists() else []
            if failure or version in ('v3.7.9','v3.10.0-rc.1','bad','v03.9.0'):
                self.assertNotEqual(result.returncode,0,result.stdout+result.stderr)
                self.assertEqual(old.read_bytes(),b'healthy')
            else:
                self.assertEqual(result.returncode,0,result.stdout+result.stderr)
                if consume: self.assertTrue((root/'local/x-ui/x-ui.sh').exists())
            return result, calls

    def test_verified_archive_is_downloaded_once_and_reused(self):
        result,calls = self.preflight(consume=True)
        self.assertEqual(len(calls),2)
        self.assertTrue(all('/v3.9.0/' in url for url in calls))
        self.assertNotIn('not the verified baseline',result.stdout)

    def test_explicit_stable_selection_warns_and_rejects_unverifiable_releases(self):
        result,calls = self.preflight('v3.10.0')
        self.assertIn('not the verified baseline',result.stdout)
        self.assertEqual(len(calls),3)
        self.assertTrue(all('v3.10.0' in url for url in calls))
        for failure in ('nonexistent','prerelease','draft','missing-asset','unknown-digest'):
            with self.subTest(failure=failure): self.preflight('v3.10.0',failure)
        for version in ('v3.7.9','v3.10.0-rc.1','bad','v03.9.0'):
            with self.subTest(version=version):
                _,calls = self.preflight(version)
                self.assertEqual(calls,[])

    def test_corrupt_or_unsafe_archive_and_dependency_failure_preserve_target(self):
        for failure in ('download','pinned-mismatch','invalid-archive','unsafe-member','dependencies'):
            with self.subTest(failure=failure): self.preflight(failure=failure)

    def test_canary_selection_and_changed_implementation_cache_identity(self):
        from unittest.mock import patch
        import upstream_canary
        from release_contract import candidate, baseline
        release = {'tag_name':'v3.10.0','draft':False,'prerelease':False,'assets':[
            {'name':'x-ui-linux-amd64.tar.gz','digest':'sha256:'+'1'*64},
            {'name':'x-ui-linux-amd64.tar.gz.sha256'}]}
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            output=root/'outputs'
            with redirect_stdout(io.StringIO()), patch.dict(os.environ,{'GITHUB_OUTPUT':str(output)}), patch.object(upstream_canary,'fetch_json',return_value=release), patch.object(upstream_canary,'fingerprint',return_value='first'):
                selected=upstream_canary.probe(root)
            self.assertEqual(selected['version'],'v3.10.0')
            self.assertEqual(selected['sha256'],'1'*64)
            self.assertEqual(baseline()['version'],'v3.9.0')
            first=output.read_text()
            with redirect_stdout(io.StringIO()), patch.dict(os.environ,{'GITHUB_OUTPUT':str(output)}), patch.object(upstream_canary,'fetch_json',return_value=release), patch.object(upstream_canary,'fingerprint',return_value='second'):
                upstream_canary.probe(root)
            self.assertNotEqual(first.splitlines()[0],output.read_text().splitlines()[1])
            for field,value in (('tag_name','v3.10.0-rc.1'),('prerelease',True),('draft',True)):
                with self.subTest(field=field), self.assertRaises(ValueError): candidate({**release,field:value})

    def test_canary_uses_own_archive_and_reports_compatibility_failure(self):
        from unittest.mock import patch
        import upstream_canary
        from release_contract import verify
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            payload=b'candidate archive, not baseline'
            digest=hashlib.sha256(payload).hexdigest()
            selected={'version':'v3.10.0','sha256':digest,'arch':'amd64','implementation':'test'}
            (root/'candidate.json').write_text(json.dumps(selected))
            downloads=[]
            def download(url,target):
                downloads.append(url)
                target.write_bytes((digest+'  x-ui-linux-amd64.tar.gz\n').encode() if url.endswith('.sha256') else payload)
            class Process:
                stdout=iter(('FAIL: native setting persistence incompatible\n','FAILED (failures=1)\n'))
                def __init__(self,*args,**kwargs):
                    pass
                def wait(self,timeout): return 1
                def poll(self): return 1
                def kill(self): pass
            with redirect_stdout(io.StringIO()), patch('test_real_integration.download',side_effect=download), patch.object(upstream_canary.subprocess,'Popen',side_effect=Process) as process:
                self.assertEqual(upstream_canary.run(root),1)
            env=process.call_args.kwargs['env']
            self.assertEqual(env['UPSTREAM_CANDIDATE'],str(root/'candidate.json'))
            self.assertNotIn('INTEGRATION_ARTIFACTS',env)
            self.assertEqual(downloads,[f'https://github.com/MHSanaei/3x-ui/releases/download/v3.10.0/x-ui-linux-amd64.tar.gz'+suffix for suffix in ('','.sha256')])
            result=json.loads((root/'result.json').read_text())
            self.assertEqual(result['status'],'FAIL')
            self.assertIn('setting persistence incompatible',result['reason'])
            (root/'x-ui.tar.gz').write_bytes(b'corruption')
            with self.assertRaises(ValueError): verify(root/'x-ui.tar.gz',root/'x-ui.sha256',digest)

    def test_baseline_all_archives_are_pinned_but_coverage_is_explicit(self):
        from release_contract import baseline
        value=baseline()
        source=(ROOT/'x-ui-latest.sh').read_text()
        body=source[source.index('verified_panel_release()'):source.index('bootstrap_panel_dependencies()')]
        emitted=subprocess.check_output(['bash','-u','-c',body+'\nverified_panel_release'],text=True)
        self.assertEqual(json.loads(emitted),value)
        self.assertEqual(value['version'],'v3.9.0')
        self.assertEqual(value['xray'],'26.9.30')
        self.assertEqual(set(value['archives']),{'amd64','arm64','386','armv5','armv6','armv7','s390x'})
        self.assertEqual(value['integration_architectures'],['amd64'])
        for digest in value['archives'].values(): self.assertRegex(digest,r'^[0-9a-f]{64}$')

    def test_bootstrap_missing_binaries_and_apt_failure_fail_closed(self):
        source=(ROOT/'x-ui-latest.sh').read_text()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            ca=root/'ca.crt';ca.write_text('fixture CA')
            body=function(source,'bootstrap_panel_dependencies').replace('/etc/ssl/certs/ca-certificates.crt',str(ca))
            mock=r'''
command() { [[ "$1" != -v ]] || return 1; builtin command "$@"; }
apt-get() { echo "$*" >> "$CALLS"; [[ "$FAIL_APT" != 1 ]]; }
msg_err() { echo "$*" >&2; }
'''
            for failure in ('0','1'):
                log=root/'calls';log.write_text('')
                result=subprocess.run(['bash','-u','-c',mock+body+'\nbootstrap_panel_dependencies'],
                                      env={**os.environ,'CALLS':str(log),'FAIL_APT':failure},capture_output=True,text=True)
                self.assertNotEqual(result.returncode,0)
                self.assertIn('update',log.read_text())
                if failure=='0':
                    self.assertIn('curl ca-certificates python3 tar gzip',log.read_text())
                    self.assertIn('Release preflight requires curl',result.stderr)
                else:
                    self.assertNotIn('install',log.read_text())

    def test_canary_preparation_failure_is_reported_but_not_cached_as_tested(self):
        from unittest.mock import patch
        import upstream_canary
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            selected={'version':'v3.10.0','sha256':'1'*64,'arch':'amd64','implementation':'test'}
            (root/'candidate.json').write_text(json.dumps(selected))
            output=root/'outputs'
            with patch.dict(os.environ,{'GITHUB_OUTPUT':str(output)}), patch('test_real_integration.download',side_effect=RuntimeError('download unavailable')):
                self.assertEqual(upstream_canary.run(root),1)
            result=json.loads((root/'result.json').read_text())
            self.assertFalse(result['integration_started'])
            self.assertEqual(result['version'],'v3.10.0')
            self.assertIn('download unavailable',result['reason'])
            self.assertEqual(output.read_text(),'tested=false\n')
            workflow=(ROOT/'.github/workflows/upstream-canary.yml').read_text()
            self.assertIn("steps.integration.outputs.tested == 'true'",workflow)


if __name__ == '__main__':
    unittest.main()
