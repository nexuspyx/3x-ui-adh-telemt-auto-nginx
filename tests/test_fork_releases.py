"""Stage inert release fixtures with the README installer's download helper."""

import hashlib
import io
import os
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest


HELPER = Path(__file__).resolve().parents[1] / "assets/install/releases.sh"
GITHUB = "https://github.com/"
ASSETS = {
    "amd64": (
        ("MHSanaei/3x-ui/releases/download/v3.10.2/x-ui-linux-amd64.tar.gz",
         "bfda7921984e7a72555c38d4e861c7a8b67b3686b658608937de99682f1452d0"),
        ("AdguardTeam/AdGuardHome/releases/download/v0.107.80/AdGuardHome_linux_amd64.tar.gz",
         "28593bc761d71469abdbc6f39ca42cbed763ec92b63082c729177b037ed51d18"),
        ("telemt/telemt/releases/download/3.5.14/telemt-x86_64-linux-musl.tar.gz",
         "71c1454f79cf755edea0985500629daba553002f9863be754e8e38a418822773"),
        ("amirotin/telemt_panel/releases/download/v1.0.0-rc.3/telemt-panel-x86_64-linux-musl.tar.gz",
         "9f195fcf88fe6cfaee92ccaa3616149717fc6aa142a2a6c9e3bb34134e4b9d15"),
    ),
    "arm64": (
        ("MHSanaei/3x-ui/releases/download/v3.10.2/x-ui-linux-arm64.tar.gz",
         "a867c65ce52ad49f6504dd4c9c395337dc92ba57ae478da4f1b7ddb624014ed5"),
        ("AdguardTeam/AdGuardHome/releases/download/v0.107.80/AdGuardHome_linux_arm64.tar.gz",
         "d5e17d0d724418ddef8b7723ed8ade7eef4e621ec5b0e43a341859cca71a3d8f"),
        ("telemt/telemt/releases/download/3.5.14/telemt-aarch64-linux-musl.tar.gz",
         "21795430c4165cf964405459714d08eea319edaa5c9027beac4de373c356d84d"),
        ("amirotin/telemt_panel/releases/download/v1.0.0-rc.3/telemt-panel-aarch64-linux-musl.tar.gz",
         "420a52a67fc60718be0ab18fc6f2f5dcc15e59e21e432461a64af113bed3450d"),
    ),
}


