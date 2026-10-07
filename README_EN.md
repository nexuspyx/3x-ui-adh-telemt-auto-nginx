<div align="center">

# 🚀 3x-ui Auto Nginx

### Automated 3x-ui / Xray deployment on your own VPS

**REALITY · XHTTP · Hysteria2 · WebSocket · gRPC · nginx · HTTPS · Backup / Restore**

Two domains, a clean VPS, and a few minutes to install.

[![XHTTP / security](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-xhttp.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-xhttp.yml)
[![Backup / Restore](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-backup.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-backup.yml)
[![Ubuntu](https://img.shields.io/badge/Ubuntu-24.04%20%7C%2026.04-E95420?logo=ubuntu&logoColor=white)](#technical-details)
[![Releases](https://img.shields.io/github/v/release/xPROMSx/3x-ui-auto-nginx)](https://github.com/xPROMSx/3x-ui-auto-nginx/releases)

[Русский](README.md) · [Releases](https://github.com/xPROMSx/3x-ui-auto-nginx/releases) · [Telegram Web Proxy Manager](#telemt-web-manager) · [Issues](https://github.com/xPROMSx/3x-ui-auto-nginx/issues)

</div>

**3x-ui Auto Nginx** installs [3x-ui](https://github.com/MHSanaei/3x-ui) and Xray with nginx, HTTPS, connection profiles, subscriptions, diagnostics, and Backup / Restore. Choose AdGuard Home with DoH during setup if needed.

Bring two domains and a clean VPS. A cover website is selected and deployed automatically.

## ✨ Why this project

| | |
| --- | --- |
| **🚀 Preconfigured connections**<br>REALITY, XHTTP, Hysteria2, WebSocket, and Trojan gRPC are already set up. | **🔐 Private internal services**<br>The panel and subscription service don't expose their internal ports to the internet. |
| **🌐 Restricted nginx routing**<br>Requests cannot be forwarded to arbitrary local ports. | **💾 Backup / Restore v3**<br>Roll back your VPS or recover after reinstalling the OS. |
| **♻️ Automatic certificates**<br>Let's Encrypt issuance and renewal need no manual configuration. | **🛡️ AdGuard Home + DoH**<br>Opt in during setup; no third domain needed. |

<a id="installation"></a>

## 🚀 Quick start

Point **two domains** at a clean VPS: one for the panel, one for REALITY. Connect via SSH as **root** and allow TCP **80/443**, UDP **443**. [Supported systems](#technical-details).

```bash
curl -fSL https://raw.githubusercontent.com/xPROMSx/3x-ui-auto-nginx/main/x-ui-latest.sh -o x-ui-latest.sh
bash x-ui-latest.sh
```

The installer prompts for both domains. You can also supply them directly:

```bash
bash x-ui-latest.sh -subdomain panel.example.com -reality_domain reality.example.com
```

> **⚠️ Clean install or full reinstall only.** `x-ui-latest.sh` removes the 3x-ui database and nginx configuration. **Do not use it to update a working VPS.** Save a Backup off-server first. A detected installation requires exact uppercase `YES` for reinstall or uninstall.

You receive a **panel URL, random credentials, and diagnostics URL** (3x-ui-authenticated MTR/LibreSpeed). With AdGuard Home: admin URL, password, and DoH endpoint.

## 🚀 Connection profiles

| Connection | Status |
| --- | :---: |
| VLESS + REALITY | ✅ Ready to use |
| VLESS + XHTTP | ✅ Ready to use |
| Hysteria2 | ✅ Ready to use |
| VLESS + WebSocket | ✅ Ready to use |
| Trojan + gRPC | ✅ Ready to use |

All five profiles are preconfigured. Enable whichever you need in 3x-ui without editing nginx. **Create clients in the panel**; compatibility depends on your app and version.

### 🔗 Subscriptions

Standard subscriptions, JSON, and **Mihomo / Clash** use nginx and HTTPS. `provider=1` returns the original subscription for proxy providers instead of a full Clash configuration.

## 🛡️ AdGuard Home + DoH

Optional, default **N**. No third domain needed: the admin UI uses a random `/adg-.../` prefix on your panel domain, with DoH at `https://panel.example.com/dns-query`.

The installer does not open public TCP/UDP **53**. AdGuard Home configuration and data are included in **Backup / Restore v3**.

## 💾 Backup / Restore

**Format v3** supports same-host rollback or recovery on a compatible system after an OS reinstall. The utility is already installed; run as root:

```bash
x-ui-backup backup
x-ui-backup list
x-ui-backup restore /var/backups/x-ui/<archive>.tar.gz
```

> **Archives contain sensitive data:** the client database, passwords, and certificate private keys. Store copies from `/var/backups/x-ui/` **off the VPS**. Restore only trusted archives created by this utility and kept under your control: validation detects damage and incompatibility, but does not authenticate archive provenance.

Use **the same OS, version, and architecture**. Restore covers application state, not the whole OS. [New-VPS recovery guide](#technical-details).

## 🔐 Security

- Internal services are not exposed directly to the internet.
- No proxying to arbitrary local ports.
- 3x-ui and its CLI share one SHA-256-verified archive.
- Critical errors stop installation and restore.
- Xray process and socket checks run after startup.
- Existing UFW rules and policy are preserved.

<a id="technical-details"></a>

<details>
<summary>⚙️ Technical details and compatibility</summary>

- **Systems:** Ubuntu 26.04 amd64 has been tested on a real VPS; Ubuntu 24.04 runs in CI. Debian 13 is accepted without equivalent live coverage; Debian 12 is unsupported. The amd64 results don't imply that every architecture has been tested.
- **nginx / Xray:** nginx handles TCP 80/443; Hysteria2 handles UDP 443. SNI routes traffic to REALITY or HTTPS. Internal services listen locally, and XHTTP uses a Unix socket. Panel/API and subscription connections use internal HTTPS. API-token multi-node access goes through the panel domain with TLS verification; direct native panel mTLS is outside this setup.
- **UFW:** adds 80/tcp, 443/tcp, and 443/udp. Inactive UFW is enabled only after the SSH port is detected and allowed; otherwise it remains inactive with a warning. Restore never enables UFW. Other rules and policy are preserved.
- **Certificates:** Let's Encrypt webroot `/var/www/acme` and the distro `certbot.timer`. nginx stays online during renewal; x-ui restarts only when the panel certificate renews.
- **Versions and CPU:** latest stable 3x-ui by default; `-version v3.9.0` selects a release, with v3.8.0 as the minimum. QEMU/KVM does not block installation; missing hardware AES only triggers a performance advisory.
- **AdGuard Home:** nginx terminates TLS; AGH web and DNS listeners stay local. `x-ui-adguard.sh` is a non-destructive stub and does not perform a separate installation.
- **Validation:** CI covers profiles, nginx, firewall, certificates, and Backup / Restore; it doesn't replace real-client testing. Backup v3 with AdGuard Home was tested on Ubuntu 26.04 amd64: archive creation, repeated same-host restore, TLS/DoH, panel/XHTTP, and startup after reboot. Clean-host v3 recovery, Debian, and arm64 are not claimed as live-validated.

**New VPS:** use the same OS ID, VERSION_ID, and architecture; update DNS if the IP changes. Install only the utility instead of running `x-ui-latest.sh`, then restore a trusted archive:

```bash
curl -fSL https://raw.githubusercontent.com/xPROMSx/3x-ui-auto-nginx/main/assets/backup/x-ui-backup.sh -o /tmp/x-ui-backup
install -o root -g root -m 0755 /tmp/x-ui-backup /usr/local/bin/x-ui-backup
rm -f /tmp/x-ui-backup
x-ui-backup restore /root/<archive>.tar.gz
```

SSH, OS configuration, and the base firewall remain the administrator's responsibility. For v2 archives, use the utility from the matching older release. `/usr/local/lib/3x-ui-pro` and `/etc/sysctl.d/99-3x-ui-pro.conf` are retained for compatibility.

</details>

<a id="telemt-web-manager"></a>

## ✈️ Telegram Web Proxy Manager

[A companion project](https://github.com/xPROMSx/telegram-web-proxy-manager) for your own Telegram WEB Proxy: HTTPS, a cover site, and updates with rollback.

The projects are independent; **this installer does not install Telegram Web Proxy Manager**.

## 🤝 Credits / Origins

Based on [3x-ui-pro](https://github.com/mozaroc/3x-ui-pro), with more than half of the core logic reworked or replaced since the fork. XHTTP/nginx routing, certificates, and Backup / Restore were reworked; Hysteria2 was added and identified security issues fixed. The project is developed independently.

The panel comes from [MHSanaei/3x-ui](https://github.com/MHSanaei/3x-ui). Third-party authors retain their rights and existing licenses.

<div align="center">

**Two domains. A few minutes. Your own 3x-ui / Xray server.**

[Install](#installation) · [Releases](https://github.com/xPROMSx/3x-ui-auto-nginx/releases) · [Issues](https://github.com/xPROMSx/3x-ui-auto-nginx/issues)

</div>
