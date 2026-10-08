<div align="center">

[🇷🇺 Русский](README.md) · 🇬🇧 **English** · [🇮🇷 فارسی](README_FA.md) · [🇨🇳 简体中文](README_ZH_CN.md)

# 🚀 3x-ui Auto Nginx

### Automated 3x-ui / Xray deployment on your own VPS

**REALITY · XHTTP · Hysteria2 · WebSocket · gRPC · nginx · HTTPS · Backup / Restore**

Two domains, a clean VPS, and a few minutes to install.

[![XHTTP / security](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-xhttp.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-xhttp.yml)
[![Backup / Restore](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-backup.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-backup.yml)
[![Ubuntu](https://img.shields.io/badge/Ubuntu-24.04%20%7C%2026.04-E95420?logo=ubuntu&logoColor=white)](#technical-details)
[![Releases](https://img.shields.io/github/v/release/xPROMSx/3x-ui-auto-nginx)](https://github.com/xPROMSx/3x-ui-auto-nginx/releases)

[Releases](https://github.com/xPROMSx/3x-ui-auto-nginx/releases) · [Telegram Web Proxy Manager](#telemt-web-manager) · [Issues](https://github.com/xPROMSx/3x-ui-auto-nginx/issues)

</div>

**3x-ui Auto Nginx** installs [3x-ui](https://github.com/MHSanaei/3x-ui) and Xray with nginx, HTTPS, connection profiles, subscriptions, diagnostics, and Backup / Restore. Choose AdGuard Home with DoH during setup if needed.

Bring two domains and a clean VPS. A cover website is selected and deployed automatically.

## ✨ Why this project

| | |
| --- | --- |
| **Preconfigured connections**<br>REALITY, XHTTP, Hysteria2, WebSocket, and Trojan gRPC are already set up. | **Private internal services**<br>The panel and subscription service don't expose their internal ports to the internet. |
| **Restricted nginx routing**<br>Requests cannot be forwarded to arbitrary local ports. | **Backup / Restore v3**<br>Roll back your VPS or restore to a new server, including one from another hosting provider. |
| **Automatic certificates**<br>Let's Encrypt issuance and renewal need no manual configuration. | **AdGuard Home + DoH**<br>Opt in during setup; no third domain needed. |

<a id="installation"></a>

## 🚀 Quick start

Point the DNS records of **two domains** to your VPS IP address: one for the panel, one for REALITY. Connect via SSH as **root** and allow TCP **80/443**, UDP **443**. [Supported systems](#technical-details).

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

Backup / Restore **v3** lets you roll back your VPS or recover a compatible server after an OS reinstall. The utility is installed with the server; run as root:

```bash
# Create a new backup
x-ui-backup backup

# List available backups
x-ui-backup list

# Restore a selected backup
x-ui-backup restore /var/backups/x-ui/<archive>.tar.gz
```

Archives are saved in `/var/backups/x-ui/`.

> **Backups contain sensitive data:** the client database, passwords, and certificate private keys. Keep a copy **off the VPS** and restore only trusted archives.

[Recovery on a new VPS](#technical-details).

## 🔐 Security

Service interfaces are not exposed directly to the internet. The panel, subscriptions, and additional services are accessible through nginx and HTTPS, while internal ports remain local.

There is no generic proxy to arbitrary localhost ports. Critical errors stop installation or restore rather than starting an incomplete configuration.

<a id="technical-details"></a>

<details>
<summary>⚙️ Technical details and compatibility</summary>

- **Systems:** Ubuntu 24.04, Ubuntu 26.04, and Debian 13. Debian 12 is unsupported.
- **UFW:** adds 80/tcp, 443/tcp, and 443/udp. Inactive UFW is enabled only after the SSH port is detected and allowed; otherwise it remains inactive with a warning. Restore never enables UFW.
- **Certificates:** Let's Encrypt webroot and `certbot.timer` renew certificates automatically without stopping nginx.
- **3x-ui version:** latest stable release by default. Select a specific release with `-version v3.9.0`; the minimum is v3.8.0.

**Recovery on a new VPS:** match the backup's OS, OS version, and architecture. Update DNS if the IP changes. Do not run `x-ui-latest.sh` — install only the utility, then restore a trusted archive:

```bash
curl -fSL https://raw.githubusercontent.com/xPROMSx/3x-ui-auto-nginx/main/assets/backup/x-ui-backup.sh -o /tmp/x-ui-backup
install -o root -g root -m 0755 /tmp/x-ui-backup /usr/local/bin/x-ui-backup
rm -f /tmp/x-ui-backup
x-ui-backup restore /root/<archive>.tar.gz
```

SSH, OS configuration, and the base firewall remain the administrator's responsibility. For v2 archives, use the utility from the matching older release.

</details>

<a id="telemt-web-manager"></a>

## ✈️ Telegram Web Proxy Manager

[A companion project](https://github.com/xPROMSx/telegram-web-proxy-manager) for your own Telegram WEB Proxy: HTTPS, a cover site, and updates with rollback.

The projects are independent; **this installer does not install Telegram Web Proxy Manager**.

## 🤝 Credits / Origins

Based on [3x-ui-pro](https://github.com/mozaroc/3x-ui-pro), this project is maintained independently. Since the fork, nginx/SNI routing and XHTTP configuration have been substantially reworked, TLS certificate issuance and automatic renewal redesigned, and Backup / Restore v3 introduced with recovery and rollback capabilities. Hysteria2 on UDP/443 was added, and identified security issues were fixed.

The panel comes from [MHSanaei/3x-ui](https://github.com/MHSanaei/3x-ui). Third-party authors retain their rights and existing licenses.

<div align="center">

**Two domains. A few minutes. Your own 3x-ui / Xray server.**

⭐ If this installer helped you, consider giving it a [Star on GitHub](https://github.com/xPROMSx/3x-ui-auto-nginx). It helps others discover the project.

[Install](#installation) · [Releases](https://github.com/xPROMSx/3x-ui-auto-nginx/releases) · [Issues](https://github.com/xPROMSx/3x-ui-auto-nginx/issues)

</div>
