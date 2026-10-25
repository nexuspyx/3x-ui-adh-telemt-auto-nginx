# 3X-UI + Telemt + Hardened Nginx + AdGuard Home (Anti-TSPU Stealth Edition)

> 🛡️ **Automated deployment suite for next-generation personal proxy servers.**  
> Advanced censorship & TSPU evasion, active probe defense, unified ingress via port 443, silent SSL issuance via Cloudflare DNS-01 API (port 80 permanently closed), integrated Telegram Web Proxy (Telemt) with 200 OK decoy camouflage, AdGuard Home with 0 ms local DNS RAM cache, and intelligent bot protection.

---

## 🚀 One-Command Quick Start

Connect to your clean VPS via SSH as `root` and execute:

```bash
bash -c "$(curl -fsSL https://raw.githubusercontent.com/nexuspyx/3x-ui-adh-telemt-auto-nginx/main/install.sh)"
```

Or via standard pipe:
```bash
curl -sSL https://raw.githubusercontent.com/nexuspyx/3x-ui-adh-telemt-auto-nginx/main/install.sh | bash
```

### What the installer automates:
1. **Interactive Wizard**:
   - Prompts for your domain and Cloudflare API credentials.
   - Allows customization of secret URL paths (`Locations`) and internal ports, or generates strong random values:
     - 3X-UI Panel path (`PANEL_PATH`)
     - Base64 Subscription path (`SUB_PATH`)
     - JSON Subscription path for Happ / Sing-box / v2rayNG (`SUB_JSON_PATH`)
     - **Telemt Telegram Web Proxy (`TG_PATH`, e.g. `/tg` or `/custom-path`)**
     - **AdGuard Home DoH (`ADH_DOH_PATH`, e.g. `/dns-query` or `/my-secret-doh`)**
     - VLESS WebSocket, XHTTP, gRPC, and Trojan gRPC paths
2. **Silent SSL (Cloudflare DNS-01 API)**:
   - Automated certificate issuance through Cloudflare API.
   - **Port 80 is PERMANENTLY CLOSED in UFW and Nginx** — the server never exposes an open HTTP port to internet scanners.
3. **Kernel Tuning & Stealth Mode**:
   - **IPv6 is completely disabled** at the kernel level (eliminates 1–3s DNS and outbound connection delays).
   - **ICMP Ping replies disabled**: server completely ignores ping (`icmp_echo_ignore_all = 1` - 100% packet drop stealth).
   - Enabled TCP **BBR** congestion control and TCP Fast Open.
4. **Deployment of 3X-UI (All 7 Inbounds)**:
   - Reality TCP (`:8443` -> Fallback `:9443` with `xver: 1`).
   - VLESS WebSocket.
   - VLESS XHTTP (via Unix Domain Socket `/dev/shm/uds2023.sock`).
   - Trojan gRPC.
   - VLESS gRPC.
   - Hysteria 2 UDP (`:443`).
   - AmneziaWG (`:8443`).
   - **Dual Subscriptions**: Standard Base64 and Xray JSON.
5. **Telemt (Telegram Web Proxy) with 200 OK Decoy**:
   - Proxies Telegram traffic over HTTPS on your custom path.
   - Full support for WebSocket lanes and HTTPS lanes.
   - Internal Decoy server (`:18082`): returns genuine cover site with HTTP **200 OK** on any censorship probing instead of suspicious 404s.
   - Web management interface via Telemt Panel (`:65001` HTTPS) with automated GeoIP (GeoLite2-City) database and client geography map.
6. **AdGuard Home (0 ms Local DNS + DoH with Custom Path)**:
   - Local DNS resolver (`127.0.0.1:57998`) provides **0 ms RAM caching** for Xray Outbound.
   - DoH available on your secret custom path (e.g. `https://domain.com/my-secret-doh`).
   - Web management dashboard (`:3000`).
7. **Anti-Bot Shield in Nginx**:
   - Drops connections instantly (`return 444`) for web scrapers (Google, Yandex), scanners (Shodan, Censys, sqlmap, nikto, etc.), and exploit scanners (`.env`, `wp-login`).
   - De-indexing headers `X-Robots-Tag: noindex, nofollow` and `robots.txt` Disallow.
8. **Placeholder Page & Custom Website Hosting**:
   - Serves a clean, lightweight placeholder page with `200 OK` by default (also used by the Decoy server and Reality fallback).
   - Server directory to upload your own website: `/var/www/html/` (simply upload your website files replacing `index.html`).

---

## 🗺️ Network Architecture & Ports

All web services are multiplexed over a **single ingress port 443**:

