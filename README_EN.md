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

Run all commands below as root.

### Backup

A fresh install automatically installs `/usr/local/bin/x-ui-backup`.
If the tool is missing on an existing VPS, use the installation block below;
there is no need to rerun the installer.

Create a backup and list local archives:

```bash
x-ui-backup backup
x-ui-backup list
```

Archives are saved in `/var/backups/x-ui/` with root-only permissions. They contain
the database, UUIDs and client state, certificates/private keys and REALITY/runtime secrets.
**Always copy the archive off the VPS to a PC, NAS or another secure storage location:**
a local backup cannot help if the server is lost.

### Restore on the current VPS

Choose an archive and restore it:

```bash
x-ui-backup list
x-ui-backup restore /var/backups/x-ui/<archive>.tar.gz
```

Restore performs the required checks itself. Success is confirmed by
`Restore completed successfully.` It rolls back 3x-ui-pro state, not the entire OS.

### Restore on a new VPS

Use a clean VPS with **the same OS ID, VERSION_ID and architecture** as the backup.
For example, Ubuntu 26.04 amd64 → Ubuntu 26.04 amd64; moving to Ubuntu 24.04
or arm64 is not supported.

1. Install a compatible OS and run your usual OS/bootstrap script if needed.
2. **Do not run `x-ui-latest.sh`.** Install only the backup utility from `personal`:

   ```bash
   curl -fsSL \
     https://raw.githubusercontent.com/xPROMSx/3x-ui-pro/personal/assets/backup/x-ui-backup.sh \
     -o /tmp/x-ui-backup
   install -o root -g root -m 0755 /tmp/x-ui-backup /usr/local/bin/x-ui-backup
   rm -f /tmp/x-ui-backup
   ```

3. Securely transfer the saved archive to the VPS, for example into `/root/`, then run:

   ```bash
   x-ui-backup restore /root/x-ui-backup-....tar.gz
   ```

4. If the IP has changed, point the existing domains' DNS records to the new VPS.
   After a successful restore, use your existing panel and client configurations.

Restore installs missing application dependencies and restores 3x-ui-pro state
automatically.

### What is restored / what is not

Backup covers the panel/runtime, nginx, certificates, diagnostics/web state,
project systemd units, managed sysctl and managed Certbot cron. SSH, `authorized_keys`,
fail2ban, the base UFW policy and OS/bootstrap state are not restored — prepare
them through bootstrap or as the new server's administrator. Restore only adds
application rules for 80/tcp, 443/tcp and 443/udp; unrelated cron jobs are preserved.

### Validation

Backup/Restore v2 was validated on a real Ubuntu 26.04 amd64 VPS
(3x-ui 3.9.0, Xray 26.9.30, nginx 1.28.3): same-host backup/rollback,
clean-host recovery after bootstrap without running `x-ui-latest.sh`,
service persistence after reboot, and working panel and real client connections.