def make_archive(path, entries):
    with tarfile.open(path, "w:gz") as archive:
        for name, mode, data in entries:
            member = tarfile.TarInfo(name)
            member.mode = mode
            if data is None:
                member.type = tarfile.DIRTYPE
                archive.addfile(member)
            else:
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ForkReleasesTest(unittest.TestCase):
    def setUp(self):
        fixture = tempfile.TemporaryDirectory()
        self.addCleanup(fixture.cleanup)
        self.root = Path(fixture.name)
        self.private = self.root / "private stage"
        self.private.mkdir(mode=0o700)
        self.staged = self.private / "releases"
        self.helper = self.root / "releases.sh"
        self.downloads = self.root / "downloads"

    def prepare(self, arch="amd64", private=None):
        return subprocess.run(
            ["bash", "-c", r'''
set -euo pipefail
curl() {
    local url='' output='' condition=''
    while (($#)); do
        case "$1" in
            --output|-o) output="$2"; shift ;;
            https://*) url="$1" ;;
        esac
        shift
    done
    printf '%s\n' "$url" >> "$DOWNLOADS"
    [[ -n "$url" && -n "$output" ]] || return 91
    while read -r allowed fixture; do
        if [[ "$allowed" == "$url" ]]; then
            cp -- "$fixture" "$output"
            return $?
        fi
    done < "$ASSET_MAP"
    return 22
}
source "$1"
prepare_fork_releases "$2" "$3"
''', "bash", str(self.helper if self.helper.exists() else HELPER),
             str(self.private if private is None else private), arch],
            env={**os.environ, "DOWNLOADS": str(self.downloads),
                 "ASSET_MAP": str(self.root / "asset-map")},
            capture_output=True, text=True, timeout=10,
        )

    def release_fixtures(self, arch, *, bad_checksum=False, missing_xray=False,
                         duplicate=False, binary_directory=False, omit_panel=False):
        # Only the copied test helper receives fixture digests. It still verifies each archive.
        source = HELPER.read_text() if HELPER.exists() else ""
        xui = [("x-ui", 0o755, None), ("x-ui/x-ui", 0o755, b"panel fixture"),
               ("x-ui/x-ui.sh", 0o755, b"helper fixture"),
               ("x-ui/x-ui.service.debian", 0o644, b"service fixture"),
               ("x-ui/bin/geoip.dat", 0o644, b"data fixture")]
        if not missing_xray:
            xui.append((f"x-ui/bin/xray-linux-{arch}", 0o755, b"xray fixture"))
        if duplicate:
            xui.append(("./x-ui/x-ui", 0o755, b"duplicate fixture"))
        adguard = [("./AdGuardHome", 0o755, None),
                   ("./AdGuardHome/AdGuardHome", 0o755,
                    None if binary_directory else b"adguard fixture"),
                   ("./AdGuardHome/README.md", 0o644, b"adguard docs fixture")]
        contents = (xui, adguard, [("telemt", 0o755, b"telemt fixture")],
                    [("telemt-panel", 0o755, b"telemt panel fixture")])
        with (self.root / "asset-map").open("w") as asset_map:
            for index, ((release_path, pin), entries) in enumerate(zip(ASSETS[arch], contents)):
                asset = self.root / str(index)
                digest = make_archive(asset, entries)
                if not (index == 0 and bad_checksum):
                    source = source.replace(pin, digest)
                if not (index == 3 and omit_panel):
                    asset_map.write(f"{GITHUB}{release_path} {asset}\n")
        if source:
            self.helper.write_text(source)

    def test_stages_both_architectures_from_versioned_assets(self):
        for arch in ("amd64", "arm64"):
            with self.subTest(arch=arch):
                private = self.private / arch
                private.mkdir(mode=0o700)
                self.release_fixtures(arch)
                before = self.downloads.read_text().splitlines() if self.downloads.exists() else []
                result = self.prepare(arch, private)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(self.downloads.read_text().splitlines()[len(before):],
                                 [GITHUB + path for path, _ in ASSETS[arch]])
                staged = private / "releases"
                for name, data in {
                    "x-ui/x-ui": b"panel fixture",
                    "x-ui/x-ui.sh": b"helper fixture",
                    "x-ui/x-ui.service.debian": b"service fixture",
                    f"x-ui/bin/xray-linux-{arch}": b"xray fixture",
                    "x-ui/bin/geoip.dat": b"data fixture",
                    "AdGuardHome": b"adguard fixture",
                    "telemt": b"telemt fixture",
                    "telemt-panel": b"telemt panel fixture",
                }.items():
                    self.assertEqual((staged / name).read_bytes(), data)
                self.assertEqual(staged.stat().st_mode & 0o777, 0o700)
                self.assertTrue(os.access(staged / "x-ui/x-ui", os.X_OK))
                self.assertTrue(os.access(staged / "telemt-panel", os.X_OK))

    def test_checksum_failure_does_not_extract(self):
        self.release_fixtures("amd64", bad_checksum=True)
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("x-ui", result.stderr)
        self.assertIn("SHA-256", result.stderr)
        self.assertFalse((self.staged / "x-ui").exists())

    def test_missing_architecture_binary_stops_before_extraction(self):
        self.release_fixtures("amd64", missing_xray=True)
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("x-ui/bin/xray-linux-amd64", result.stderr)
        self.assertFalse((self.staged / "x-ui").exists())

    def test_duplicate_normalized_member_stops_before_extraction(self):
        self.release_fixtures("amd64", duplicate=True)
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("duplicate", result.stderr.lower())
        self.assertFalse((self.staged / "x-ui").exists())

    def test_directory_cannot_replace_expected_release_binary(self):
        self.release_fixtures("amd64", binary_directory=True)
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("AdGuardHome/AdGuardHome", result.stderr)
        self.assertFalse((self.staged / "AdGuardHome").exists())

    def test_failed_download_names_component_and_stage(self):
        self.release_fixtures("amd64", omit_panel=True)
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("telemt-panel", result.stderr)
        self.assertIn("download", result.stderr.lower())
        self.assertFalse((self.staged / "telemt-panel").exists())

    def test_existing_stage_is_preserved(self):
        self.staged.mkdir()
        sentinel = self.staged / "existing"
        sentinel.write_text("preserve fixture")
        result = self.prepare()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("staging", result.stderr)
        self.assertEqual(sentinel.read_text(), "preserve fixture")
        self.assertFalse(self.downloads.exists())

    def test_unsupported_architecture_has_no_side_effects(self):
        result = self.prepare("i386")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("architecture", result.stderr)
        self.assertFalse(self.staged.exists())
        self.assertFalse(self.downloads.exists())
