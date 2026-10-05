<div align="center">

# 3x-ui Stack

**REALITY · XHTTP · Hysteria2 · nginx · Backup/Restore**

[![XHTTP / security](https://github.com/xPROMSx/3x-ui-stack/actions/workflows/stack-xhttp.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-stack/actions/workflows/stack-xhttp.yml)
[![Backup / restore](https://github.com/xPROMSx/3x-ui-stack/actions/workflows/stack-backup.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-stack/actions/workflows/stack-backup.yml)
[![Tested OS](https://img.shields.io/badge/tested-Ubuntu%2026.04%20amd64-E95420?logo=ubuntu&logoColor=white)](#platforms)
[![Releases](https://img.shields.io/github/v/release/xPROMSx/3x-ui-stack)](https://github.com/xPROMSx/3x-ui-stack/releases)

[Русский](README.md) · [Releases](https://github.com/xPROMSx/3x-ui-stack/releases) · [Issues](https://github.com/xPROMSx/3x-ui-stack/issues)

</div>

An actively maintained deployment stack for [3x-ui](https://github.com/MHSanaei/3x-ui) and Xray: modern transports, nginx SNI/TLS routing, subscriptions, diagnostics and validated recovery. Development takes place on `main` through separate pull requests with security and Backup/Restore checks.

> **Fresh installation or rebuild only.** `x-ui-latest.sh` stops and removes the previous installation, including the panel database and nginx configuration. It is not a safe in-place updater for an existing VPS. Save an off-server backup first.

## Features and transports

| Feature | Implementation | Purpose |
| --- | --- | --- |
| VLESS + REALITY | TCP, public 443 through nginx SNI, Xray on 8443 | Primary REALITY profile |
| VLESS + XHTTP | `stream-up`, Unix socket, TLS through nginx | XHTTP without arbitrary localhost-port proxying |
| Hysteria2 | UDP/QUIC on 443, TLS in Xray | Alternative transport where UDP is available |
| VLESS + WebSocket | Fixed nginx route with TLS | Additional profile for compatible clients |
| Trojan + gRPC | Fixed nginx route, HTTP/2 and TLS | Additional profile for compatible clients |
| Subscriptions and diagnostics | Clash/Mihomo, JSON; MTR and LibreSpeed | Client management and network checks |
| Backup/Restore v2 | Archive, database and service health checks | Rollback and recovery on a compatible clean OS |

The installer creates all five inbound profiles. WS and Trojan gRPC usage is optional; there is currently no separate installation toggle. Transport support depends on the client and Xray versions.

### nginx, SNI and TLS

TCP 443 is routed by SNI: REALITY traffic goes to Xray and the panel domain goes to the nginx TLS vhost. Let's Encrypt/Certbot provides certificates. XHTTP uses a Unix socket; WS and Trojan gRPC use known routes and fixed backend ports. Arbitrary `/<port>/...` paths must not expose localhost services. Hysteria2 uses UDP 443 independently of nginx TCP routing.

### UFW behavior

With active UFW, the installer preserves existing policy and adds 80/tcp, 443/tcp and 443/udp. With inactive UFW, it discovers the SSH port from the current connection or effective sshd configuration before enabling the firewall. If discovery fails, it warns and leaves UFW inactive. It does not reset rules or assume SSH port 22.

<a id="platforms"></a>
## Supported and tested platforms

| Platform | Status |
| --- | --- |
| Ubuntu 26.04 LTS amd64 | Primary platform; previously validated on a real VPS |
| Ubuntu 24.04 LTS | Accepted by installer and restore; automated checks run in CI |
| Debian 12 / 13 | Accepted by OS checks; equivalent live VPS coverage is not claimed |

Restore requires matching source `ID`, `VERSION_ID` and architecture. OS acceptance does not imply that every architecture is tested. The installer rejects CPUs reporting a QEMU model; use a VPS exposing the real CPU model.

## Installation

Use root access, a clean supported OS, correctly configured DNS for the panel and REALITY domains, TCP 80/443, and UDP 443 for Hysteria2.

Download and review the script before running it:

```bash
curl -fSL https://raw.githubusercontent.com/xPROMSx/3x-ui-stack/main/x-ui-latest.sh -o x-ui-latest.sh
less x-ui-latest.sh
bash x-ui-latest.sh -subdomain panel.example.com -reality_domain reality.example.com
```

Replace the example domains. The latest stable 3x-ui release is selected by default. Pin a validated panel version with `-version <tag>` for reproducible deployment; the 3x-ui Stack utility and the panel have separate release cycles.

## Companion project: Telemt WEB Manager

[**Telemt WEB Manager**](https://github.com/xPROMSx/telemt-web-manager) complements this stack with installation and management of Telemt WEB proxy on Ubuntu using nginx, Let's Encrypt, systemd and optional SOCKS5.

It is a separate companion project for deployments that also need Telemt WEB proxy. This installer does not install it automatically. For a shared host, coordinate domains, occupied ports and nginx configuration using both projects' documentation.

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
`Restore completed successfully.` It rolls back 3x-ui Stack state, not the entire OS.

### Restore on a new VPS

Use a clean VPS with **the same OS ID, VERSION_ID and architecture** as the backup.
For example, Ubuntu 26.04 amd64 -> Ubuntu 26.04 amd64; moving to Ubuntu 24.04
or arm64 is not supported.

1. Install a compatible OS and run your usual OS/bootstrap script if needed.
2. **Do not run `x-ui-latest.sh`.** Install only the backup utility from `main`:

   ```bash
   curl -fsSL \
     https://raw.githubusercontent.com/xPROMSx/3x-ui-stack/main/assets/backup/x-ui-backup.sh \
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

Restore installs missing application dependencies and restores 3x-ui Stack state
automatically.

### What is restored / what is not

Backup covers the panel/runtime, nginx, certificates, diagnostics/web state,
project systemd units, managed sysctl and managed Certbot cron. SSH, `authorized_keys`,
fail2ban, the base UFW policy and OS/bootstrap state are not restored - prepare
them through bootstrap or as the new server's administrator. Restore only adds
application rules for 80/tcp, 443/tcp and 443/udp; unrelated cron jobs are preserved.

### Validation

Backup/Restore v2 was validated on a real Ubuntu 26.04 amd64 VPS
(3x-ui 3.9.0, Xray 26.9.30, nginx 1.28.3): same-host backup/rollback,
clean-host recovery after bootstrap without running `x-ui-latest.sh`,
service persistence after reboot, and working panel and real client connections.

## Security validation

CI runs the complete current regression suite: inbound/Host generation for both database schemas, XHTTP stream-up, Hysteria2, REALITY, fixed WS/gRPC routes, a negative arbitrary-localhost-port test, nginx syntax, UFW and matching panel/CLI versions. A separate workflow exercises Backup/Restore v2 on isolated fixtures with real tar/gzip, SQLite and nginx, including unsafe archive paths/links, incompatible OS versions, failures and service checks.

Bash syntax is checked for the installer, patch, AdGuard and backup utility. Automated tests do not replace real client acceptance on a disposable VPS. The live validation above describes a previously recorded checkpoint, not every future commit.

## Development and releases

- `main` is the primary development line. Changes use `feat/*`, `fix/*`, `docs/*` or `chore/*` branches and pull requests.
- Required checks: `Stack XHTTP and security` and `Stack Backup and restore`. External reviewers are not required for a single-maintainer project.
- Stack releases use `vX.Y.Z` versions from validated `main` with release notes. Renaming alone does not create a release.
- Existing `personal-v*` tags/releases retain their historical meaning. Their release badge does not certify the latest security checkpoint.
- `personal` is temporarily retained for old-link compatibility. New commands use `main`; upstream is not synchronized automatically.
- See [CONTRIBUTING.md](CONTRIBUTING.md) for branch protection and emergency recovery details.

Historical paths `/usr/local/lib/3x-ui-pro` and `/etc/sysctl.d/99-3x-ui-pro.conf` are retained for installation and Backup/Restore v2 compatibility.

## Attribution

3x-ui Stack remains in the [mozaroc/3x-ui-pro](https://github.com/mozaroc/3x-ui-pro) fork network and follows an independent development path. The 3x-ui panel and its releases are provided by [MHSanaei/3x-ui](https://github.com/MHSanaei/3x-ui). Useful upstream changes are reviewed and adopted individually.

Original component authors retain their rights. This migration adds no new license for inherited code.
