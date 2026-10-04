# 3x-ui-pro — personal

🇷🇺 [Русская версия](README.md)

`personal` is a personal branch of the [mozaroc/3x-ui-pro](https://github.com/mozaroc/3x-ui-pro) fork used to test and operate custom improvements to the [3x-ui](https://github.com/MHSanaei/3x-ui) fresh installer.

Main focus:

- VLESS + REALITY
- VLESS + XHTTP
- nginx / SNI / TLS on a shared port 443
- modern and minimal Xray configuration
- automated validation of fresh-install templates

Primary test platform: **Ubuntu 26.04 LTS**.

Compatible with [Telemt WEB Manager](https://github.com/xPROMSx/telemt-web-manager).

> This branch is primarily intended for fresh installations and rebuilds. `x-ui-latest.sh` is not a safe in-place updater for an existing VPS.

## Installation

```bash
wget -qO x-ui-latest.sh https://raw.githubusercontent.com/xPROMSx/3x-ui-pro/personal/x-ui-latest.sh
bash x-ui-latest.sh
```

The latest stable 3x-ui release is installed by default.

## Backup / Restore

The fresh installer automatically installs `/usr/local/bin/x-ui-backup` from `personal`.
Run as root:

```bash
x-ui-backup backup
x-ui-backup list
x-ui-backup restore /var/backups/x-ui/<archive>.tar.gz
```

Format v2 saves the panel DB and runtime, nginx, certificates, web content,
x-ui/MTR units, managed sysctl and the Certbot renewal entry. Large diagnostic
test files are regenerated. SSH, bootstrap/security state and UFW files are
excluded; restore only adds application rules for 80/tcp, 443/tcp and 443/udp,
without enabling UFW. Unrelated cron jobs are preserved.

Restore is designed for same-host rollback or recovery on a clean VPS with
**the same OS ID, VERSION_ID and architecture**: Ubuntu 24.04/26.04 or Debian 12/13.
For clean-host recovery, run your bootstrap, install the tool, securely transfer
the archive and restore **without running `x-ui-latest.sh`**:

```bash
sudo install -d -m 0755 /usr/local/bin
curl -fsSL https://raw.githubusercontent.com/xPROMSx/3x-ui-pro/personal/assets/backup/x-ui-backup.sh -o x-ui-backup.sh
sudo install -o root -g root -m 0755 x-ui-backup.sh /usr/local/bin/x-ui-backup
sudo x-ui-backup restore /path/to/archive.tar.gz
```

Missing application packages are installed automatically. Domains stay unchanged;
switch DNS manually. Restoring onto an unknown server with unrelated services is
unsupported. A failed restore returns non-zero; fix the cause and rerun using
the same archive.

**Archives contain UUIDs, passwords and private keys: store and transfer them securely.**
A local backup on the same VPS is not disaster recovery — copy it off-host.
Live clean-VPS recovery validation is a separate step.