```
                              Incoming Traffic (Internet)
                                         │
                   ┌─────────────────────┴─────────────────────┐
                   │                                           │
             TCP PORT 443                                UDP PORT 443
                   │                                           │
         Nginx Stream SNI Routing                      Hysteria 2 UDP
                   │
         ┌─────────┴─────────┐
         │                   │
  SNI: Reality         SNI: Main Domain
         │                   │
  Xray Reality (:8443)       │
         │ (Fallback xver:1) │
         └─────────┬─────────┘
                   │
         Nginx HTTP SSL (:9443)
                   │
    ┌──────────────┼──────────────┬──────────────┬──────────────┬──────────────┐
    │              │              │              │              │              │
Camouflage Site  3X-UI Panel  Subscriptions (x2) Telemt Proxy    VLESS /      AdGuard DoH
    (:9443)      (Admin)      (Base64 & JSON)    (Custom Path)   Trojan      (Custom Path)
               (Loopback)       (Loopback)         (:18080)    (WS/gRPC/UDS)   (:44712)
                                                     │
                                            On Censorship Probes
                                                     │
                                            Decoy Server (:18082)
                                               (HTTP: 200 OK)
```

---

## 📁 Telemt: Client Keys Storage & Seamless Migration

To migrate your Telegram proxy to a new VPS without changing client links:

- **Configuration & Keys File:** `/etc/telemt/config.toml`
  - Section `[access.users]` holds client usernames and their 32-hex keys:
    ```toml
    [access.users]
    Client1 = "89bbf4be3eb7f2a085ec010caa541f26"
    Client2 = "6d4a813aeca5b00578e7bf4e9a7243d2"
    ```
- **Traffic limits & stats:** `/etc/telemt/telemt.limit.json`
- **Panel configuration:** `/etc/telemt-panel/config.toml`

### How to migrate clients to a new VPS:
1. Run this installer on the new server using the same domain.
2. Copy `/etc/telemt/config.toml` from the old server to the new one.
3. Restart the service: `systemctl restart telemt`.
4. All client links (`tg://proxy?...` and `tg://webproxy?...`) will continue working immediately with zero reconfiguration!

---

## ⚙️ "Server Configuration" Module (Stealth Dashboard)

No more manual editing of Nginx configuration files, 3X-UI SQLite databases, or running full reinstalls when changing paths or ports!

The suite includes a dedicated management dashboard built with **Apple Human Interface Guidelines** (fully responsive on PC and iPhone):

- **Stealth Mode by Default**: The management port is **closed in UFW by default** and does not appear on internet scanners.
- **Secure Access**: Accessing `https://your-domain:port/` automatically redirects to a private secret path with administrator authentication.
- **Atomic Two-Way Synchronization**: Changing any port or path (3X-UI, Base64/JSON subs, Telemt, AdGuard DoH, VLESS-WS, XHTTP, gRPC, Trojan) **atomically updates all configs** (Nginx, SQLite 3X-UI, Telemt, AdGuard) with `nginx -t` validation and rollback protection.
- **Logout with Instant UFW Port Lockdown**: Clicking "Logout" ends the session and closes the management port in UFW immediately.
- **Configurable Auto-Lock**: Opening the port sets a 30-minute auto-timer after which UFW automatically locks the port down again.
- **Zero-Overhead Resource Monitor**: CPU, RAM, and NVMe metrics read directly from Linux kernel pseudo-files (`/proc`), with polling paused when the browser tab is hidden.
- **Live Links Summary**: Instant access to all connection links, 1-click clipboard copy, and built-in offline QR code generator.

### CLI Quick Commands via SSH:
```bash
server-mgr open        # Open the management port in UFW with 30-min auto-timer
server-mgr close       # Lock down UFW immediately (keeping only 22 and 443 open)
server-mgr status      # Show current status, port, and access URL
server-mgr reset-pass  # Change admin password
server-mgr             # Interactive terminal menu (TUI)
```

You can also access the module via **SSH tunnel** without exposing any port to the public internet:
```bash
ssh -L 8088:127.0.0.1:<MGMT_PORT> root@your-domain
# then open in your browser: http://localhost:8088/
```

---

## 🔒 Security, Firewall (UFW) & Fail2ban

After installation, the system is fully hardened:
- **UFW Firewall**:
  - `SSH` — Allowed (only your verified SSH port, default `22/tcp`).
  - `443/tcp` — Allowed (Nginx Stream + Reality / Web).
  - `443/udp` — Allowed (Hysteria 2 / QUIC).
  - `80/tcp` — **PERMANENTLY DENIED** (`ufw deny 80/tcp`).
  - All other ports — **BLOCKED** (`default deny incoming`).
- **Fail2ban (Brute-Force Protection)**:
  - `nftables` integration: attackers are banned at the kernel level across **all ports on the server** (`allports`).
  - **Progressive ban (`bantime.increment`)**: repeat offenders receive exponentially increasing ban durations (up to 5 weeks).
  - Real-time `systemd` journal monitoring.

---

## 📄 Access Credentials & Summary

Upon installation completion, all credentials, passwords, and links are saved to:
```bash
cat /root/server_credentials.txt
```
The file contains:
- 3X-UI admin link, username, and password.
- Both subscription links (Base64 and JSON).
- Telemt Panel link and both connection formats (`tg://webproxy?...` and `tg://proxy?...`).
- AdGuard Home dashboard credentials and DoH link.
- Full list of configured inbounds and ports.
