"""Read the installer's single baseline; authenticate official candidate artifacts."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def baseline():
    text = (ROOT/'x-ui-latest.sh').read_text()
    match = re.search(r"cat <<'VERIFIED_RELEASE_JSON'\n(.*?)\nVERIFIED_RELEASE_JSON", text, re.S)
    if not match:
        raise ValueError('Missing installer release baseline')
    value = json.loads(match[1])
    stable_version(value['version'])
    if not isinstance(value.get('xray'), str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', value['xray']):
        raise ValueError('Invalid bundled Xray version')
    archives = value.get('archives')
    if not isinstance(archives, dict) or not archives:
        raise ValueError('Missing pinned archives')
    for arch, digest in archives.items():
        if not isinstance(arch, str) or not re.fullmatch('[a-z0-9]+', arch) or not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest):
            raise ValueError('Invalid pinned archive digest')
    coverage = value.get('integration_architectures')
    if not isinstance(coverage, list) or not coverage or any(not isinstance(arch, str) or arch not in archives for arch in coverage) or len(set(coverage)) != len(coverage):
        raise ValueError('Invalid integration architecture coverage')
    if 'amd64' not in archives:
        raise ValueError('Missing default integration archive')
    return value


def validate_layout(archive, arch):
    """Run the trusted installer's exact layout contract on the verified archive."""
    source = (ROOT/'x-ui-latest.sh').read_text()
    match = re.search(r'^_validate_panel_archive\(\)\s*\{.*?^\}', source, re.M | re.S)
    if not match:
        raise ValueError('Missing production archive layout validator')
    result = subprocess.run(['bash','-u','-c',match[0]+'\n_validate_panel_archive "$1" "$2"',
                             'archive-validation',str(archive),arch],capture_output=True,text=True,timeout=60)
    if result.returncode:
        raise ValueError('Production archive layout validation failed: '+result.stderr.strip())


def stable_version(tag):
    if not isinstance(tag, str) or not re.fullmatch(r'v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)', tag):
        raise ValueError('Not a stable release tag')
    if tuple(map(int, tag[1:].split('.'))) < (3, 8, 0):
        raise ValueError('Unsupported release (< v3.8.0)')
    return tag


def candidate(release, requested=None):
    version = stable_version(release.get('tag_name'))
    if requested is not None and version != requested:
        raise ValueError('Release tag does not match the requested candidate')
    if release.get('prerelease') is not False or release.get('draft') is not False:
        raise ValueError('Not a published stable release')
    name = 'x-ui-linux-amd64.tar.gz'
    for asset_name in (name, name+'.sha256'):
        matches = [a for a in release['assets'] if a.get('name') == asset_name]
        if len(matches) != 1:
            raise ValueError('Missing/ambiguous official asset: '+asset_name)
    digest = next(a for a in release['assets'] if a['name'] == name).get('digest')
    if not isinstance(digest, str) or not re.fullmatch('sha256:[0-9a-f]{64}', digest):
        raise ValueError('Missing official archive SHA-256 digest')
    return {'version':version, 'sha256':digest[7:], 'arch':'amd64'}


def selection():
    value = baseline()
    descriptor = os.environ.get('UPSTREAM_CANDIDATE')
    if not descriptor:
        return {'version':value['version'], 'sha256':value['archives']['amd64'],
                'xray':value['xray'], 'candidate':False}
    selected = json.loads(Path(descriptor).read_text())
    stable_version(selected['version'])
    if selected.get('arch') != 'amd64' or not re.fullmatch('[0-9a-f]{64}', selected['sha256']):
        raise ValueError('Invalid explicit Canary candidate descriptor')
    return {**selected, 'xray':None, 'candidate':True}


def verify(archive, sidecar, digest):
    actual = hashlib.sha256(Path(archive).read_bytes()).hexdigest()
    if actual != digest:
        raise ValueError('Official 3x-ui archive digest mismatch')
    if not re.fullmatch(digest+r'\s+\*?x-ui-linux-amd64.tar.gz\s*', Path(sidecar).read_text()):
        raise ValueError('Official 3x-ui sidecar mismatch')
