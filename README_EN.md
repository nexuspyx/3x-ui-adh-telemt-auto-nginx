<div align="center">

# 🚀 3x-ui Auto Nginx

**One command. Two domains. Ready-to-use 3x-ui server.**

Automatic **nginx · SSL · Fake Site · REALITY · XHTTP · Hysteria2 · Backup / Restore**

[![XHTTP / security](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-xhttp.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-xhttp.yml)
[![Backup / Restore](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-backup.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-backup.yml)
[![Ubuntu](https://img.shields.io/badge/Ubuntu-24.04%20%7C%2026.04-E95420?logo=ubuntu&logoColor=white)](#technical-details)
[![Releases](https://img.shields.io/github/v/release/xPROMSx/3x-ui-auto-nginx)](https://github.com/xPROMSx/3x-ui-auto-nginx/releases)

[Русский](README.md) · [Releases](https://github.com/xPROMSx/3x-ui-auto-nginx/releases) · [Telemt WEB Manager](#telemt-web-manager) · [Issues](https://github.com/xPROMSx/3x-ui-auto-nginx/issues)

</div>

A turnkey deployment project for [3x-ui](https://github.com/MHSanaei/3x-ui) and Xray. Supply a panel domain and a REALITY domain; the script installs the server, configures nginx and TLS, and deploys a cover website.

## ⚡ Get started

1. Point **two domains to your VPS**: one for the panel, one for REALITY.
2. On a clean supported OS, connect to the server over SSH as **root**.
3. Run:

```bash
curl -fSL https://raw.githubusercontent.com/xPROMSx/3x-ui-auto-nginx/main/x-ui-latest.sh -o x-ui-latest.sh
bash x-ui-latest.sh
```

The script will prompt for both domains. If you prefer to pass them directly:

```bash
bash x-ui-latest.sh -subdomain panel.example.com -reality_domain reality.example.com
```

Allow inbound TCP 80/443 and UDP 443; the domains must resolve to this server for certificate issuance.

> **⚠️ Clean installation or full reinstall only.** `x-ui-latest.sh` removes an existing 3x-ui installation, including the panel database and nginx configuration. **Do not use it as a normal update command on a working VPS.** Detected 3x-ui/nginx/TLS state requires exact `YES` before file removal, including uninstall. If the server is already configured, copy a backup off-server first.

## 🌐 Two domains → ready server

| You provide | The script prepares | You receive |
| --- | --- | --- |
| Panel domain + REALITY domain | 3x-ui/Xray, nginx, Let's Encrypt TLS, automatic fake site, five inbound profiles, subscriptions, diagnostics, Backup / Restore and UFW integration | Panel URL, generated login/password and diagnostics URL |

**Create clients in 3x-ui after installation.** Share links and subscriptions are managed through the panel; the installer does not seed client accounts.

## ✨ Built for a quick deployment

| | |
| --- | --- |
| **⚡ Fully automated**<br>Installation and server configuration from two domains. | **🔐 Generated secrets**<br>Panel credentials, REALITY keys/short IDs and paths are generated automatically. |
| **🌐 nginx + SNI + TLS**<br>Ready routing and Let's Encrypt certificates. | **🥸 Automatic Fake Site**<br>A bundled cover site is randomly selected and deployed for you. |
| **🚀 Modern transports**<br>REALITY, XHTTP stream-up and Hysteria2, with optional WS/gRPC profiles. | **💾 Backup / Restore**<br>Same-host rollback and recovery on a compatible clean OS. |
| **📊 Diagnostics**<br>Panel-authenticated MTR and LibreSpeed tools. | **🔥 UFW-aware**<br>Adds application rules while preserving existing policy and rules. |

### 🚀 Transport profiles

| Transport | Fresh-install default | Connection model |
| --- | --- | --- |
| VLESS + REALITY | ✅ Enabled | TCP 443 → nginx SNI → Xray |
| VLESS + XHTTP `stream-up` | ✅ Enabled | TLS/HTTP/2 → nginx → Unix socket |
| Hysteria2 | ✅ Enabled | UDP 443 → Xray, TLS + H3 |
| VLESS + WebSocket | ⏸ Disabled | TLS → fixed nginx route → Xray |
| Trojan + gRPC | ⏸ Disabled | TLS/HTTP/2 → fixed nginx route → Xray |

All five profiles are created. Enable WS or Trojan gRPC manually in 3x-ui when needed. Client support depends on the transport and client version.

### 🥸 Automatic Fake Site

No need to build a cover website yourself: the installer selects a page from the bundled fake-site collection and deploys it with nginx and TLS. The site is part of the deployment, not a guarantee of traffic invisibility.

<a id="telemt-web-manager"></a>

## ✈️ Need a Telegram proxy too?

### [Telemt WEB Manager](https://github.com/xPROMSx/telemt-web-manager)

A companion project for automated Telemt WEB proxy installation and management, with updates, rollback and nginx/TLS integration.

**3x-ui Auto Nginx** handles your 3x-ui/Xray server; **Telemt WEB Manager** handles Telemt WEB proxy. They are separate projects: this installer does not install Telemt. Shared-host deployment requires checking ports, domains and nginx configuration.

## 💾 Backup / Restore

Run as root. A fresh install includes `/usr/local/bin/x-ui-backup`:

```bash
x-ui-backup backup
x-ui-backup list
```

The current backup format is **v3**. This tool does not restore v2 archives; use the utility from the matching older release.

Archives are saved in `/var/backups/x-ui/` with root-only permissions. They contain client/database state, certificates/private keys and runtime secrets. **Copy the archive off the VPS** to a PC, NAS or another secure store.

For rollback on the current VPS:

```bash
x-ui-backup restore /var/backups/x-ui/<archive>.tar.gz
```

Success ends with `Restore completed successfully.` Restore covers managed application state, not the entire OS.

<details>
<summary>🛟 Recovery on a new VPS / installing the backup utility</summary>

Use **the same OS ID, VERSION_ID and architecture** as the backup, e.g. Ubuntu 26.04 amd64 → Ubuntu 26.04 amd64. Ubuntu 24.04 or arm64 is not a compatible target for that archive.

1. Install the compatible clean OS and run your usual OS/bootstrap script if needed.
2. **Do not run `x-ui-latest.sh`.** Install only the backup utility (also suitable for an existing installation missing the tool):

   ```bash
   curl -fsSL https://raw.githubusercontent.com/xPROMSx/3x-ui-auto-nginx/main/assets/backup/x-ui-backup.sh -o /tmp/x-ui-backup
   install -o root -g root -m 0755 /tmp/x-ui-backup /usr/local/bin/x-ui-backup
   rm -f /tmp/x-ui-backup
   ```

3. Transfer the saved archive securely, then restore:

   ```bash
   x-ui-backup restore /root/x-ui-backup-....tar.gz
   ```

4. If the server IP changed, point the existing domains' DNS to the new VPS. Use your existing panel and client configurations after recovery.

Restore installs missing application dependencies and restores panel/runtime, nginx, certificates, web/diagnostics, project systemd units, managed sysctl and webroot renewal through `certbot.timer`. SSH, `authorized_keys`, fail2ban, base UFW policy and OS/bootstrap state remain the administrator's responsibility. Restore adds 80/tcp, 443/tcp and 443/udp rules but never enables UFW; unrelated cron jobs are preserved.

The previous Backup/Restore v2 was validated on a real Ubuntu 26.04 amd64 VPS: same-host rollback, clean-host recovery after bootstrap without the installer, reboot persistence and panel/real client connectivity (3x-ui 3.9.0, Xray 26.9.30, nginx 1.28.3).

</details>

<a id="technical-details"></a>

## 🔧 Technical details

<details>
<summary>Platforms, routing, firewall and optional tools</summary>

- **Platforms:** Ubuntu 26.04 amd64 has live VPS validation; Ubuntu 24.04 runs in CI. Debian 13 is accepted by OS checks without equivalent live coverage; Debian 12 is unsupported. Not every architecture is tested. QEMU CPUs do not block installation; missing hardware AES produces a performance advisory.
- **Routing:** nginx owns TCP 443 and routes by SNI to REALITY/Xray on 8443 or the panel TLS vhost on 7443. The REALITY camouflage target uses 9443. XHTTP uses a Unix socket; WS/gRPC have fixed paths/backends. Hysteria2 owns UDP 443 independently.
- **UFW:** active UFW receives only 80/tcp, 443/tcp and 443/udp rules. Inactive UFW is enabled only after detecting and allowing SSH ports; otherwise it stays inactive with a warning. Existing rules/default policy are preserved, with no hardcoded SSH port 22.
- **Versions:** latest stable 3x-ui is selected by default. Use `-version <tag>` to select a panel release; binary and CLI come from that same tag.
- **Diagnostics/subscriptions:** panel-authenticated MTR/LibreSpeed, JSON and Clash/Mihomo subscriptions. New custom WS/gRPC inbounds require explicit nginx routes; there is no arbitrary localhost-port proxy.
- **Optional tools:** `x-ui-adguard.sh` is retained for optional AdGuard Home integration and future development; the main installer does not run it. `x-ui-patch.sh` is a secondary maintenance utility, not a database migration or universal updater.
- **TLS renewal:** webroot `/var/www/acme` + distro `certbot.timer`. Nginx stays online and reloads gracefully after renewal; x-ui restarts only when the panel certificate renews. Release acceptance includes `certbot renew --dry-run` for both certificates.
- **Compatibility:** `/usr/local/lib/3x-ui-pro` and `/etc/sysctl.d/99-3x-ui-pro.conf` are deliberately preserved for Backup/Restore compatibility.

</details>

<details>
<summary>Validation, development and releases</summary>

CI checks all transport/Host profiles, nginx syntax, negative arbitrary-localhost-port access, firewall behavior, version pinning, the destructive guard, webroot/timer renewal and canonical sources. Backup/Restore tests use isolated fixtures with real tar/gzip, SQLite and nginx. Bash syntax covers installer, patch, AdGuard and backup scripts. CI does not replace live client acceptance.

Development uses PRs to `main`; see [CONTRIBUTING.md](CONTRIBUTING.md). Required job names remain `Stack XHTTP and security` and `Stack Backup and restore` for branch-protection compatibility.

Historical `personal-v*` releases and branches are preserved. Future project releases belong to 3x-ui Auto Nginx; project versioning is independent of upstream 3x-ui/Xray versions. No new release is implied by branding changes.

</details>

## 🤝 Credits / Origins

Derived from [mozaroc/3x-ui-pro](https://github.com/mozaroc/3x-ui-pro). The panel is provided by [MHSanaei/3x-ui](https://github.com/MHSanaei/3x-ui). **3x-ui Auto Nginx** develops independently and may selectively adopt useful upstream changes after review.

Third-party authors retain their rights and existing license/copyright statements. No new license is added for inherited code.
