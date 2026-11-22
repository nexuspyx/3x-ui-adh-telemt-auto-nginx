# Sourced by install.sh. Stages pinned upstream release files inside its private directory.
prepare_fork_releases() (
    local directory="${1:-}" arch="${2:-}" suffix panel_sha adguard_sha telemt_sha telemt_panel_sha
    local stage component asset digest archive
    case "$arch" in
        amd64)
            suffix=x86_64
            panel_sha=bfda7921984e7a72555c38d4e861c7a8b67b3686b658608937de99682f1452d0
            adguard_sha=28593bc761d71469abdbc6f39ca42cbed763ec92b63082c729177b037ed51d18
            telemt_sha=71c1454f79cf755edea0985500629daba553002f9863be754e8e38a418822773
            telemt_panel_sha=9f195fcf88fe6cfaee92ccaa3616149717fc6aa142a2a6c9e3bb34134e4b9d15
            ;;
        arm64)
            suffix=aarch64
            panel_sha=a867c65ce52ad49f6504dd4c9c395337dc92ba57ae478da4f1b7ddb624014ed5
            adguard_sha=d5e17d0d724418ddef8b7723ed8ade7eef4e621ec5b0e43a341859cca71a3d8f
            telemt_sha=21795430c4165cf964405459714d08eea319edaa5c9027beac4de373c356d84d
            telemt_panel_sha=420a52a67fc60718be0ab18fc6f2f5dcc15e59e21e432461a64af113bed3450d
            ;;
        *) printf 'Release staging: unsupported architecture %s.\n' "$arch" >&2; return 1 ;;
    esac

    umask 077
    if ! python3 - "$directory" <<'PY_STAGE'
from pathlib import Path
import os
import stat
import sys

directory = Path(sys.argv[1])
try:
    if not directory.is_absolute():
        raise ValueError("expected an absolute directory")
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid() or info.st_mode & 0o077:
        raise ValueError("expected an existing private, owned directory")
    (directory / "releases").mkdir(mode=0o700)
except (OSError, ValueError) as error:
    sys.exit(str(error))
PY_STAGE
    then
        printf 'Release staging: could not create the private releases directory.\n' >&2
        return 1
    fi
    stage="$directory/releases"

    while IFS=' ' read -r component asset digest; do
        archive="$stage/$component.tar.gz"
        if ! curl -q -fsSL --proto '=https' --proto-redir '=https' \
            --retry 3 --retry-max-time 240 --connect-timeout 15 --max-time 180 \
            --output "$archive" "https://github.com/$asset"; then
            printf '%s release: download failed.\n' "$component" >&2
            return 1
        fi
        python3 - "$archive" "$stage" "$component" "$arch" "$digest" <<'PY_ARCHIVE' || return 1
import hashlib
from pathlib import Path, PurePosixPath
import shutil
import sys
import tarfile

archive, stage, component, arch, expected = sys.argv[1:]
stage = Path(stage)
required = tuple(map(PurePosixPath, {
    "x-ui": ("x-ui/x-ui", "x-ui/x-ui.sh", "x-ui/x-ui.service.debian", f"x-ui/bin/xray-linux-{arch}"),
    "AdGuardHome": ("AdGuardHome/AdGuardHome",),
    "telemt": ("telemt",),
    "telemt-panel": ("telemt-panel",),
}[component]))

try:
    with open(archive, "rb") as package:
        digest = hashlib.sha256()
        for chunk in iter(lambda: package.read(1024 * 1024), b""):
            digest.update(chunk)
        if digest.hexdigest() != expected:
            sys.exit(f"{component} release: SHA-256 verification failed.")
        package.seek(0)

        with tarfile.open(fileobj=package, mode="r:gz") as contents:
            members = {}
            for member in contents:
                path = PurePosixPath(member.name)
                if (not path.parts or path.is_absolute() or ".." in path.parts
                        or path.parts[0] != component or "\0" in member.name):
                    raise ValueError(f"unsafe archive path {member.name!r}")
                if path in members:
                    raise ValueError(f"duplicate archive member {str(path)!r}")
                if (member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE, tarfile.DIRTYPE)
                        or member.issparse()):
                    raise ValueError(f"unsupported archive member type for {str(path)!r}")
                members[path] = member

            for path in members:
                for parent in path.parents:
                    if parent in members and not members[parent].isdir():
                        raise ValueError(f"archive member parent is not a directory: {str(parent)!r}")
            for path in required:
                member = members.get(path)
                if member is None or not member.isfile() or member.size <= 0:
                    raise ValueError(f"missing regular release file {str(path)!r}")

            # Write the validated paths without restoring ownership, archive modes or links.
            selected = members if component == "x-ui" else required
            for path in selected:
                member = members[path]
                target = stage / (path if component == "x-ui" else path.name)
                if member.isdir():
                    target.mkdir(mode=0o700, parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                with contents.extractfile(member) as payload, target.open("xb") as output:
                    shutil.copyfileobj(payload, output)
                if member.mode & 0o111 or target.name == component:
                    target.chmod(0o700)
except (EOFError, OSError, ValueError, tarfile.TarError) as error:
    sys.exit(f"{component} release: archive validation or extraction failed: {error}")
PY_ARCHIVE
    done <<EOF
x-ui MHSanaei/3x-ui/releases/download/v3.10.2/x-ui-linux-$arch.tar.gz $panel_sha
AdGuardHome AdguardTeam/AdGuardHome/releases/download/v0.107.80/AdGuardHome_linux_$arch.tar.gz $adguard_sha
telemt telemt/telemt/releases/download/3.5.14/telemt-$suffix-linux-musl.tar.gz $telemt_sha
telemt-panel amirotin/telemt_panel/releases/download/v1.0.0-rc.3/telemt-panel-$suffix-linux-musl.tar.gz $telemt_panel_sha
EOF
)
