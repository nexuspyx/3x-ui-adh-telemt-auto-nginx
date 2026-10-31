#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Build Timestamp: 12:55.31.10.2026
"""
==============================================================================
«КОНФИГУРИРОВАНИЕ СЕРВЕРА» (SERVER CONFIGURATION MANAGER)
Stealth Management Daemon & Apple HIG Web Dashboard for 3x-ui + Telemt + Nginx
==============================================================================
"""

import os
import sys
import json
import time
import re
import ssl
import shutil
import socket
import sqlite3
import hashlib
import secrets
import subprocess
import threading
from urllib.parse import urlparse, parse_qs
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

CONFIG_FILE = "/etc/server-manager/config.json"
AUTO_LOCK_FILE = "/etc/server-manager/auto_lock.timer"
VHOST_TEMPLATE_FILE = "/etc/server-manager/vhost.conf.template"
CREDENTIALS_FILE = "/root/server_credentials.txt"
XUI_DB_PATH = "/etc/x-ui/x-ui.db"
TELEMT_CONF_PATH = "/etc/telemt/config.toml"
ADGUARD_CONF_PATH = "/opt/AdGuardHome/AdGuardHome.yaml"

# In-memory session store & rate limiting
SESSIONS = {}  # token -> {"user": user, "expires": timestamp}
FAILED_ATTEMPTS = {}  # ip -> {"count": int, "blocked_until": timestamp}
UNLOCK_EXPIRY = 0  # timestamp when firewall should auto-lock (0 = no timer)
CPU_PREV_TOTAL = 0
CPU_PREV_IDLE = 0


# ==============================================================================
# [1] SYSTEM RESOURCE MONITOR (ZERO-OVERHEAD DIRECT KERNEL READS)
# ==============================================================================

def get_cpu_usage():
    global CPU_PREV_TOTAL, CPU_PREV_IDLE
    try:
        with open("/proc/stat", "r") as f:
            line = f.readline()
        parts = [float(x) for x in line.split()[1:8]]
        idle = parts[3] + parts[4]
        total = sum(parts)
        if CPU_PREV_TOTAL == 0:
            CPU_PREV_TOTAL = total
            CPU_PREV_IDLE = idle
            return 0.0
        delta_total = total - CPU_PREV_TOTAL
        delta_idle = idle - CPU_PREV_IDLE
        CPU_PREV_TOTAL = total
        CPU_PREV_IDLE = idle
        if delta_total <= 0:
            return 0.0
        return round((1.0 - (delta_idle / delta_total)) * 100, 1)
    except Exception:
        return 0.0


def get_ram_usage():
    try:
        mem = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                parts = line.split(":")
                if len(parts) == 2:
                    mem[parts[0].strip()] = int(parts[1].split()[0])
        total = mem.get("MemTotal", 1)
        avail = mem.get("MemAvailable", mem.get("MemFree", 0))
        used_pct = round((1.0 - (avail / total)) * 100, 1)
        return {
            "percent": used_pct,
            "total_mb": round(total / 1024),
            "used_mb": round((total - avail) / 1024)
        }
    except Exception:
        return {"percent": 0.0, "total_mb": 0, "used_mb": 0}


def get_disk_usage():
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        avail = st.f_bavail * st.f_frsize
        used = total - avail
        used_pct = round((used / total) * 100, 1) if total > 0 else 0.0
        return {
            "percent": used_pct,
            "total_gb": round(total / (1024 ** 3), 1),
            "used_gb": round(used / (1024 ** 3), 1)
        }
    except Exception:
        return {"percent": 0.0, "total_gb": 0, "used_gb": 0}


def get_service_status(service_name):
    try:
        res = subprocess.run(
            ["systemctl", "is-active", service_name],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3
        )
        return res.stdout.strip() == "active"
    except Exception:
        return False


def get_ssl_status(domain):
    cert_path = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
    if not os.path.exists(cert_path):
        return {"valid": False, "days_left": 0, "expires": "Сертификат не найден"}
    try:
        res = subprocess.run(
            ["openssl", "x509", "-enddate", "-noout", "-in", cert_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=3
        )
        end_str = res.stdout.strip().replace("notAfter=", "")
        from email.utils import parsedate_to_datetime
        import datetime
        end_date = parsedate_to_datetime(end_str)
        now = datetime.datetime.now(datetime.timezone.utc)
        days = (end_date - now).days
        return {
            "valid": days > 0,
            "days_left": max(0, days),
            "expires": end_date.strftime("%d.%m.%Y %H:%M UTC")
        }
    except Exception as e:
        return {"valid": False, "days_left": 0, "expires": str(e)}


# ==============================================================================
# [2] CONFIGURATION LOADER & SYNC ENGINE
# ==============================================================================

def load_master_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return json.load(f)
        except Exception:
            pass
    # Fallback default configuration
    return {
        "domain": "example.com",
        "mgmt_port": 54892,
        "mgmt_secret_path": "cfg-admin",
        "auth_user": "admin",
        "auth_pass_hash": "",
        "auto_lock_minutes": 30,
        "server_ip": "127.0.0.1",
        "services": {}
    }


def save_master_config(cfg):
    os.makedirs(os.path.dirname(CONFIG_FILE), exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


def detect_live_services(cfg):
    domain = cfg.get("domain", "example.com")
    services = {}

    # 1. 3X-UI SQLite Database
    if os.path.exists(XUI_DB_PATH):
        try:
            conn = sqlite3.connect(XUI_DB_PATH)
            cur = conn.cursor()
            cur.execute("SELECT key, value FROM settings WHERE key IN ('webPort', 'webBasePath', 'subPort', 'subPath', 'subJsonPath', 'webDomain')")
            settings = dict(cur.fetchall())

            services["xui_panel"] = {
                "id": "xui_panel",
                "name": "Панель управления 3X-UI",
                "protocol": "WEB",
                "current_port": settings.get("webPort", "2053"),
                "current_path": settings.get("webBasePath", "/xui/"),
                "has_path": True,
                "has_port": True,
                "badge": "3X-UI"
            }
            services["sub_base64"] = {
                "id": "sub_base64",
                "name": "Подписка Base64 (V2Ray / Clash)",
                "protocol": "HTTP",
                "current_port": settings.get("subPort", "2096"),
                "current_path": settings.get("subPath", "/sub/"),
                "has_path": True,
                "has_port": True,
                "badge": "Base64"
            }
            services["sub_json"] = {
                "id": "sub_json",
                "name": "Подписка JSON (Happ / Sing-box)",
                "protocol": "HTTP",
                "current_port": settings.get("subPort", "2096"),
                "current_path": settings.get("subJsonPath", "/sub-json/"),
                "has_path": True,
                "has_port": False,
                "badge": "Sing-box"
            }

            # Inbounds
            cur.execute("SELECT id, remark, port, protocol, settings, stream_settings, tag FROM inbounds")
            inbounds = cur.fetchall()
            for ib in inbounds:
                remark = ib[1]
                port = str(ib[2])
                protocol = ib[3]
                try:
                    stream = json.loads(ib[5])
                except Exception:
                    stream = {}

                net = stream.get("network", "")
                if "Reality" in remark or (protocol == "vless" and stream.get("security") == "reality"):
                    continue
                elif "WS" in remark or net == "ws":
                    ws_path = stream.get("wsSettings", {}).get("path", "/vless-ws")
                    services["vless_ws"] = {
                        "id": "vless_ws",
                        "name": "VLESS WebSocket",
                        "protocol": "WS",
                        "current_port": port,
                        "current_path": ws_path,
                        "has_path": True,
                        "has_port": True,
                        "badge": "VLESS-WS"
                    }
                elif "XHTTP" in remark or net == "xhttp":
                    xhttp_path = stream.get("xhttpSettings", {}).get("path", "/vless-xhttp")
                    services["vless_xhttp"] = {
                        "id": "vless_xhttp",
                        "name": "VLESS XHTTP (UDS)",
                        "protocol": "XHTTP",
                        "current_port": "0 (UDS Socket)",
                        "current_path": xhttp_path,
                        "has_path": True,
                        "has_port": False,
                        "badge": "XHTTP"
                    }
                elif "Trojan" in remark or protocol == "trojan":
                    svc_name = stream.get("grpcSettings", {}).get("serviceName", "trojan-grpc")
                    services["trojan_grpc"] = {
                        "id": "trojan_grpc",
                        "name": "Trojan gRPC",
                        "protocol": "gRPC",
                        "current_port": port,
                        "current_path": svc_name,
                        "has_path": True,
                        "has_port": True,
                        "badge": "Trojan"
                    }
                elif "gRPC" in remark or (protocol == "vless" and net == "grpc"):
                    svc_name = stream.get("grpcSettings", {}).get("serviceName", "vless-grpc")
                    services["vless_grpc"] = {
                        "id": "vless_grpc",
                        "name": "VLESS gRPC",
                        "protocol": "gRPC",
                        "current_port": port,
                        "current_path": svc_name,
                        "has_path": True,
                        "has_port": True,
                        "badge": "VLESS-gRPC"
                    }
                elif "Hysteria" in remark or protocol == "hysteria":
                    services["hysteria2"] = {
                        "id": "hysteria2",
                        "name": "Hysteria 2 (UDP 443)",
                        "protocol": "UDP",
                        "current_port": port if port != "0" else "443",
                        "current_path": "-",
                        "has_path": False,
                        "has_port": True,
                        "badge": "Hysteria2"
                    }
                elif "Amnezia" in remark or protocol == "amneziawg":
                    services["amneziawg"] = {
                        "id": "amneziawg",
                        "name": "AmneziaWG (UDP 8443)",
                        "protocol": "UDP",
                        "current_port": port if port != "0" else "8443",
                        "current_path": "-",
                        "has_path": False,
                        "has_port": True,
                        "badge": "AmneziaWG"
                    }

            # Check for any created clients in inbounds
            client_sub_id = None
            for ib in inbounds:
                try:
                    ib_settings = json.loads(ib[4]) if ib[4] else {}
                    for c in ib_settings.get("clients", []):
                        sid = c.get("subId") or c.get("id")
                        if sid:
                            client_sub_id = sid
                            break
                    if client_sub_id:
                        break
                except Exception:
                    pass

            if client_sub_id:
                if "sub_base64" in services:
                    services["sub_base64"]["client_sub_id"] = client_sub_id
                if "sub_json" in services:
                    services["sub_json"]["client_sub_id"] = client_sub_id

            conn.close()
        except Exception as e:
            print(f"[WARN] Error reading x-ui db: {e}")

    # 2. Telemt config
    if os.path.exists(TELEMT_CONF_PATH):
        try:
            with open(TELEMT_CONF_PATH, "r") as f:
                tc = f.read()
            tg_port_m = re.search(r'\[\[server\.listeners\]\][\s\S]*?port\s*=\s*(\d+)', tc)
            tg_path_m = re.search(r'\[\[web\.vhosts\]\][\s\S]*?base_path\s*=\s*"([^"]+)"', tc)
            services["telemt"] = {
                "id": "telemt",
                "name": "Telemt (Telegram Web Proxy)",
                "protocol": "TG",
                "current_port": tg_port_m.group(1) if tg_port_m else "18080",
                "current_path": ("/" + tg_path_m.group(1)) if tg_path_m else "/tg",
                "has_path": True,
                "has_port": True,
                "badge": "Telemt"
            }
        except Exception as e:
            print(f"[WARN] Error reading telemt config: {e}")

    # 3. Telemt Panel
    telemt_panel_conf = "/etc/nginx/sites-available/telemt-panel.conf"
    if os.path.exists(telemt_panel_conf):
        try:
            with open(telemt_panel_conf, "r") as f:
                pc = f.read()
            p_port_m = re.search(r'listen\s+(\d+)\s+ssl', pc)
            services["telemt_panel"] = {
                "id": "telemt_panel",
                "name": "Панель управления Telemt (HTTPS)",
                "protocol": "WEB",
                "current_port": p_port_m.group(1) if p_port_m else "65001",
                "current_path": "/",
                "has_path": False,
                "has_port": True,
                "badge": "TG-Panel"
            }
        except Exception:
            pass

    # 4. AdGuard Home config
    if os.path.exists(ADGUARD_CONF_PATH):
        try:
            with open(ADGUARD_CONF_PATH, "r") as f:
                ac = f.read()
            bind_port_m = re.search(r'bind_port:\s*(\d+)', ac)
            doh_port_m = re.search(r'tls:[\s\S]*?port:\s*(\d+)', ac)

            bind_port_m = re.search(r'address:\s*(?:[0-9.]+:)?(\d+)', ac) or re.search(r'bind_port:\s*(\d+)', ac)
            web_port = bind_port_m.group(1) if bind_port_m else "3000"

            # DoH path & port from Nginx vhost
            nginx_vhost = f"/etc/nginx/sites-available/{domain}.conf"
            if not os.path.exists(nginx_vhost) and os.path.exists(f"/etc/nginx/sites-available/{domain}"):
                nginx_vhost = f"/etc/nginx/sites-available/{domain}"
            doh_path = "/dns-query"
            doh_port = web_port
            if os.path.exists(nginx_vhost):
                with open(nginx_vhost, "r") as f:
                    nc = f.read()
                doh_path_m = re.search(r'# === \[8\] ADGUARD HOME DoH[\s\S]*?location\s*=\s*([^ \t\n{]+)', nc)
                if not doh_path_m:
                    doh_path_m = re.search(r'location\s*~\s*"[\^/]+([^/"]+)/\(\?<doh_client_id>', nc)
                    if doh_path_m:
                        doh_path = "/" + doh_path_m.group(1).lstrip("/")
                else:
                    doh_path = doh_path_m.group(1)

                port_m = re.search(r'proxy_pass\s+http://127\.0\.0\.1:(\d+)/dns-query', nc)
                if port_m:
                    doh_port = port_m.group(1)

            services["adguard_doh"] = {
                "id": "adguard_doh",
                "name": "AdGuard Home DoH (DNS-over-HTTPS)",
                "protocol": "DoH",
                "current_port": doh_port,
                "current_path": doh_path,
                "has_path": True,
                "has_port": False,
                "badge": "AdGuard-DoH"
            }
            services["adguard_web"] = {
                "id": "adguard_web",
                "name": "AdGuard Home Web UI",
                "protocol": "WEB",
                "current_port": bind_port_m.group(1) if bind_port_m else "3000",
                "current_path": "/",
                "has_path": False,
                "has_port": True,
                "badge": "AdGuard-Web"
            }
        except Exception as e:
            print(f"[WARN] Error reading AdGuard config: {e}")

    return services


# ==============================================================================
# [3] ATOMIC CONFIG UPDATER WITH ROLLBACK
# ==============================================================================

def normalize_path(path, style="with_slash"):
    """
    Normalizes path according to required target style:
    - 'with_slash': /path/ (e.g. 3x-ui, subscriptions)
    - 'leading_only': /path (e.g. websocket, xhttp, DoH, Telemt location)
    - 'no_slash': path (e.g. gRPC serviceName, Telemt config.toml base_path)
    """
    if not path:
        return ""
    clean = "/" + str(path).strip().strip("/")
    if style == "with_slash":
        return clean + "/" if not clean.endswith("/") else clean
    elif style == "leading_only":
        return clean
    elif style == "no_slash":
        return clean.strip("/")
    return clean


def is_port_in_use(port, exclude_current=None):
    try:
        port_int = int(port)
        if exclude_current and port_int == int(exclude_current):
            return False
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.5)
            return s.connect_ex(("127.0.0.1", port_int)) == 0
    except Exception:
        return False


def apply_service_changes(service_id, new_port, new_path, cfg):
    domain = cfg.get("domain", "example.com")
    backup_dir = f"/tmp/server_mgr_backup_{int(time.time())}"
    os.makedirs(backup_dir, exist_ok=True)

    nginx_vhost = f"/etc/nginx/sites-available/{domain}.conf"
    if not os.path.exists(nginx_vhost) and os.path.exists(f"/etc/nginx/sites-available/{domain}"):
        nginx_vhost = f"/etc/nginx/sites-available/{domain}"

    # Backup all configuration files before touching anything
    for src in [nginx_vhost, XUI_DB_PATH, TELEMT_CONF_PATH, ADGUARD_CONF_PATH, "/etc/nginx/sites-available/telemt-panel.conf"]:
        if os.path.exists(src):
            shutil.copy2(src, os.path.join(backup_dir, os.path.basename(src)))

    try:
        current_services = detect_live_services(cfg)
        target_svc = current_services.get(service_id, {})
        cur_port = str(target_svc.get("current_port", ""))
        cur_path = str(target_svc.get("current_path", ""))

        # 1. 3X-UI PANEL
        if service_id == "xui_panel":
            norm_path = normalize_path(new_path, "with_slash") if new_path else cur_path
            if os.path.exists(XUI_DB_PATH):
                conn = sqlite3.connect(XUI_DB_PATH)
                cur = conn.cursor()
                if new_port:
                    cur.execute("UPDATE settings SET value = ? WHERE key = 'webPort'", (str(new_port),))
                if new_path:
                    cur.execute("UPDATE settings SET value = ? WHERE key = 'webBasePath'", (norm_path,))
                conn.commit()
                conn.close()

            if os.path.exists(nginx_vhost):
                with open(nginx_vhost, "r") as f:
                    content = f.read()
                if new_path and cur_path:
                    content = content.replace(f"location = {cur_path.rstrip('/')}", f"location = {norm_path.rstrip('/')}")
                    content = content.replace(f"return 301 {cur_path};", f"return 301 {norm_path};")
                    content = content.replace(f"location ^~ {cur_path}", f"location ^~ {norm_path}")
                if new_port and cur_port:
                    content = re.sub(r'(# === \[1\] 3X-UI WEB PANEL ===[\s\S]*?proxy_pass\s+http://127\.0\.0\.1:)\d+', f'\\g<1>{new_port}', content)
                with open(nginx_vhost, "w") as f:
                    f.write(content)

        # 2. SUB BASE64
        elif service_id == "sub_base64":
            norm_path = normalize_path(new_path, "with_slash") if new_path else cur_path
            if os.path.exists(XUI_DB_PATH):
                conn = sqlite3.connect(XUI_DB_PATH)
                cur = conn.cursor()
                if new_port:
                    cur.execute("UPDATE settings SET value = ? WHERE key = 'subPort'", (str(new_port),))
                if new_path:
                    cur.execute("UPDATE settings SET value = ? WHERE key = 'subPath'", (norm_path,))
                    cur.execute("UPDATE settings SET value = ? WHERE key = 'subURI'", (f"https://{domain}{norm_path}",))
                conn.commit()
                conn.close()

            if os.path.exists(nginx_vhost):
                with open(nginx_vhost, "r") as f:
                    content = f.read()
                if new_path and cur_path:
                    content = content.replace(f"location ^~ {cur_path}", f"location ^~ {norm_path}")
                if new_port:
                    def _sub_repl(m):
                        return re.sub(r'(proxy_pass\s+https?://127\.0\.0\.1:)\d+', f'\\g<1>{new_port}', m.group(0))
                    content = re.sub(r'# === \[2\] SUBSCRIPTIONS[\s\S]*?(?=# === \[3\])', _sub_repl, content, count=1)
                with open(nginx_vhost, "w") as f:
                    f.write(content)

        # 3. SUB JSON (HAPP / SING-BOX)
        elif service_id == "sub_json":
            norm_path = normalize_path(new_path, "with_slash") if new_path else cur_path
            if os.path.exists(XUI_DB_PATH):
                conn = sqlite3.connect(XUI_DB_PATH)
                cur = conn.cursor()
                cur.execute("UPDATE settings SET value = ? WHERE key = 'subJsonPath'", (norm_path,))
                cur.execute("UPDATE settings SET value = ? WHERE key = 'subJsonURI'", (f"https://{domain}{norm_path}",))
                conn.commit()
                conn.close()

            if os.path.exists(nginx_vhost):
                with open(nginx_vhost, "r") as f:
                    content = f.read()
                if new_path and cur_path:
                    content = content.replace(f"location ^~ {cur_path}", f"location ^~ {norm_path}")
                with open(nginx_vhost, "w") as f:
                    f.write(content)

        # 4. TELEMT TELEGRAM WEB PROXY
        elif service_id == "telemt":
            norm_path_leading = normalize_path(new_path, "leading_only") if new_path else cur_path
            norm_path_no_slash = normalize_path(new_path, "no_slash") if new_path else cur_path.lstrip("/")

            if os.path.exists(TELEMT_CONF_PATH):
                with open(TELEMT_CONF_PATH, "r") as f:
                    tc = f.read()
                if new_path:
                    tc = re.sub(r'(base_path\s*=\s*")[^"]+(")', f'\\g<1>{norm_path_no_slash}\\g<2>', tc)
                if new_port:
                    tc = re.sub(r'(\[\[server\.listeners\]\][\s\S]*?port\s*=\s*)\d+', f'\\g<1>{new_port}', tc)
                with open(TELEMT_CONF_PATH, "w") as f:
                    f.write(tc)

            if os.path.exists(nginx_vhost):
                with open(nginx_vhost, "r") as f:
                    content = f.read()
                if new_path and cur_path:
                    content = content.replace(f"location ^~ {cur_path}", f"location ^~ {norm_path_leading}")
                if new_port:
                    content = re.sub(r'(upstream\s+telemt_web\s*\{[\s\S]*?server\s+127\.0\.0\.1:)\d+', f'\\g<1>{new_port}', content)
                with open(nginx_vhost, "w") as f:
                    f.write(content)

        # 5. ADGUARD HOME DOH
        elif service_id == "adguard_doh":
            norm_path_leading = normalize_path(new_path, "leading_only") if new_path else cur_path
            if os.path.exists(nginx_vhost):
                with open(nginx_vhost, "r") as f:
                    content = f.read()
                if new_path and cur_path:
                    content = content.replace(f"location = {cur_path}", f"location = {norm_path_leading}")
                    content = content.replace(f'location ~ "^{cur_path}/', f'location ~ "^{norm_path_leading}/')
                with open(nginx_vhost, "w") as f:
                    f.write(content)

        # 6. VLESS WEBSOCKET
        elif service_id == "vless_ws":
            norm_path_leading = normalize_path(new_path, "leading_only") if new_path else cur_path
            if os.path.exists(XUI_DB_PATH):
                conn = sqlite3.connect(XUI_DB_PATH)
                cur = conn.cursor()
                cur.execute("SELECT id, stream_settings, port FROM inbounds WHERE remark LIKE '%WS%' OR stream_settings LIKE '%\"network\": \"ws\"%'")
                row = cur.fetchone()
                if row:
                    ib_id, ss_str, ib_port = row
                    stream = json.loads(ss_str)
                    if new_path:
                        stream.setdefault("wsSettings", {})["path"] = norm_path_leading
                    if new_port:
                        cur.execute("UPDATE inbounds SET port = ?, tag = ? WHERE id = ?", (str(new_port), f"inbound-{new_port}", ib_id))
                    cur.execute("UPDATE inbounds SET stream_settings = ? WHERE id = ?", (json.dumps(stream), ib_id))
                    conn.commit()
                conn.close()

            if os.path.exists(nginx_vhost):
                with open(nginx_vhost, "r") as f:
                    content = f.read()
                if new_path and cur_path:
                    content = content.replace(f"location = {cur_path}", f"location = {norm_path_leading}")
                if new_port:
                    target_p = norm_path_leading if new_path else cur_path
                    content = re.sub(r'(location\s*=\s*' + re.escape(target_p) + r'[\s\S]*?proxy_pass\s+http://127\.0\.0\.1:)\d+', f'\\g<1>{new_port}', content)
                with open(nginx_vhost, "w") as f:
                    f.write(content)

        # 7. VLESS XHTTP
        elif service_id == "vless_xhttp":
            norm_path_leading = normalize_path(new_path, "leading_only") if new_path else cur_path
            if os.path.exists(XUI_DB_PATH):
                conn = sqlite3.connect(XUI_DB_PATH)
                cur = conn.cursor()
                cur.execute("SELECT id, stream_settings FROM inbounds WHERE remark LIKE '%XHTTP%' OR stream_settings LIKE '%\"network\": \"xhttp\"%'")
                row = cur.fetchone()
                if row:
                    ib_id, ss_str = row
                    stream = json.loads(ss_str)
                    stream.setdefault("xhttpSettings", {})["path"] = norm_path_leading
                    cur.execute("UPDATE inbounds SET stream_settings = ? WHERE id = ?", (json.dumps(stream), ib_id))
                    conn.commit()
                conn.close()

            if os.path.exists(nginx_vhost):
                with open(nginx_vhost, "r") as f:
                    content = f.read()
                if new_path and cur_path:
                    content = content.replace(f"location ^~ {cur_path}", f"location ^~ {norm_path_leading}")
                with open(nginx_vhost, "w") as f:
                    f.write(content)

        # 8. TROJAN GRPC
        elif service_id == "trojan_grpc":
            norm_path_no_slash = normalize_path(new_path, "no_slash") if new_path else cur_path.lstrip("/")
            cur_clean = cur_path.lstrip("/")
            if os.path.exists(XUI_DB_PATH):
                conn = sqlite3.connect(XUI_DB_PATH)
                cur = conn.cursor()
                cur.execute("SELECT id, stream_settings FROM inbounds WHERE remark LIKE '%Trojan%' OR protocol = 'trojan'")
                row = cur.fetchone()
                if row:
                    ib_id, ss_str = row
                    stream = json.loads(ss_str)
                    if new_path:
                        stream.setdefault("grpcSettings", {})["serviceName"] = norm_path_no_slash
                    if new_port:
                        cur.execute("UPDATE inbounds SET port = ?, tag = ? WHERE id = ?", (str(new_port), f"inbound-{new_port}", ib_id))
                    cur.execute("UPDATE inbounds SET stream_settings = ? WHERE id = ?", (json.dumps(stream), ib_id))
                    conn.commit()
                conn.close()

            if os.path.exists(nginx_vhost):
                with open(nginx_vhost, "r") as f:
                    content = f.read()
                if new_path and cur_path:
                    content = re.sub(r'(location\s+\^\~\s+)/?' + re.escape(cur_clean) + r'(\s*\{)', f'\\g<1>/{norm_path_no_slash}\\2', content)
                if new_port:
                    target_sn = norm_path_no_slash if new_path else cur_clean
                    content = re.sub(r'(location\s+\^\~\s+/?' + re.escape(target_sn) + r'[\s\S]*?grpc_pass\s+grpc://127\.0\.0\.1:)\d+', f'\\g<1>{new_port}', content)
                with open(nginx_vhost, "w") as f:
                    f.write(content)

        # 9. VLESS GRPC
        elif service_id == "vless_grpc":
            norm_path_no_slash = normalize_path(new_path, "no_slash") if new_path else cur_path.lstrip("/")
            cur_clean = cur_path.lstrip("/")
            if os.path.exists(XUI_DB_PATH):
                conn = sqlite3.connect(XUI_DB_PATH)
                cur = conn.cursor()
                cur.execute("SELECT id, stream_settings FROM inbounds WHERE remark LIKE '%VLESS-gRPC%' OR (protocol = 'vless' AND stream_settings LIKE '%\"network\": \"grpc\"%')")
                row = cur.fetchone()
                if row:
                    ib_id, ss_str = row
                    stream = json.loads(ss_str)
                    if new_path:
                        stream.setdefault("grpcSettings", {})["serviceName"] = norm_path_no_slash
                    if new_port:
                        cur.execute("UPDATE inbounds SET port = ?, tag = ? WHERE id = ?", (str(new_port), f"inbound-{new_port}", ib_id))
                    cur.execute("UPDATE inbounds SET stream_settings = ? WHERE id = ?", (json.dumps(stream), ib_id))
                    conn.commit()
                conn.close()

            if os.path.exists(nginx_vhost):
                with open(nginx_vhost, "r") as f:
                    content = f.read()
                if new_path and cur_path:
                    content = re.sub(r'(location\s+\^\~\s+)/?' + re.escape(cur_clean) + r'(\s*\{)', f'\\g<1>/{norm_path_no_slash}\\2', content)
                if new_port:
                    target_sn = norm_path_no_slash if new_path else cur_clean
                    content = re.sub(r'(location\s+\^\~\s+/?' + re.escape(target_sn) + r'[\s\S]*?grpc_pass\s+grpc://127\.0\.0\.1:)\d+', f'\\g<1>{new_port}', content)
                with open(nginx_vhost, "w") as f:
                    f.write(content)

        # 10. TELEMT PANEL
        elif service_id == "telemt_panel" and new_port:
            tp_conf = "/etc/nginx/sites-available/telemt-panel.conf"
            if os.path.exists(tp_conf):
                with open(tp_conf, "r") as f:
                    content = f.read()
                content = re.sub(r'(listen\s+)\d+(\s+ssl)', f'\\g<1>{new_port}\\g<2>', content)
                with open(tp_conf, "w") as f:
                    f.write(content)

        # TEST NGINX CONFIGURATION INTEGRITY
        t = subprocess.run(["nginx", "-t"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        if t.returncode != 0:
            raise Exception(f"Nginx validation failed:\n{t.stderr}")

        # Reload affected services
        subprocess.run(["systemctl", "reload", "nginx"], check=False)
        if service_id in ["xui_panel", "sub_base64", "sub_json", "vless_ws", "vless_xhttp", "trojan_grpc", "vless_grpc"]:
            subprocess.run(["systemctl", "reset-failed", "x-ui"], check=False)
            subprocess.run(["systemctl", "restart", "x-ui"], check=False)
        elif service_id == "telemt":
            subprocess.run(["systemctl", "reset-failed", "telemt"], check=False)
            subprocess.run(["systemctl", "restart", "telemt"], check=False)
        # Note: adguard_doh changes only require nginx reload (handled above) since AdGuardHome backend is untouched

        # Cleanup backup
        shutil.rmtree(backup_dir, ignore_errors=True)
        return True, "Параметры успешно обновлены во всех компонентах!"

    except Exception as e:
        # ATOMIC ROLLBACK ON ERROR
        print(f"[ERROR] Rollback triggered: {e}")
        for f_name in os.listdir(backup_dir):
            dst = os.path.join(backup_dir, f_name)
            if f_name.endswith(".conf"):
                shutil.copy2(dst, f"/etc/nginx/sites-available/{f_name}")
            elif f_name == "x-ui.db":
                shutil.copy2(dst, XUI_DB_PATH)
            elif f_name == "config.toml":
                shutil.copy2(dst, TELEMT_CONF_PATH)
            elif f_name == "AdGuardHome.yaml":
                shutil.copy2(dst, ADGUARD_CONF_PATH)
        subprocess.run(["systemctl", "reload", "nginx"], check=False)
        shutil.rmtree(backup_dir, ignore_errors=True)
        return False, f"Ошибка при применении (выполнен авто-откат): {str(e)}"


def lock_firewall():
    """
    Strict lockdown: ensures only ports 22 (SSH) and 443 (TCP/UDP)
    are allowed in UFW.
    """
    try:
        if os.path.exists(AUTO_LOCK_FILE):
            try:
                os.remove(AUTO_LOCK_FILE)
            except OSError:
                pass

        # Detect SSH port
        res = subprocess.run(
            ["ss", "-tlnp"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        ssh_port = "22"
        for line in res.stdout.splitlines():
            if "sshd" in line:
                m = re.search(r':(\d+)\s+', line)
                if m:
                    ssh_port = m.group(1)
                    break

        cfg = load_master_config()
        mgmt_port = str(cfg.get("mgmt_port", "54892"))

        # Delete management port rule
        subprocess.run(["ufw", "delete", "allow", f"{mgmt_port}/tcp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["ufw", "delete", "allow", mgmt_port], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Ensure default deny
        subprocess.run(["ufw", "default", "deny", "incoming"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["ufw", "default", "allow", "outgoing"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Allow core whitelist
        subprocess.run(["ufw", "allow", f"{ssh_port}/tcp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["ufw", "allow", "443/tcp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["ufw", "allow", "443/udp"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        subprocess.run(["ufw", "reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        print("[FIREWALL] Locked down. Only ports 22, 443 are allowed.")
        return True
    except Exception as e:
        print(f"[FIREWALL ERROR] {e}")
        return False


def unlock_firewall(minutes=30):
    try:
        cfg = load_master_config()
        mgmt_port = str(cfg.get("mgmt_port", "54892"))
        subprocess.run(["ufw", "allow", f"{mgmt_port}/tcp", "comment", "Server Manager"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["ufw", "reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        expiry = time.time() + (int(minutes) * 60)
        os.makedirs(os.path.dirname(AUTO_LOCK_FILE), exist_ok=True)
        with open(AUTO_LOCK_FILE, "w") as f:
            f.write(str(expiry))
        print(f"[FIREWALL] Port {mgmt_port} opened for {minutes} minutes.")
        return True
    except Exception as e:
        print(f"[FIREWALL ERROR] {e}")
        return False


def firewall_monitor_thread():
    while True:
        try:
            if os.path.exists(AUTO_LOCK_FILE):
                try:
                    with open(AUTO_LOCK_FILE, "r") as f:
                        content = f.read().strip()
                    if content:
                        expiry = float(content)
                        if time.time() >= expiry:
                            print("[FIREWALL] Auto-lock timer expired. Locking down firewall...")
                            lock_firewall()
                except Exception:
                    pass
        except Exception as e:
            print(f"[FIREWALL MONITOR ERROR] {e}")
        time.sleep(5)


# [5] EMBEDDED APPLE HIG WEB FRONTEND (HTML / CSS / JS)
# ==============================================================================

HTML_PAGE = r"""<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <title>Конфигурирование сервера</title>
  <style>
    :root {
      --bg-color: #000000;
      --card-bg: rgba(28, 28, 30, 0.72);
      --card-border: rgba(255, 255, 255, 0.08);
      --input-bg: rgba(44, 44, 46, 0.75);
      --text-main: #FFFFFF;
      --text-secondary: #8E8E93;
      --accent-blue: #007AFF;
      --accent-blue-hover: #0A84FF;
      --accent-green: #34C759;
      --accent-orange: #FF9500;
      --accent-red: #FF3B30;
      --accent-purple: #AF52DE;
      --radius-large: 20px;
      --radius-medium: 14px;
      --radius-small: 10px;
      --blur-strength: blur(28px) saturate(190%);
    }

    * {
      box-sizing: border-box;
      margin: 0;
      padding: 0;
      -webkit-font-smoothing: antialiased;
      -moz-osx-font-smoothing: grayscale;
    }

    body {
      background-color: var(--bg-color);
      color: var(--text-main);
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Helvetica Neue", sans-serif;
      min-height: 100vh;
      padding-top: max(20px, env(safe-area-inset-top));
      padding-bottom: max(30px, env(safe-area-inset-bottom));
      padding-left: max(16px, env(safe-area-inset-left));
      padding-right: max(16px, env(safe-area-inset-right));
      background-image:
        radial-gradient(at 0% 0%, rgba(0, 122, 255, 0.12) 0px, transparent 50%),
        radial-gradient(at 100% 100%, rgba(175, 82, 222, 0.1) 0px, transparent 50%);
      background-attachment: fixed;
    }

    .container {
      max-width: 1080px;
      margin: 0 auto;
    }

    /* === TOP NAVIGATION BAR === */
    .nav-bar {
      position: sticky;
      top: max(10px, env(safe-area-inset-top));
      z-index: 100;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      padding: 14px 20px;
      background: var(--card-bg);
      backdrop-filter: var(--blur-strength);
      -webkit-backdrop-filter: var(--blur-strength);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-large);
      margin-bottom: 24px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4);
    }

    .brand-title {
      font-size: 19px;
      font-weight: 700;
      letter-spacing: -0.4px;
      display: flex;
      align-items: center;
      gap: 8px;
    }

    .brand-title span {
      background: linear-gradient(135deg, #FFF 30%, #A1A1A6 100%);
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
    }

    /* Resource usage widget (CPU, RAM, SSD) */
    .metrics-group {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .metric-pill {
      display: flex;
      align-items: center;
      gap: 7px;
      padding: 6px 12px;
      background: rgba(255, 255, 255, 0.05);
      border: 1px solid rgba(255, 255, 255, 0.06);
      border-radius: 30px;
      font-size: 13px;
      font-weight: 600;
    }

    .metric-label {
      color: var(--text-secondary);
      font-size: 11px;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }

    .metric-bar-wrap {
      width: 40px;
      height: 5px;
      background: rgba(255, 255, 255, 0.1);
      border-radius: 4px;
      overflow: hidden;
    }

    .metric-bar-fill {
      height: 100%;
      border-radius: 4px;
      transition: width 0.4s ease;
      background: var(--accent-blue);
    }

    .btn-settings {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 8px 16px;
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid var(--card-border);
      color: var(--text-main);
      border-radius: 30px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s cubic-bezier(0.25, 1, 0.5, 1);
    }

    .btn-settings:hover {
      background: rgba(255, 255, 255, 0.16);
      border-color: rgba(255, 255, 255, 0.24);
      transform: scale(1.02);
    }

    .btn-settings:active {
      transform: scale(0.97);
    }

    .btn-logout {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 8px 16px;
      background: rgba(255, 59, 48, 0.15);
      border: 1px solid rgba(255, 59, 48, 0.3);
      color: var(--accent-red);
      border-radius: 30px;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s cubic-bezier(0.25, 1, 0.5, 1);
    }

    .btn-logout:hover {
      background: rgba(255, 59, 48, 0.25);
      transform: scale(1.02);
    }

    .btn-logout:active {
      transform: scale(0.97);
    }

    /* === STACK STATUS SECTION === */
    .section-title {
      font-size: 14px;
      font-weight: 600;
      text-transform: uppercase;
      letter-spacing: 0.6px;
      color: var(--text-secondary);
      margin: 24px 0 12px 6px;
    }

    .status-panel {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      gap: 12px;
      margin-bottom: 24px;
    }

    .status-card {
      background: var(--card-bg);
      backdrop-filter: var(--blur-strength);
      -webkit-backdrop-filter: var(--blur-strength);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-medium);
      padding: 16px;
      display: flex;
      align-items: center;
      justify-content: space-between;
    }

    .status-left {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .status-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--text-secondary);
      box-shadow: 0 0 10px rgba(255, 255, 255, 0.2);
    }

    .status-dot.active {
      background: var(--accent-green);
      box-shadow: 0 0 10px rgba(52, 199, 89, 0.6);
      animation: pulse 2.5s infinite;
    }

    .status-dot.inactive {
      background: var(--accent-red);
      box-shadow: 0 0 10px rgba(255, 59, 48, 0.6);
    }

    @keyframes pulse {
      0%, 100% { opacity: 1; transform: scale(1); }
      50% { opacity: 0.7; transform: scale(1.15); }
    }

    @keyframes spin {
      from { transform: rotate(0deg); }
      to { transform: rotate(360deg); }
    }

    .status-name {
      font-size: 15px;
      font-weight: 600;
    }

    .status-state {
      font-size: 12px;
      font-weight: 500;
      color: var(--text-secondary);
    }

    .stack-actions {
      display: flex;
      gap: 12px;
      margin-bottom: 28px;
      flex-wrap: wrap;
    }

    .btn-action {
      display: flex;
      align-items: center;
      gap: 8px;
      padding: 12px 20px;
      background: var(--card-bg);
      backdrop-filter: var(--blur-strength);
      -webkit-backdrop-filter: var(--blur-strength);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-medium);
      color: var(--text-main);
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      transition: all 0.2s cubic-bezier(0.25, 1, 0.5, 1);
    }

    .btn-action:hover {
      background: rgba(255, 255, 255, 0.1);
      border-color: rgba(255, 255, 255, 0.2);
      transform: translateY(-1px);
    }

    .btn-action:active {
      transform: scale(0.98);
    }

    /* === SERVICES & INBOUNDS GRID === */
    .services-grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
      gap: 16px;
      margin-bottom: 34px;
    }

    .service-card {
      background: var(--card-bg);
      backdrop-filter: var(--blur-strength);
      -webkit-backdrop-filter: var(--blur-strength);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-large);
      padding: 20px;
      display: flex;
      flex-direction: column;
      justify-content: space-between;
      transition: all 0.25s cubic-bezier(0.25, 1, 0.5, 1);
      box-shadow: 0 4px 20px rgba(0, 0, 0, 0.2);
    }

    .service-card:hover {
      border-color: rgba(255, 255, 255, 0.18);
      transform: translateY(-2px);
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.4);
    }

    .card-header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      margin-bottom: 14px;
    }

    .card-title {
      font-size: 16px;
      font-weight: 600;
      letter-spacing: -0.2px;
    }

    .badge {
      display: inline-block;
      padding: 4px 10px;
      border-radius: 20px;
      font-size: 11px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
      background: rgba(0, 122, 255, 0.18);
      color: var(--accent-blue);
      border: 1px solid rgba(0, 122, 255, 0.3);
    }

    .current-props {
      display: flex;
      flex-direction: column;
      gap: 4px;
      background: rgba(0, 0, 0, 0.25);
      border-radius: var(--radius-small);
      padding: 10px 12px;
      margin-bottom: 16px;
      font-size: 13px;
    }

    .prop-row {
      display: flex;
      justify-content: space-between;
      color: var(--text-secondary);
    }

    .prop-val {
      color: #E5E5EA;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      font-weight: 500;
    }

    .inputs-area {
      display: flex;
      flex-direction: column;
      gap: 10px;
      margin-bottom: 16px;
    }

    .input-row {
      display: flex;
      flex-direction: column;
      gap: 4px;
    }

    .input-label {
      font-size: 12px;
      font-weight: 500;
      color: var(--text-secondary);
    }

    .input-field-wrap {
      display: flex;
      gap: 6px;
    }

    .apple-input {
      flex: 1;
      background: var(--input-bg);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: var(--radius-small);
      padding: 10px 12px;
      color: var(--text-main);
      font-size: 14px;
      outline: none;
      transition: all 0.2s ease;
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    }

    .apple-input:focus {
      border-color: var(--accent-blue);
      box-shadow: 0 0 0 3px rgba(0, 122, 255, 0.25);
    }

    .btn-dice {
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.1);
      color: var(--text-main);
      border-radius: var(--radius-small);
      width: 38px;
      display: flex;
      align-items: center;
      justify-content: center;
      cursor: pointer;
      font-size: 16px;
      transition: all 0.2s ease;
    }

    .btn-dice:hover {
      background: rgba(255, 255, 255, 0.16);
      transform: rotate(15deg);
    }

    .btn-apply {
      width: 100%;
      padding: 11px 16px;
      background: var(--accent-blue);
      border: none;
      border-radius: var(--radius-medium);
      color: #FFF;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 8px;
      transition: all 0.2s cubic-bezier(0.25, 1, 0.5, 1);
    }

    .btn-apply:hover {
      background: var(--accent-blue-hover);
      box-shadow: 0 4px 15px rgba(0, 122, 255, 0.4);
    }

    .btn-apply:active {
      transform: scale(0.98);
    }

    /* === LINKS SUMMARY SECTION === */
    .links-card {
      background: var(--card-bg);
      backdrop-filter: var(--blur-strength);
      -webkit-backdrop-filter: var(--blur-strength);
      border: 1px solid var(--card-border);
      border-radius: var(--radius-large);
      padding: 24px;
      margin-bottom: 40px;
    }

    .link-item {
      display: flex;
      align-items: center;
      justify-content: space-between;
      padding: 14px 0;
      border-bottom: 1px solid rgba(255, 255, 255, 0.06);
      gap: 12px;
      flex-wrap: wrap;
    }

    .link-item:last-child {
      border-bottom: none;
    }

    .link-info {
      flex: 1;
      min-width: 240px;
    }

    .link-name {
      font-size: 15px;
      font-weight: 600;
      margin-bottom: 4px;
    }

    .link-url {
      font-size: 13px;
      color: var(--text-secondary);
      font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
      word-break: break-all;
    }

    .link-actions {
      display: flex;
      gap: 8px;
    }

    .btn-copy, .btn-qr {
      padding: 8px 14px;
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: var(--radius-small);
      color: var(--text-main);
      font-size: 13px;
      font-weight: 600;
      cursor: pointer;
      display: flex;
      align-items: center;
      gap: 5px;
      transition: all 0.2s ease;
    }

    .btn-copy:hover, .btn-qr:hover {
      background: rgba(255, 255, 255, 0.16);
      border-color: rgba(255, 255, 255, 0.2);
    }

    .btn-copy.copied {
      background: rgba(52, 199, 89, 0.2);
      border-color: var(--accent-green);
      color: var(--accent-green);
    }

    /* === APPLE MODAL SHEET === */
    .modal-overlay {
      position: fixed;
      top: 0; left: 0; right: 0; bottom: 0;
      background: rgba(0, 0, 0, 0.65);
      backdrop-filter: blur(16px);
      -webkit-backdrop-filter: blur(16px);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 999;
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.25s cubic-bezier(0.16, 1, 0.3, 1);
      padding: 20px;
    }

    .modal-overlay.open {
      opacity: 1;
      pointer-events: auto;
    }

    .modal-card {
      background: rgba(32, 32, 36, 0.96);
      backdrop-filter: blur(36px) saturate(190%);
      -webkit-backdrop-filter: blur(36px) saturate(190%);
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: var(--radius-large);
      max-width: 460px;
      width: 100%;
      padding: 24px;
      box-shadow: 0 24px 60px rgba(0, 0, 0, 0.7), 0 0 0 1px rgba(255, 255, 255, 0.05);
      transform: scale(0.96) translateY(8px);
      transition: transform 0.28s cubic-bezier(0.16, 1, 0.3, 1), opacity 0.28s ease;
    }

    .modal-overlay.open .modal-card {
      transform: scale(1) translateY(0);
    }

    @media (max-width: 600px) {
      .modal-overlay {
        align-items: flex-end;
        padding: 0;
      }
      .modal-card {
        border-radius: 24px 24px 0 0;
        max-width: 100%;
        padding: 24px 20px max(24px, env(safe-area-inset-bottom));
        transform: translateY(100%);
      }
      .modal-overlay.open .modal-card {
        transform: translateY(0);
      }
    }

    .modal-title {
      font-size: 18px;
      font-weight: 700;
      margin-bottom: 8px;
    }

    .modal-body {
      font-size: 14px;
      color: var(--text-secondary);
      line-height: 1.5;
      margin-bottom: 20px;
    }

    .modal-buttons {
      display: flex;
      gap: 10px;
      justify-content: flex-end;
    }

    .btn-cancel {
      min-height: 44px;
      padding: 10px 20px;
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: var(--radius-medium);
      color: var(--text-main);
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      transition: all 0.2s;
    }
    .btn-cancel:hover {
      background: rgba(255, 255, 255, 0.14);
    }
    .btn-cancel:active {
      transform: scale(0.98);
    }

    .btn-confirm {
      min-height: 44px;
      padding: 10px 20px;
      background: var(--accent-blue);
      border: none;
      border-radius: var(--radius-medium);
      color: #FFF;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
      display: inline-flex;
      align-items: center;
      justify-content: center;
      gap: 6px;
      transition: all 0.2s;
    }
    .btn-confirm:hover {
      background: var(--accent-blue-hover);
    }
    .btn-confirm:active {
      transform: scale(0.98);
    }
    .btn-confirm:disabled {
      opacity: 0.6;
      cursor: not-allowed;
    }

    .btn-confirm.danger {
      background: var(--accent-red);
    }

    .btn-icon-close {
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 50%;
      width: 32px;
      height: 32px;
      min-width: 32px;
      display: flex;
      align-items: center;
      justify-content: center;
      color: var(--text-secondary);
      cursor: pointer;
      transition: all 0.2s;
    }
    .btn-icon-close:hover {
      background: rgba(255, 255, 255, 0.16);
      color: #FFF;
    }
    .btn-icon-close:active {
      transform: scale(0.94);
    }

    .btn-eye {
      background: none;
      border: none;
      color: #8E8E93;
      cursor: pointer;
      padding: 8px;
      display: flex;
      align-items: center;
      justify-content: center;
      border-radius: 8px;
      transition: all 0.2s;
    }
    .btn-eye:hover {
      color: #FFF;
      background: rgba(255, 255, 255, 0.08);
    }

    /* Apple Toast */
    .toast {
      position: fixed;
      bottom: max(30px, env(safe-area-inset-bottom));
      left: 50%;
      transform: translateX(-50%) translateY(100px);
      background: rgba(44, 44, 46, 0.95);
      backdrop-filter: blur(20px);
      -webkit-backdrop-filter: blur(20px);
      border: 1px solid rgba(255, 255, 255, 0.15);
      color: #FFF;
      padding: 12px 24px;
      border-radius: 40px;
      font-size: 14px;
      font-weight: 600;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5);
      z-index: 1000;
      opacity: 0;
      transition: all 0.3s cubic-bezier(0.25, 1, 0.5, 1);
    }

    .toast.show {
      transform: translateX(-50%) translateY(0);
      opacity: 1;
    }

    @media (max-width: 768px) {
      .nav-bar {
        flex-direction: column;
        align-items: stretch;
        gap: 12px;
      }
      .nav-top-row {
        display: flex;
        align-items: center;
        justify-content: space-between;
      }
      .metrics-group {
        justify-content: space-between;
        width: 100%;
      }
    }
  </style>
</head>
<body>
  <div class="container">

    <!-- TOP NAVIGATION BAR -->
    <header class="nav-bar">
      <div class="nav-top-row" style="display:flex; align-items:center; justify-content:space-between; gap:12px;">
        <div class="brand-title">
          <span>⚙️ Конфигурирование сервера</span>
        </div>
        <div style="display:flex; align-items:center; gap:8px;">
          <button class="btn-settings" onclick="openSettingsModal()">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 0 1 0 2.83 2 2 0 0 1-2.83 0l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-2 2 2 2 0 0 1-2-2v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 0 1-2.83 0 2 2 0 0 1 0-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1-2-2 2 2 0 0 1 2-2h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 0 1 0-2.83 2 2 0 0 1 2.83 0l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 2-2 2 2 0 0 1 2 2v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 0 1 2.83 0 2 2 0 0 1 0 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 2 2 2 2 0 0 1-2 2h-.09a1.65 1.65 0 0 0-1.51 1z"/></svg>
            Настройки
          </button>
          <button class="btn-logout" onclick="confirmLogout()">
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
            Выход
          </button>
        </div>
      </div>

      <!-- CPU / RAM / SSD RESOURCE WIDGET -->
      <div class="metrics-group">
        <div class="metric-pill">
          <span class="metric-label">CPU</span>
          <span id="metric-cpu-val">0%</span>
          <div class="metric-bar-wrap"><div id="metric-cpu-bar" class="metric-bar-fill" style="width:0%"></div></div>
        </div>

        <div class="metric-pill">
          <span class="metric-label">RAM</span>
          <span id="metric-ram-val">0%</span>
          <div class="metric-bar-wrap"><div id="metric-ram-bar" class="metric-bar-fill" style="width:0%"></div></div>
        </div>

        <div class="metric-pill">
          <span class="metric-label">SSD</span>
          <span id="metric-disk-val">0%</span>
          <div class="metric-bar-wrap"><div id="metric-disk-bar" class="metric-bar-fill" style="width:0%"></div></div>
        </div>
      </div>
    </header>

    <!-- STACK STATUS -->
    <div class="section-title">Состояние системы и служб</div>
    <div class="status-panel">
      <div class="status-card">
        <div class="status-left">
          <div id="dot-nginx" class="status-dot"></div>
          <div>
            <div class="status-name">Nginx Core</div>
            <div id="text-nginx" class="status-state">Проверка...</div>
          </div>
        </div>
      </div>

      <div class="status-card">
        <div class="status-left">
          <div id="dot-xui" class="status-dot"></div>
          <div>
            <div class="status-name">3X-UI & Xray</div>
            <div id="text-xui" class="status-state">Проверка...</div>
          </div>
        </div>
      </div>

      <div class="status-card">
        <div class="status-left">
          <div id="dot-telemt" class="status-dot"></div>
          <div>
            <div class="status-name">Telemt Proxy</div>
            <div id="text-telemt" class="status-state">Проверка...</div>
          </div>
        </div>
      </div>

      <div class="status-card">
        <div class="status-left">
          <div id="dot-adguard" class="status-dot"></div>
          <div>
            <div class="status-name">AdGuard Home</div>
            <div id="text-adguard" class="status-state">Проверка...</div>
          </div>
        </div>
      </div>
    </div>

    <!-- ACTION BUTTONS -->
    <div class="stack-actions">
      <button class="btn-action" onclick="restartStack()">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10"/></svg>
        Перезапустить стек
      </button>

      <button class="btn-action" onclick="checkSSL()">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="11" width="18" height="11" rx="2" ry="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/></svg>
        <span id="ssl-btn-text">Проверить сертификат SSL</span>
      </button>
    </div>

    <!-- SERVICES & INBOUNDS CONFIGURATION -->
    <div class="section-title">Службы, Inbounds и маршрутизация</div>
    <div id="services-grid" class="services-grid">
      <!-- Dynamically generated service cards -->
    </div>

    <!-- ACTIVE LINKS SUMMARY -->
    <div class="section-title">Актуальные ссылки для подключения</div>
    <div class="links-card">
      <div id="links-container">
        <!-- Dynamically generated active links -->
      </div>
    </div>

  </div>

  <!-- CONFIRMATION MODAL -->
  <div id="confirm-modal" class="modal-overlay">
    <div class="modal-card">
      <div id="modal-title" class="modal-title">Подтверждение</div>
      <div id="modal-body" class="modal-body">Вы уверены?</div>
      <div class="modal-buttons">
        <button class="btn-cancel" onclick="closeModal()">Отмена</button>
        <button id="modal-confirm-btn" class="btn-confirm">Подтвердить</button>
      </div>
    </div>
  </div>

  <!-- SETTINGS MODAL -->
  <div id="settings-modal" class="modal-overlay" onclick="handleModalOverlayClick(event, 'settings-modal')">
    <div class="modal-card">
      <div style="display:flex; align-items:flex-start; justify-content:space-between; margin-bottom:12px;">
        <div>
          <div class="modal-title" style="margin-bottom:4px;">⚙️ Учетная запись</div>
          <div style="font-size:13px; color:var(--text-secondary);">Управление логином и паролем доступа к серверу</div>
        </div>
        <button type="button" class="btn-icon-close" onclick="closeSettingsModal()" aria-label="Закрыть">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
        </button>
      </div>

      <div class="modal-body" style="text-align:left; margin-bottom:18px;">
        <!-- Username Section -->
        <div class="input-row">
          <label class="input-label" for="settings-user">Имя пользователя (Логин)</label>
          <input id="settings-user" class="apple-input" style="width:100%" placeholder="Введите логин" autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false" required />
          <div style="font-size:12px; color:#8E8E93; margin-top:4px;">Используется для авторизации в модуле конфигурирования.</div>
        </div>

        <!-- Password Section (Optional) -->
        <div style="margin-top:16px; padding-top:14px; border-top:1px solid rgba(255,255,255,0.08);">
          <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:6px;">
            <span style="font-size:13px; font-weight:600; color:#FFF;">Смена пароля</span>
            <span style="font-size:11px; color:#8E8E93; text-transform:uppercase; letter-spacing:0.4px;">Необязательно</span>
          </div>
          <div style="font-size:12px; color:#8E8E93; margin-bottom:12px;">Оставьте эти поля пустыми, если хотите сохранить текущий пароль.</div>

          <div class="input-row" style="position:relative;">
            <label class="input-label" for="settings-pass">Новый пароль</label>
            <div style="position:relative; display:flex; align-items:center;">
              <input id="settings-pass" type="password" class="apple-input" style="width:100%; padding-right:42px;" placeholder="Минимум 8 символов" autocomplete="new-password" />
              <button type="button" class="btn-eye" onclick="togglePassVisibility('settings-pass', this)" aria-label="Показать пароль" style="position:absolute; right:8px;">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"></path><circle cx="12" cy="12" r="3"></circle></svg>
              </button>
            </div>
          </div>

          <div class="input-row" style="margin-top:12px;">
            <label class="input-label" for="settings-pass2">Повторите новый пароль</label>
            <input id="settings-pass2" type="password" class="apple-input" style="width:100%" placeholder="Повторите новый пароль" autocomplete="new-password" />
          </div>
        </div>

        <div id="settings-err" style="color:var(--accent-red); font-size:13px; margin-top:14px; display:none; padding:8px 12px; background:rgba(255,59,48,0.12); border-radius:8px;"></div>
      </div>

      <div class="modal-buttons">
        <button class="btn-cancel" onclick="closeSettingsModal()">Отмена</button>
        <button id="settings-save-btn" class="btn-confirm" onclick="saveSettings()">Сохранить</button>
      </div>
    </div>
  </div>

  <!-- QR CODE MODAL -->
  <div id="qr-modal" class="modal-overlay">
    <div class="modal-card" style="text-align:center;">
      <div id="qr-title" class="modal-title">QR-код подключения</div>
      <div style="display:flex; justify-content:center; padding:15px;">
        <div id="qr-box" style="background:#FFF; padding:12px; border-radius:12px; display:inline-block;"></div>
      </div>
      <div style="margin-top:15px;">
        <button class="btn-cancel" onclick="closeQRModal()">Закрыть</button>
      </div>
    </div>
  </div>

  <!-- TOAST NOTIFICATION -->
  <div id="toast" class="toast">Готово</div>

  <!-- AUTHENTIC QR CODE GENERATOR (SELF-CONTAINED JS) -->
  <script>
var QRCode;!function(){function a(a){this.mode=c.MODE_8BIT_BYTE,this.data=a,this.parsedData=[];for(var b=[],d=0,e=this.data.length;e>d;d++){var f=this.data.charCodeAt(d);f>65536?(b[0]=240|(1835008&f)>>>18,b[1]=128|(258048&f)>>>12,b[2]=128|(4032&f)>>>6,b[3]=128|63&f):f>2048?(b[0]=224|(61440&f)>>>12,b[1]=128|(4032&f)>>>6,b[2]=128|63&f):f>128?(b[0]=192|(1984&f)>>>6,b[1]=128|63&f):b[0]=f,this.parsedData=this.parsedData.concat(b)}this.parsedData.length!=this.data.length&&(this.parsedData.unshift(191),this.parsedData.unshift(187),this.parsedData.unshift(239))}function b(a,b){this.typeNumber=a,this.errorCorrectLevel=b,this.modules=null,this.moduleCount=0,this.dataCache=null,this.dataList=[]}function i(a,b){if(void 0==a.length)throw new Error(a.length+"/"+b);for(var c=0;c<a.length&&0==a[c];)c++;this.num=new Array(a.length-c+b);for(var d=0;d<a.length-c;d++)this.num[d]=a[d+c]}function j(a,b){this.totalCount=a,this.dataCount=b}function k(){this.buffer=[],this.length=0}function m(){return"undefined"!=typeof CanvasRenderingContext2D}function n(){var a=!1,b=navigator.userAgent;return/android/i.test(b)&&(a=!0,aMat=b.toString().match(/android ([0-9]\.[0-9])/i),aMat&&aMat[1]&&(a=parseFloat(aMat[1]))),a}function r(a,b){for(var c=1,e=s(a),f=0,g=l.length;g>=f;f++){var h=0;switch(b){case d.L:h=l[f][0];break;case d.M:h=l[f][1];break;case d.Q:h=l[f][2];break;case d.H:h=l[f][3]}if(h>=e)break;c++}if(c>l.length)throw new Error("Too long data");return c}function s(a){var b=encodeURI(a).toString().replace(/\%[0-9a-fA-F]{2}/g,"a");return b.length+(b.length!=a?3:0)}a.prototype={getLength:function(){return this.parsedData.length},write:function(a){for(var b=0,c=this.parsedData.length;c>b;b++)a.put(this.parsedData[b],8)}},b.prototype={addData:function(b){var c=new a(b);this.dataList.push(c),this.dataCache=null},isDark:function(a,b){if(0>a||this.moduleCount<=a||0>b||this.moduleCount<=b)throw new Error(a+","+b);return this.modules[a][b]},getModuleCount:function(){return this.moduleCount},make:function(){this.makeImpl(!1,this.getBestMaskPattern())},makeImpl:function(a,c){this.moduleCount=4*this.typeNumber+17,this.modules=new Array(this.moduleCount);for(var d=0;d<this.moduleCount;d++){this.modules[d]=new Array(this.moduleCount);for(var e=0;e<this.moduleCount;e++)this.modules[d][e]=null}this.setupPositionProbePattern(0,0),this.setupPositionProbePattern(this.moduleCount-7,0),this.setupPositionProbePattern(0,this.moduleCount-7),this.setupPositionAdjustPattern(),this.setupTimingPattern(),this.setupTypeInfo(a,c),this.typeNumber>=7&&this.setupTypeNumber(a),null==this.dataCache&&(this.dataCache=b.createData(this.typeNumber,this.errorCorrectLevel,this.dataList)),this.mapData(this.dataCache,c)},setupPositionProbePattern:function(a,b){for(var c=-1;7>=c;c++)if(!(-1>=a+c||this.moduleCount<=a+c))for(var d=-1;7>=d;d++)-1>=b+d||this.moduleCount<=b+d||(this.modules[a+c][b+d]=c>=0&&6>=c&&(0==d||6==d)||d>=0&&6>=d&&(0==c||6==c)||c>=2&&4>=c&&d>=2&&4>=d?!0:!1)},getBestMaskPattern:function(){for(var a=0,b=0,c=0;8>c;c++){this.makeImpl(!0,c);var d=f.getLostPoint(this);(0==c||a>d)&&(a=d,b=c)}return b},createMovieClip:function(a,b,c){var d=a.createEmptyMovieClip(b,c),e=1;this.make();for(var f=0;f<this.modules.length;f++)for(var g=f*e,h=0;h<this.modules[f].length;h++){var i=h*e,j=this.modules[f][h];j&&(d.beginFill(0,100),d.moveTo(i,g),d.lineTo(i+e,g),d.lineTo(i+e,g+e),d.lineTo(i,g+e),d.endFill())}return d},setupTimingPattern:function(){for(var a=8;a<this.moduleCount-8;a++)null==this.modules[a][6]&&(this.modules[a][6]=0==a%2);for(var b=8;b<this.moduleCount-8;b++)null==this.modules[6][b]&&(this.modules[6][b]=0==b%2)},setupPositionAdjustPattern:function(){for(var a=f.getPatternPosition(this.typeNumber),b=0;b<a.length;b++)for(var c=0;c<a.length;c++){var d=a[b],e=a[c];if(null==this.modules[d][e])for(var g=-2;2>=g;g++)for(var h=-2;2>=h;h++)this.modules[d+g][e+h]=-2==g||2==g||-2==h||2==h||0==g&&0==h?!0:!1}},setupTypeNumber:function(a){for(var b=f.getBCHTypeNumber(this.typeNumber),c=0;18>c;c++){var d=!a&&1==(1&b>>c);this.modules[Math.floor(c/3)][c%3+this.moduleCount-8-3]=d}for(var c=0;18>c;c++){var d=!a&&1==(1&b>>c);this.modules[c%3+this.moduleCount-8-3][Math.floor(c/3)]=d}},setupTypeInfo:function(a,b){for(var c=this.errorCorrectLevel<<3|b,d=f.getBCHTypeInfo(c),e=0;15>e;e++){var g=!a&&1==(1&d>>e);6>e?this.modules[e][8]=g:8>e?this.modules[e+1][8]=g:this.modules[this.moduleCount-15+e][8]=g}for(var e=0;15>e;e++){var g=!a&&1==(1&d>>e);8>e?this.modules[8][this.moduleCount-e-1]=g:9>e?this.modules[8][15-e-1+1]=g:this.modules[8][15-e-1]=g}this.modules[this.moduleCount-8][8]=!a},mapData:function(a,b){for(var c=-1,d=this.moduleCount-1,e=7,g=0,h=this.moduleCount-1;h>0;h-=2)for(6==h&&h--;;){for(var i=0;2>i;i++)if(null==this.modules[d][h-i]){var j=!1;g<a.length&&(j=1==(1&a[g]>>>e));var k=f.getMask(b,d,h-i);k&&(j=!j),this.modules[d][h-i]=j,e--,-1==e&&(g++,e=7)}if(d+=c,0>d||this.moduleCount<=d){d-=c,c=-c;break}}}},b.PAD0=236,b.PAD1=17,b.createData=function(a,c,d){for(var e=j.getRSBlocks(a,c),g=new k,h=0;h<d.length;h++){var i=d[h];g.put(i.mode,4),g.put(i.getLength(),f.getLengthInBits(i.mode,a)),i.write(g)}for(var l=0,h=0;h<e.length;h++)l+=e[h].dataCount;if(g.getLengthInBits()>8*l)throw new Error("code length overflow. ("+g.getLengthInBits()+">"+8*l+")");for(g.getLengthInBits()+4<=8*l&&g.put(0,4);0!=g.getLengthInBits()%8;)g.putBit(!1);for(;;){if(g.getLengthInBits()>=8*l)break;if(g.put(b.PAD0,8),g.getLengthInBits()>=8*l)break;g.put(b.PAD1,8)}return b.createBytes(g,e)},b.createBytes=function(a,b){for(var c=0,d=0,e=0,g=new Array(b.length),h=new Array(b.length),j=0;j<b.length;j++){var k=b[j].dataCount,l=b[j].totalCount-k;d=Math.max(d,k),e=Math.max(e,l),g[j]=new Array(k);for(var m=0;m<g[j].length;m++)g[j][m]=255&a.buffer[m+c];c+=k;var n=f.getErrorCorrectPolynomial(l),o=new i(g[j],n.getLength()-1),p=o.mod(n);h[j]=new Array(n.getLength()-1);for(var m=0;m<h[j].length;m++){var q=m+p.getLength()-h[j].length;h[j][m]=q>=0?p.get(q):0}}for(var r=0,m=0;m<b.length;m++)r+=b[m].totalCount;for(var s=new Array(r),t=0,m=0;d>m;m++)for(var j=0;j<b.length;j++)m<g[j].length&&(s[t++]=g[j][m]);for(var m=0;e>m;m++)for(var j=0;j<b.length;j++)m<h[j].length&&(s[t++]=h[j][m]);return s};for(var c={MODE_NUMBER:1,MODE_ALPHA_NUM:2,MODE_8BIT_BYTE:4,MODE_KANJI:8},d={L:1,M:0,Q:3,H:2},e={PATTERN000:0,PATTERN001:1,PATTERN010:2,PATTERN011:3,PATTERN100:4,PATTERN101:5,PATTERN110:6,PATTERN111:7},f={PATTERN_POSITION_TABLE:[[],[6,18],[6,22],[6,26],[6,30],[6,34],[6,22,38],[6,24,42],[6,26,46],[6,28,50],[6,30,54],[6,32,58],[6,34,62],[6,26,46,66],[6,26,48,70],[6,26,50,74],[6,30,54,78],[6,30,56,82],[6,30,58,86],[6,34,62,90],[6,28,50,72,94],[6,26,50,74,98],[6,30,54,78,102],[6,28,54,80,106],[6,32,58,84,110],[6,30,58,86,114],[6,34,62,90,118],[6,26,50,74,98,122],[6,30,54,78,102,126],[6,26,52,78,104,130],[6,30,56,82,108,134],[6,34,60,86,112,138],[6,30,58,86,114,142],[6,34,62,90,118,146],[6,30,54,78,102,126,150],[6,24,50,76,102,128,154],[6,28,54,80,106,132,158],[6,32,58,84,110,136,162],[6,26,54,82,110,138,166],[6,30,58,86,114,142,170]],G15:1335,G18:7973,G15_MASK:21522,getBCHTypeInfo:function(a){for(var b=a<<10;f.getBCHDigit(b)-f.getBCHDigit(f.G15)>=0;)b^=f.G15<<f.getBCHDigit(b)-f.getBCHDigit(f.G15);return(a<<10|b)^f.G15_MASK},getBCHTypeNumber:function(a){for(var b=a<<12;f.getBCHDigit(b)-f.getBCHDigit(f.G18)>=0;)b^=f.G18<<f.getBCHDigit(b)-f.getBCHDigit(f.G18);return a<<12|b},getBCHDigit:function(a){for(var b=0;0!=a;)b++,a>>>=1;return b},getPatternPosition:function(a){return f.PATTERN_POSITION_TABLE[a-1]},getMask:function(a,b,c){switch(a){case e.PATTERN000:return 0==(b+c)%2;case e.PATTERN001:return 0==b%2;case e.PATTERN010:return 0==c%3;case e.PATTERN011:return 0==(b+c)%3;case e.PATTERN100:return 0==(Math.floor(b/2)+Math.floor(c/3))%2;case e.PATTERN101:return 0==b*c%2+b*c%3;case e.PATTERN110:return 0==(b*c%2+b*c%3)%2;case e.PATTERN111:return 0==(b*c%3+(b+c)%2)%2;default:throw new Error("bad maskPattern:"+a)}},getErrorCorrectPolynomial:function(a){for(var b=new i([1],0),c=0;a>c;c++)b=b.multiply(new i([1,g.gexp(c)],0));return b},getLengthInBits:function(a,b){if(b>=1&&10>b)switch(a){case c.MODE_NUMBER:return 10;case c.MODE_ALPHA_NUM:return 9;case c.MODE_8BIT_BYTE:return 8;case c.MODE_KANJI:return 8;default:throw new Error("mode:"+a)}else if(27>b)switch(a){case c.MODE_NUMBER:return 12;case c.MODE_ALPHA_NUM:return 11;case c.MODE_8BIT_BYTE:return 16;case c.MODE_KANJI:return 10;default:throw new Error("mode:"+a)}else{if(!(41>b))throw new Error("type:"+b);switch(a){case c.MODE_NUMBER:return 14;case c.MODE_ALPHA_NUM:return 13;case c.MODE_8BIT_BYTE:return 16;case c.MODE_KANJI:return 12;default:throw new Error("mode:"+a)}}},getLostPoint:function(a){for(var b=a.getModuleCount(),c=0,d=0;b>d;d++)for(var e=0;b>e;e++){for(var f=0,g=a.isDark(d,e),h=-1;1>=h;h++)if(!(0>d+h||d+h>=b))for(var i=-1;1>=i;i++)0>e+i||e+i>=b||(0!=h||0!=i)&&g==a.isDark(d+h,e+i)&&f++;f>5&&(c+=3+f-5)}for(var d=0;b-1>d;d++)for(var e=0;b-1>e;e++){var j=0;a.isDark(d,e)&&j++,a.isDark(d+1,e)&&j++,a.isDark(d,e+1)&&j++,a.isDark(d+1,e+1)&&j++,(0==j||4==j)&&(c+=3)}for(var d=0;b>d;d++)for(var e=0;b-6>e;e++)a.isDark(d,e)&&!a.isDark(d,e+1)&&a.isDark(d,e+2)&&a.isDark(d,e+3)&&a.isDark(d,e+4)&&!a.isDark(d,e+5)&&a.isDark(d,e+6)&&(c+=40);for(var e=0;b>e;e++)for(var d=0;b-6>d;d++)a.isDark(d,e)&&!a.isDark(d+1,e)&&a.isDark(d+2,e)&&a.isDark(d+3,e)&&a.isDark(d+4,e)&&!a.isDark(d+5,e)&&a.isDark(d+6,e)&&(c+=40);for(var k=0,e=0;b>e;e++)for(var d=0;b>d;d++)a.isDark(d,e)&&k++;var l=Math.abs(100*k/b/b-50)/5;return c+=10*l}},g={glog:function(a){if(1>a)throw new Error("glog("+a+")");return g.LOG_TABLE[a]},gexp:function(a){for(;0>a;)a+=255;for(;a>=256;)a-=255;return g.EXP_TABLE[a]},EXP_TABLE:new Array(256),LOG_TABLE:new Array(256)},h=0;8>h;h++)g.EXP_TABLE[h]=1<<h;for(var h=8;256>h;h++)g.EXP_TABLE[h]=g.EXP_TABLE[h-4]^g.EXP_TABLE[h-5]^g.EXP_TABLE[h-6]^g.EXP_TABLE[h-8];for(var h=0;255>h;h++)g.LOG_TABLE[g.EXP_TABLE[h]]=h;i.prototype={get:function(a){return this.num[a]},getLength:function(){return this.num.length},multiply:function(a){for(var b=new Array(this.getLength()+a.getLength()-1),c=0;c<this.getLength();c++)for(var d=0;d<a.getLength();d++)b[c+d]^=g.gexp(g.glog(this.get(c))+g.glog(a.get(d)));return new i(b,0)},mod:function(a){if(this.getLength()-a.getLength()<0)return this;for(var b=g.glog(this.get(0))-g.glog(a.get(0)),c=new Array(this.getLength()),d=0;d<this.getLength();d++)c[d]=this.get(d);for(var d=0;d<a.getLength();d++)c[d]^=g.gexp(g.glog(a.get(d))+b);return new i(c,0).mod(a)}},j.RS_BLOCK_TABLE=[[1,26,19],[1,26,16],[1,26,13],[1,26,9],[1,44,34],[1,44,28],[1,44,22],[1,44,16],[1,70,55],[1,70,44],[2,35,17],[2,35,13],[1,100,80],[2,50,32],[2,50,24],[4,25,9],[1,134,108],[2,67,43],[2,33,15,2,34,16],[2,33,11,2,34,12],[2,86,68],[4,43,27],[4,43,19],[4,43,15],[2,98,78],[4,49,31],[2,32,14,4,33,15],[4,39,13,1,40,14],[2,121,97],[2,60,38,2,61,39],[4,40,18,2,41,19],[4,40,14,2,41,15],[2,146,116],[3,58,36,2,59,37],[4,36,16,4,37,17],[4,36,12,4,37,13],[2,86,68,2,87,69],[4,69,43,1,70,44],[6,43,19,2,44,20],[6,43,15,2,44,16],[4,101,81],[1,80,50,4,81,51],[4,50,22,4,51,23],[3,36,12,8,37,13],[2,116,92,2,117,93],[6,58,36,2,59,37],[4,46,20,6,47,21],[7,42,14,4,43,15],[4,133,107],[8,59,37,1,60,38],[8,44,20,4,45,21],[12,33,11,4,34,12],[3,145,115,1,146,116],[4,64,40,5,65,41],[11,36,16,5,37,17],[11,36,12,5,37,13],[5,109,87,1,110,88],[5,65,41,5,66,42],[5,54,24,7,55,25],[11,36,12],[5,122,98,1,123,99],[7,73,45,3,74,46],[15,43,19,2,44,20],[3,45,15,13,46,16],[1,135,107,5,136,108],[10,74,46,1,75,47],[1,50,22,15,51,23],[2,42,14,17,43,15],[5,150,120,1,151,121],[9,69,43,4,70,44],[17,50,22,1,51,23],[2,42,14,19,43,15],[3,141,113,4,142,114],[3,70,44,11,71,45],[17,47,21,4,48,22],[9,39,13,16,40,14],[3,135,107,5,136,108],[3,67,41,13,68,42],[15,54,24,5,55,25],[15,43,15,10,44,16],[4,144,116,4,145,117],[17,68,42],[17,50,22,6,51,23],[19,46,16,6,47,17],[2,139,111,7,140,112],[17,74,46],[7,54,24,16,55,25],[34,37,13],[4,151,121,5,152,122],[4,75,47,14,76,48],[11,54,24,14,55,25],[16,45,15,14,46,16],[6,147,117,4,148,118],[6,73,45,14,74,46],[11,54,24,16,55,25],[30,46,16,2,47,17],[8,132,106,4,133,107],[8,75,47,13,76,48],[7,54,24,22,55,25],[22,45,15,13,46,16],[10,142,114,2,143,115],[19,74,46,4,75,47],[28,50,22,6,51,23],[33,46,16,4,47,17],[8,152,122,4,153,123],[22,73,45,3,74,46],[8,53,23,26,54,24],[12,45,15,28,46,16],[3,147,117,10,148,118],[3,73,45,23,74,46],[4,54,24,31,55,25],[11,45,15,31,46,16],[7,146,116,7,147,117],[21,73,45,7,74,46],[1,53,23,37,54,24],[19,45,15,26,46,16],[5,145,115,10,146,116],[19,75,47,10,76,48],[15,54,24,25,55,25],[23,45,15,25,46,16],[13,145,115,3,146,116],[2,74,46,29,75,47],[42,54,24,1,55,25],[23,45,15,28,46,16],[17,145,115],[10,74,46,23,75,47],[10,54,24,35,55,25],[19,45,15,35,46,16],[17,145,115,1,146,116],[14,74,46,21,75,47],[29,54,24,19,55,25],[11,45,15,46,46,16],[13,145,115,6,146,116],[14,74,46,23,75,47],[44,54,24,7,55,25],[59,46,16,1,47,17],[12,151,121,7,152,122],[12,75,47,26,76,48],[39,54,24,14,55,25],[22,45,15,41,46,16],[6,151,121,14,152,122],[6,75,47,34,76,48],[46,54,24,10,55,25],[2,45,15,64,46,16],[17,152,122,4,153,123],[29,74,46,14,75,47],[49,54,24,10,55,25],[24,45,15,46,46,16],[4,152,122,18,153,123],[13,74,46,32,75,47],[48,54,24,14,55,25],[42,45,15,32,46,16],[20,147,117,4,148,118],[40,75,47,7,76,48],[43,54,24,22,55,25],[10,45,15,67,46,16],[19,148,118,6,149,119],[18,75,47,31,76,48],[34,54,24,34,55,25],[20,45,15,61,46,16]],j.getRSBlocks=function(a,b){var c=j.getRsBlockTable(a,b);if(void 0==c)throw new Error("bad rs block @ typeNumber:"+a+"/errorCorrectLevel:"+b);for(var d=c.length/3,e=[],f=0;d>f;f++)for(var g=c[3*f+0],h=c[3*f+1],i=c[3*f+2],k=0;g>k;k++)e.push(new j(h,i));return e},j.getRsBlockTable=function(a,b){switch(b){case d.L:return j.RS_BLOCK_TABLE[4*(a-1)+0];case d.M:return j.RS_BLOCK_TABLE[4*(a-1)+1];case d.Q:return j.RS_BLOCK_TABLE[4*(a-1)+2];case d.H:return j.RS_BLOCK_TABLE[4*(a-1)+3];default:return void 0}},k.prototype={get:function(a){var b=Math.floor(a/8);return 1==(1&this.buffer[b]>>>7-a%8)},put:function(a,b){for(var c=0;b>c;c++)this.putBit(1==(1&a>>>b-c-1))},getLengthInBits:function(){return this.length},putBit:function(a){var b=Math.floor(this.length/8);this.buffer.length<=b&&this.buffer.push(0),a&&(this.buffer[b]|=128>>>this.length%8),this.length++}};var l=[[17,14,11,7],[32,26,20,14],[53,42,32,24],[78,62,46,34],[106,84,60,44],[134,106,74,58],[154,122,86,64],[192,152,108,84],[230,180,130,98],[271,213,151,119],[321,251,177,137],[367,287,203,155],[425,331,241,177],[458,362,258,194],[520,412,292,220],[586,450,322,250],[644,504,364,280],[718,560,394,310],[792,624,442,338],[858,666,482,382],[929,711,509,403],[1003,779,565,439],[1091,857,611,461],[1171,911,661,511],[1273,997,715,535],[1367,1059,751,593],[1465,1125,805,625],[1528,1190,868,658],[1628,1264,908,698],[1732,1370,982,742],[1840,1452,1030,790],[1952,1538,1112,842],[2068,1628,1168,898],[2188,1722,1228,958],[2303,1809,1283,983],[2431,1911,1351,1051],[2563,1989,1423,1093],[2699,2099,1499,1139],[2809,2213,1579,1219],[2953,2331,1663,1273]],o=function(){var a=function(a,b){this._el=a,this._htOption=b};return a.prototype.draw=function(a){function g(a,b){var c=document.createElementNS("http://www.w3.org/2000/svg",a);for(var d in b)b.hasOwnProperty(d)&&c.setAttribute(d,b[d]);return c}var b=this._htOption,c=this._el,d=a.getModuleCount();Math.floor(b.width/d),Math.floor(b.height/d),this.clear();var h=g("svg",{viewBox:"0 0 "+String(d)+" "+String(d),width:"100%",height:"100%",fill:b.colorLight});h.setAttributeNS("http://www.w3.org/2000/xmlns/","xmlns:xlink","http://www.w3.org/1999/xlink"),c.appendChild(h),h.appendChild(g("rect",{fill:b.colorDark,width:"1",height:"1",id:"template"}));for(var i=0;d>i;i++)for(var j=0;d>j;j++)if(a.isDark(i,j)){var k=g("use",{x:String(i),y:String(j)});k.setAttributeNS("http://www.w3.org/1999/xlink","href","#template"),h.appendChild(k)}},a.prototype.clear=function(){for(;this._el.hasChildNodes();)this._el.removeChild(this._el.lastChild)},a}(),p="svg"===document.documentElement.tagName.toLowerCase(),q=p?o:m()?function(){function a(){this._elImage.src=this._elCanvas.toDataURL("image/png"),this._elImage.style.display="block",this._elCanvas.style.display="none"}function d(a,b){var c=this;if(c._fFail=b,c._fSuccess=a,null===c._bSupportDataURI){var d=document.createElement("img"),e=function(){c._bSupportDataURI=!1,c._fFail&&_fFail.call(c)},f=function(){c._bSupportDataURI=!0,c._fSuccess&&c._fSuccess.call(c)};return d.onabort=e,d.onerror=e,d.onload=f,d.src="data:image/gif;base64,iVBORw0KGgoAAAANSUhEUgAAAAUAAAAFCAYAAACNbyblAAAAHElEQVQI12P4//8/w38GIAXDIBKE0DHxgljNBAAO9TXL0Y4OHwAAAABJRU5ErkJggg==",void 0}c._bSupportDataURI===!0&&c._fSuccess?c._fSuccess.call(c):c._bSupportDataURI===!1&&c._fFail&&c._fFail.call(c)}if(this._android&&this._android<=2.1){var b=1/window.devicePixelRatio,c=CanvasRenderingContext2D.prototype.drawImage;CanvasRenderingContext2D.prototype.drawImage=function(a,d,e,f,g,h,i,j){if("nodeName"in a&&/img/i.test(a.nodeName))for(var l=arguments.length-1;l>=1;l--)arguments[l]=arguments[l]*b;else"undefined"==typeof j&&(arguments[1]*=b,arguments[2]*=b,arguments[3]*=b,arguments[4]*=b);c.apply(this,arguments)}}var e=function(a,b){this._bIsPainted=!1,this._android=n(),this._htOption=b,this._elCanvas=document.createElement("canvas"),this._elCanvas.width=b.width,this._elCanvas.height=b.height,a.appendChild(this._elCanvas),this._el=a,this._oContext=this._elCanvas.getContext("2d"),this._bIsPainted=!1,this._elImage=document.createElement("img"),this._elImage.style.display="none",this._el.appendChild(this._elImage),this._bSupportDataURI=null};return e.prototype.draw=function(a){var b=this._elImage,c=this._oContext,d=this._htOption,e=a.getModuleCount(),f=d.width/e,g=d.height/e,h=Math.round(f),i=Math.round(g);b.style.display="none",this.clear();for(var j=0;e>j;j++)for(var k=0;e>k;k++){var l=a.isDark(j,k),m=k*f,n=j*g;c.strokeStyle=l?d.colorDark:d.colorLight,c.lineWidth=1,c.fillStyle=l?d.colorDark:d.colorLight,c.fillRect(m,n,f,g),c.strokeRect(Math.floor(m)+.5,Math.floor(n)+.5,h,i),c.strokeRect(Math.ceil(m)-.5,Math.ceil(n)-.5,h,i)}this._bIsPainted=!0},e.prototype.makeImage=function(){this._bIsPainted&&d.call(this,a)},e.prototype.isPainted=function(){return this._bIsPainted},e.prototype.clear=function(){this._oContext.clearRect(0,0,this._elCanvas.width,this._elCanvas.height),this._bIsPainted=!1},e.prototype.round=function(a){return a?Math.floor(1e3*a)/1e3:a},e}():function(){var a=function(a,b){this._el=a,this._htOption=b};return a.prototype.draw=function(a){for(var b=this._htOption,c=this._el,d=a.getModuleCount(),e=Math.floor(b.width/d),f=Math.floor(b.height/d),g=['<table style="border:0;border-collapse:collapse;">'],h=0;d>h;h++){g.push("<tr>");for(var i=0;d>i;i++)g.push('<td style="border:0;border-collapse:collapse;padding:0;margin:0;width:'+e+"px;height:"+f+"px;background-color:"+(a.isDark(h,i)?b.colorDark:b.colorLight)+';"></td>');g.push("</tr>")}g.push("</table>"),c.innerHTML=g.join("");var j=c.childNodes[0],k=(b.width-j.offsetWidth)/2,l=(b.height-j.offsetHeight)/2;k>0&&l>0&&(j.style.margin=l+"px "+k+"px")},a.prototype.clear=function(){this._el.innerHTML=""},a}();QRCode=function(a,b){if(this._htOption={width:256,height:256,typeNumber:4,colorDark:"#000000",colorLight:"#ffffff",correctLevel:d.H},"string"==typeof b&&(b={text:b}),b)for(var c in b)this._htOption[c]=b[c];"string"==typeof a&&(a=document.getElementById(a)),this._android=n(),this._el=a,this._oQRCode=null,this._oDrawing=new q(this._el,this._htOption),this._htOption.text&&this.makeCode(this._htOption.text)},QRCode.prototype.makeCode=function(a){this._oQRCode=new b(r(a,this._htOption.correctLevel),this._htOption.correctLevel),this._oQRCode.addData(a),this._oQRCode.make(),this._el.title=a,this._oDrawing.draw(this._oQRCode),this.makeImage()},QRCode.prototype.makeImage=function(){"function"==typeof this._oDrawing.makeImage&&(!this._android||this._android>=3)&&this._oDrawing.makeImage()},QRCode.prototype.clear=function(){this._oDrawing.clear()},QRCode.CorrectLevel=d}();
  </script>

  <!-- MAIN SCRIPT -->
  <script>
    let currentServices = {};
    let serverConfig = {};
    let statsInterval = null;

    function showToast(msg) {
      const toast = document.getElementById('toast');
      toast.innerText = msg;
      toast.classList.add('show');
      setTimeout(() => toast.classList.remove('show'), 2800);
    }

    function closeModal() {
      document.getElementById('confirm-modal').classList.remove('open');
    }

    function openSettingsModal() {
      const err = document.getElementById('settings-err');
      err.style.display = 'none';
      document.getElementById('settings-user').value = (serverConfig && serverConfig.auth_user) ? serverConfig.auth_user : '';
      const passInp = document.getElementById('settings-pass');
      passInp.value = '';
      passInp.type = 'password';
      document.getElementById('settings-pass2').value = '';
      document.querySelectorAll('#settings-modal .btn-eye').forEach(b => b.style.color = '#8E8E93');
      document.getElementById('settings-modal').classList.add('open');
    }

    function closeSettingsModal() {
      document.getElementById('settings-modal').classList.remove('open');
    }

    function togglePassVisibility(inputId, btn) {
      const inp = document.getElementById(inputId);
      if (!inp) return;
      if (inp.type === 'password') {
        inp.type = 'text';
        btn.style.color = '#007AFF';
      } else {
        inp.type = 'password';
        btn.style.color = '#8E8E93';
      }
    }

    function handleModalOverlayClick(e, modalId) {
      if (e.target.id === modalId) {
        if (modalId === 'settings-modal') closeSettingsModal();
        if (modalId === 'confirm-modal') closeModal();
        if (modalId === 'qr-modal') closeQRModal();
      }
    }

    // Keyboard support: Escape closes modals, Enter in inputs saves
    window.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        closeSettingsModal();
        closeModal();
        closeQRModal();
      }
    });

    document.addEventListener('DOMContentLoaded', () => {
      const modal = document.getElementById('settings-modal');
      if (modal) {
        modal.addEventListener('keydown', (e) => {
          if (e.key === 'Enter' && e.target.tagName === 'INPUT') {
            e.preventDefault();
            saveSettings();
          }
        });
      }
    });

    function saveSettings() {
      const user = document.getElementById('settings-user').value.trim();
      const p1 = document.getElementById('settings-pass').value;
      const p2 = document.getElementById('settings-pass2').value;
      const err = document.getElementById('settings-err');
      err.style.display = 'none';

      if (!user) {
        err.innerText = 'Имя пользователя не может быть пустым';
        err.style.display = 'block';
        return;
      }

      // Password change is optional: validate only if entered
      if (p1 || p2) {
        if (!p1) {
          err.innerText = 'Введите новый пароль';
          err.style.display = 'block';
          return;
        }
        if (p1.length < 8) {
          err.innerText = 'Пароль должен содержать не менее 8 символов';
          err.style.display = 'block';
          return;
        }
        if (p1 !== p2) {
          err.innerText = 'Пароли не совпадают';
          err.style.display = 'block';
          return;
        }
      }

      const btn = document.getElementById('settings-save-btn');
      btn.disabled = true;
      btn.innerHTML = `
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" style="animation:spin 1s linear infinite;"><path d="M21 12a9 9 0 1 1-6.219-8.56"></path></svg>
        Сохранение...
      `;

      fetch('api/settings/auth', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user: user, pass: p1 })
      })
      .then(r => r.json())
      .then(res => {
        btn.disabled = false;
        btn.innerText = 'Сохранить';
        if (res.success) {
          closeSettingsModal();
          showToast(res.message || 'Данные успешно обновлены!');
          if (serverConfig) serverConfig.auth_user = user;
        } else {
          err.innerText = res.error || 'Ошибка при сохранении';
          err.style.display = 'block';
        }
      })
      .catch(e => {
        btn.disabled = false;
        btn.innerText = 'Сохранить';
        err.innerText = 'Сетевая ошибка: ' + e;
        err.style.display = 'block';
      });
    }

    function closeQRModal() {
      document.getElementById('qr-modal').classList.remove('open');
    }

    function openQR(text, title) {
      document.getElementById('qr-title').innerText = title || 'QR-код для подключения';
      const box = document.getElementById('qr-box');
      box.innerHTML = '';
      new QRCode(box, {
        text: text,
        width: 200,
        height: 200,
        colorDark: "#000000",
        colorLight: "#ffffff",
        correctLevel: QRCode.CorrectLevel.M
      });
      document.getElementById('qr-modal').classList.add('open');
    }

    function copyToClipboard(btn, text) {
      navigator.clipboard.writeText(text).then(() => {
        const orig = btn.innerHTML;
        btn.innerHTML = '✓ Скопировано';
        btn.classList.add('copied');
        setTimeout(() => {
          btn.innerHTML = orig;
          btn.classList.remove('copied');
        }, 2000);
      }).catch(() => {
        showToast('Не удалось скопировать');
      });
    }

    function genRandomPort() {
      return Math.floor(Math.random() * (45000 - 20000 + 1)) + 20000;
    }

    function genRandomPath() {
      const chars = 'abcdefghijklmnopqrstuvwxyz0123456789';
      let res = '';
      for (let i = 0; i < 8; i++) res += chars.charAt(Math.floor(Math.random() * chars.length));
      return res;
    }

    // Smart Polling: updates only when document is visible
    function fetchStats() {
      fetch('api/stats')
        .then(r => r.json())
        .then(data => {
          document.getElementById('metric-cpu-val').innerText = `${data.cpu}%`;
          document.getElementById('metric-cpu-bar').style.width = `${Math.min(100, data.cpu)}%`;

          document.getElementById('metric-ram-val').innerText = `${data.ram.percent}%`;
          document.getElementById('metric-ram-bar').style.width = `${data.ram.percent}%`;

          document.getElementById('metric-disk-val').innerText = `${data.disk.percent}%`;
          document.getElementById('metric-disk-bar').style.width = `${data.disk.percent}%`;
        }).catch(() => {});
    }

    function fetchFullState() {
      fetch('api/state')
        .then(r => r.json())
        .then(data => {
          serverConfig = data.config || {};
          currentServices = data.services || {};

          // Update Status
          const st = data.status || {};
          setDot('nginx', st.nginx);
          setDot('xui', st.xui);
          setDot('telemt', st.telemt);
          setDot('adguard', st.adguard);

          // Update SSL
          if (data.ssl && data.ssl.valid) {
            document.getElementById('ssl-btn-text').innerText = `SSL: ${data.ssl.days_left} дн.`;
          }

          renderServices(currentServices);
          renderLinks(data.links || []);
        });
    }

    function setDot(id, active) {
      const dot = document.getElementById(`dot-${id}`);
      const text = document.getElementById(`text-${id}`);
      if (active) {
        dot.className = 'status-dot active';
        text.innerText = 'Активен';
      } else {
        dot.className = 'status-dot inactive';
        text.innerText = 'Остановлен';
      }
    }

    function renderServices(services) {
      const container = document.getElementById('services-grid');
      container.innerHTML = '';

      Object.values(services).forEach(s => {
        const card = document.createElement('div');
        card.className = 'service-card';

        let pathInputs = '';
        if (s.has_path) {
          pathInputs = `
            <div class="input-row">
              <label class="input-label">Новый путь / ServiceName</label>
              <div class="input-field-wrap">
                <input id="input-path-${s.id}" class="apple-input" placeholder="${s.current_path}" />
                <button class="btn-dice" onclick="rollPath('${s.id}')" title="Сгенерировать случайный путь">🎲</button>
              </div>
            </div>
          `;
        }

        let portInputs = '';
        if (s.has_port) {
          portInputs = `
            <div class="input-row">
              <label class="input-label">Новый внутренний порт</label>
              <div class="input-field-wrap">
                <input id="input-port-${s.id}" type="number" class="apple-input" placeholder="${s.current_port}" />
                <button class="btn-dice" onclick="rollPort('${s.id}')" title="Сгенерировать случайный свободный порт">🎲</button>
              </div>
            </div>
          `;
        }

        card.innerHTML = `
          <div>
            <div class="card-header">
              <div class="card-title">${s.name}</div>
              <span class="badge">${s.badge || s.protocol}</span>
            </div>

            <div class="current-props">
              <div class="prop-row">
                <span>Текущий порт:</span>
                <span class="prop-val">${s.current_port}</span>
              </div>
              ${s.has_path ? `
              <div class="prop-row">
                <span>Текущий путь:</span>
                <span class="prop-val">${s.current_path}</span>
              </div>
              ` : ''}
            </div>

            <div class="inputs-area">
              ${portInputs}
              ${pathInputs}
            </div>
          </div>

          <button class="btn-apply" onclick="promptApply('${s.id}')">
            Применить изменения
          </button>
        `;
        container.appendChild(card);
      });
    }

    function rollPort(id) {
      document.getElementById(`input-port-${id}`).value = genRandomPort();
    }

    function rollPath(id) {
      const p = genRandomPath();
      const input = document.getElementById(`input-path-${id}`);
      if (id.includes('grpc')) {
        input.value = p + '-grpc';
      } else {
        input.value = '/' + p;
      }
    }

    function promptApply(id) {
      const s = currentServices[id];
      const portInput = document.getElementById(`input-port-${id}`);
      const pathInput = document.getElementById(`input-path-${id}`);

      const newPort = portInput ? portInput.value.trim() : '';
      const newPath = pathInput ? pathInput.value.trim() : '';

      if (!newPort && !newPath) {
        showToast('Введите новый порт или путь!');
        return;
      }

      let desc = `Применить новые параметры для <b>${s.name}</b>?<br><br>`;
      if (newPort) desc += `• Порт: <s>${s.current_port}</s> → <b>${newPort}</b><br>`;
      if (newPath) desc += `• Путь: <s>${s.current_path}</s> → <b>${newPath}</b><br>`;
      desc += `<br><span style="color:#FF9500">Параметры автоматически обновятся в Nginx, SQLite 3X-UI и сервисах.</span>`;

      document.getElementById('modal-title').innerText = 'Подтверждение изменений';
      document.getElementById('modal-body').innerHTML = desc;
      const btn = document.getElementById('modal-confirm-btn');
      btn.className = 'btn-confirm';
      btn.innerText = 'Применить';
      btn.onclick = () => doApply(id, newPort, newPath);
      document.getElementById('confirm-modal').classList.add('open');
    }

    function doApply(id, newPort, newPath) {
      closeModal();
      showToast('Применение и верификация Nginx...');
      fetch('api/apply', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ service_id: id, new_port: newPort, new_path: newPath })
      })
      .then(r => r.json())
      .then(res => {
        if (res.success) {
          showToast(res.message || 'Успешно обновлено!');
          fetchFullState();
        } else {
          alert('Ошибка:\n' + res.error);
        }
      }).catch(err => {
        alert('Сетевая ошибка: ' + err);
      });
    }

    function renderLinks(links) {
      const c = document.getElementById('links-container');
      c.innerHTML = '';

      links.forEach(l => {
        const item = document.createElement('div');
        item.className = 'link-item';
        item.innerHTML = `
          <div class="link-info">
            <div class="link-name">${l.name}</div>
            <div class="link-url">${l.url}</div>
          </div>
          <div class="link-actions">
            <button class="btn-copy" onclick="copyToClipboard(this, '${l.url.replace(/'/g, "\\'")}')">Скопировать</button>
            <button class="btn-qr" onclick="openQR('${l.url.replace(/'/g, "\\'")}', '${l.name}')">QR</button>
          </div>
        `;
        c.appendChild(item);
      });
    }

    function restartStack() {
      if (!confirm('Перезапустить все сервисы (Nginx, 3X-UI, Telemt, AdGuard)?')) return;
      showToast('Перезапуск служб...');
      fetch('api/restart-stack', { method: 'POST' })
        .then(r => r.json())
        .then(() => {
          showToast('Стек успешно перезапущен!');
          setTimeout(fetchFullState, 1500);
        });
    }

    function checkSSL() {
      showToast('Проверка сертификата Cloudflare...');
      fetch('api/check-ssl')
        .then(r => r.json())
        .then(res => {
          alert(`SSL Статус:\\nДействителен: ${res.valid ? 'Да' : 'Нет'}\\nОсталось дней: ${res.days_left}\\nИстекает: ${res.expires}`);
        });
    }

    function confirmLogout() {
      document.getElementById('modal-title').innerText = 'Выход из модуля';
      document.getElementById('modal-body').innerHTML =
        'При выходе порт модуля будет <b>ЗАБЛОКИРОВАН в файрволе UFW</b>.<br>Открытыми останутся только порты 22 и 443.<br><br>Для повторного входа откройте порт через консоль (<code>server-mgr open</code>) или используйте SSH-туннель.';
      const btn = document.getElementById('modal-confirm-btn');
      btn.className = 'btn-confirm danger';
      btn.innerText = 'Выйти и заблокировать порт';
      btn.onclick = executeLogout;
      document.getElementById('confirm-modal').classList.add('open');
    }

    function executeLogout() {
      closeModal();
      document.body.innerHTML = `
        <div style="min-height:100vh; display:flex; align-items:center; justify-content:center; padding:20px; background:#000; color:#FFF; font-family:-apple-system,BlinkMacSystemFont,sans-serif;">
          <div style="background:rgba(28,28,30,0.8); border:1px solid rgba(255,255,255,0.1); border-radius:22px; padding:36px 30px; width:100%; max-width:380px; text-align:center;">
            <div style="font-size:40px; margin-bottom:12px;">🔒</div>
            <h1 style="font-size:20px; font-weight:700; margin-bottom:8px;">Сессия завершена</h1>
            <p style="font-size:13px; color:#8E8E93; margin-bottom:20px;">Порт модуля закрыт в файрволе UFW. Открытыми на сервере остались только порты <b>22</b> и <b>443</b>.</p>
            <div style="background:rgba(0,0,0,0.5); padding:12px; border-radius:10px; font-family:monospace; font-size:13px; color:#30D158; margin-bottom:20px;">
              # Для повторного открытия выполните в SSH:<br>server-mgr open
            </div>
            <p style="font-size:12px; color:#636366">Вы можете безопасно закрыть эту вкладку браузера.</p>
          </div>
        </div>
      `;
      fetch('api/logout', { method: 'POST' }).catch(() => {});
    }

    // Page visibility event to pause metric queries
    document.addEventListener('visibilitychange', () => {
      if (document.hidden) {
        clearInterval(statsInterval);
      } else {
        fetchStats();
        statsInterval = setInterval(fetchStats, 5000);
      }
    });

    // Initial load
    fetchFullState();
    fetchStats();
    statsInterval = setInterval(fetchStats, 5000);
  </script>
</body>
</html>
"""

LOGIN_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <title>Вход — Конфигурирование сервера</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; -webkit-font-smoothing: antialiased; }
    body {
      background: #000;
      color: #FFF;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
      background-image: radial-gradient(at 50% 50%, rgba(0, 122, 255, 0.15) 0px, transparent 60%);
    }
    .login-card {
      background: rgba(28, 28, 30, 0.8);
      backdrop-filter: blur(30px);
      -webkit-backdrop-filter: blur(30px);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 22px;
      padding: 36px 30px;
      width: 100%;
      max-width: 380px;
      box-shadow: 0 20px 50px rgba(0, 0, 0, 0.7);
      text-align: center;
    }
    .icon { font-size: 40px; margin-bottom: 12px; }
    h1 { font-size: 20px; font-weight: 700; margin-bottom: 6px; }
    p { font-size: 13px; color: #8E8E93; margin-bottom: 24px; }
    .input-wrap { margin-bottom: 14px; text-align: left; }
    .input-wrap label { font-size: 12px; color: #8E8E93; display: block; margin-bottom: 5px; font-weight: 500; }
    input {
      width: 100%;
      background: rgba(44, 44, 46, 0.8);
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: 12px;
      padding: 12px 14px;
      color: #FFF;
      font-size: 15px;
      outline: none;
      transition: all 0.2s;
    }
    input:focus { border-color: #007AFF; box-shadow: 0 0 0 3px rgba(0, 122, 255, 0.3); }
    .btn-login {
      width: 100%;
      padding: 13px;
      background: #007AFF;
      border: none;
      border-radius: 12px;
      color: #FFF;
      font-size: 15px;
      font-weight: 600;
      cursor: pointer;
      margin-top: 10px;
      transition: all 0.2s;
    }
    .btn-login:hover { background: #0A84FF; }
    .btn-login:active { transform: scale(0.98); }
    .error-msg { color: #FF453A; font-size: 13px; margin-top: 12px; display: none; }
  </style>
</head>
<body>
  <div class="login-card">
    <div class="icon">⚙️</div>
    <h1>Конфигурирование сервера</h1>
    <p>Авторизация администратора</p>
    <form id="login-form">
      <div class="input-wrap">
        <label>Имя пользователя</label>
        <input id="user" type="text" autocomplete="username" autocapitalize="none" autocorrect="off" spellcheck="false" required autofocus />
      </div>
      <div class="input-wrap">
        <label>Пароль</label>
        <input id="pass" type="password" autocomplete="current-password" required />
      </div>
      <button type="submit" class="btn-login">Войти</button>
      <div id="err" class="error-msg">Неверный логин или пароль</div>
    </form>
  </div>
  <script>
    document.getElementById('login-form').onsubmit = (e) => {
      e.preventDefault();
      const u = document.getElementById('user').value.trim();
      const p = document.getElementById('pass').value;
      const err = document.getElementById('err');
      err.style.display = 'none';

      fetch('api/auth', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user: u, pass: p })
      }).then(r => r.json()).then(res => {
        if (res.success) {
          window.location.reload();
        } else {
          err.innerText = res.error || 'Ошибка входа';
          err.style.display = 'block';
        }
      }).catch(() => {
        err.innerText = 'Сетевая ошибка';
        err.style.display = 'block';
      });
    };
  </script>
</body>
</html>
"""

LOGGED_OUT_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover">
  <title>Сессия завершена</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; -webkit-font-smoothing: antialiased; }
    body {
      background: #000;
      color: #FFF;
      font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", sans-serif;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 20px;
    }
    .card {
      background: rgba(28, 28, 30, 0.85);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: 24px;
      padding: 36px 30px;
      max-width: 440px;
      text-align: center;
      box-shadow: 0 20px 50px rgba(0, 0, 0, 0.6);
    }
    .icon { font-size: 46px; margin-bottom: 12px; }
    h1 { font-size: 21px; font-weight: 700; margin-bottom: 8px; }
    p { font-size: 14px; color: #8E8E93; line-height: 1.5; margin-bottom: 20px; }
    .code-box {
      background: rgba(0, 0, 0, 0.5);
      border: 1px solid rgba(255, 255, 255, 0.08);
      border-radius: 12px;
      padding: 12px;
      font-family: ui-monospace, Menlo, monospace;
      font-size: 13px;
      color: #34D399;
      text-align: left;
      margin-bottom: 20px;
    }
  </style>
</head>
<body>
  <div class="card">
    <div class="icon">🔒</div>
    <h1>Сессия завершена</h1>
    <p>Порт модуля закрыт в файрволе UFW. Открытыми на сервере остались только порты <b>22</b> и <b>443</b>.</p>
    <div class="code-box">
      # Для повторного открытия выполните в SSH:<br>
      server-mgr open
    </div>
    <p style="font-size:12px; color:#636366">Вы можете безопасно закрыть эту вкладку браузера.</p>
  </div>
</body>
</html>
"""


# ==============================================================================
# [6] HTTP REQUEST HANDLER
# ==============================================================================

class ServerManagerHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_HEAD(self):
        self.do_GET()

    def log_message(self, format, *args):
        # Suppress verbose standard logging to keep system clean
        pass

    def get_client_ip(self):
        return self.client_address[0]

    def get_cookie(self, name):
        cookie_header = self.headers.get("Cookie", "")
        for item in cookie_header.split(";"):
            item = item.strip()
            if item.startswith(f"{name}="):
                return item.split("=", 1)[1]
        return None

    def is_authenticated(self):
        token = self.get_cookie("mgmt_session")
        if not token or token not in SESSIONS:
            return False
        session = SESSIONS[token]
        if time.time() > session["expires"]:
            del SESSIONS[token]
            return False
        return True

    def send_json(self, data, code=200, set_cookie=None):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "keep-alive")
        self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        try:
            if self.command != "HEAD":
                self.wfile.write(body)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def send_html(self, html_text, code=200, set_cookie=None):
        body = html_text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "keep-alive")
        self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        try:
            if self.command != "HEAD":
                self.wfile.write(body)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        cfg = load_master_config()
        secret_path = cfg.get("mgmt_secret_path", "cfg-admin")
        parsed = urlparse(self.path)
        path = parsed.path
        expected_prefix = f"/{secret_path}/"

        # 1. Bare root or incorrect path -> redirect to secret path with trailing slash
        if path == "/" or path == f"/{secret_path}" or not path.startswith(expected_prefix):
            self.send_response(302)
            self.send_header("Location", expected_prefix)
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            try:
                self.wfile.flush()
            except Exception:
                pass
            return

        rel_path = path[len(expected_prefix):].rstrip("/")

        # Logged out screen
        if rel_path == "logged-out":
            self.send_html(LOGGED_OUT_PAGE)
            return

        # Check authentication
        if not self.is_authenticated():
            self.send_html(LOGIN_PAGE)
            return

        # API: System stats (lightweight poll)
        if rel_path == "api/stats":
            self.send_json({
                "cpu": get_cpu_usage(),
                "ram": get_ram_usage(),
                "disk": get_disk_usage()
            })
            return

        # API: Full State
        if rel_path == "api/state":
            domain = cfg.get("domain", "example.com")
            server_ip = cfg.get("server_ip", "127.0.0.1")
            services = detect_live_services(cfg)

            # Build Links Summary
            links = []
            if "xui_panel" in services:
                links.append({
                    "name": "Панель 3X-UI (HTTPS)",
                    "url": f"https://{domain}{services['xui_panel']['current_path']}"
                })
            if "sub_base64" in services:
                sub_path = services['sub_base64']['current_path'].rstrip("/") + "/"
                client_sid = services['sub_base64'].get("client_sub_id")
                if client_sid:
                    links.append({
                        "name": "Подписка Base64 (V2Ray / Clash)",
                        "url": f"https://{domain}{sub_path}{client_sid}"
                    })
                else:
                    links.append({
                        "name": "Подписка Base64 (Базовый путь — добавьте клиента в 3X-UI)",
                        "url": f"https://{domain}{sub_path}"
                    })
            if "sub_json" in services:
                sub_json_path = services['sub_json']['current_path'].rstrip("/") + "/"
                client_sid = services['sub_json'].get("client_sub_id")
                if client_sid:
                    links.append({
                        "name": "Подписка JSON (Happ / Sing-box)",
                        "url": f"https://{domain}{sub_json_path}{client_sid}"
                    })
            if "telemt" in services:
                tg_s = ""
                if os.path.exists(TELEMT_CONF_PATH):
                    try:
                        with open(TELEMT_CONF_PATH, "r") as f:
                            lines = f.read().splitlines()
                        in_users = False
                        for line in lines:
                            line = line.strip()
                            if line.startswith("#"):
                                continue
                            if line == "[access.users]":
                                in_users = True
                                continue
                            if in_users:
                                if line.startswith("["):
                                    break
                                m = re.match(r'^[a-zA-Z0-9_-]+\s*=\s*"([0-9a-fA-F]{32})"', line)
                                if m:
                                    tg_s = m.group(1)
                                    break
                    except Exception:
                        pass
                tg_p = services['telemt']['current_path'].strip("/")
                if tg_s:
                    links.append({
                        "name": "Telemt Web Proxy (Telegram)",
                        "url": f"tg://webproxy?server={domain}%2F{tg_p}&secret={tg_s}"
                    })
                    links.append({
                        "name": "Classic MTProto (через порт 443)",
                        "url": f"tg://proxy?server={domain}&port=443&secret=dd{tg_s}"
                    })
                else:
                    links.append({
                        "name": "Telemt Web Proxy (Telegram)",
                        "url": f"tg://webproxy?server={domain}%2F{tg_p}"
                    })
            if "adguard_doh" in services:
                links.append({
                    "name": "AdGuard Home DoH (HTTPS)",
                    "url": f"https://{domain}{services['adguard_doh']['current_path']}"
                })
            if "adguard_web" in services:
                links.append({
                    "name": "AdGuard Home Web UI",
                    "url": f"http://{server_ip}:{services['adguard_web']['current_port']}"
                })
            if "telemt_panel" in services:
                links.append({
                    "name": "Панель Telemt HTTPS",
                    "url": f"https://{domain}:{services['telemt_panel']['current_port']}"
                })

            safe_cfg = {
                "domain": domain,
                "mgmt_port": cfg.get("mgmt_port"),
                "mgmt_secret_path": cfg.get("mgmt_secret_path"),
                "auth_user": cfg.get("auth_user", "admin"),
                "auto_lock_minutes": cfg.get("auto_lock_minutes", 30),
                "server_ip": server_ip
            }

            self.send_json({
                "config": safe_cfg,
                "services": services,
                "status": {
                    "nginx": get_service_status("nginx"),
                    "xui": get_service_status("x-ui"),
                    "telemt": get_service_status("telemt"),
                    "adguard": get_service_status("AdGuardHome")
                },
                "ssl": get_ssl_status(domain),
                "links": links
            })
            return

        # API: Check SSL
        if rel_path == "api/check-ssl":
            domain = cfg.get("domain", "example.com")
            self.send_json(get_ssl_status(domain))
            return

        # Main Dashboard Page
        self.send_html(HTML_PAGE)

    def do_POST(self):
        cfg = load_master_config()
        secret_path = cfg.get("mgmt_secret_path", "cfg-admin")
        parsed = urlparse(self.path)
        path = parsed.path
        expected_prefix = f"/{secret_path}/"

        if not path.startswith(expected_prefix):
            self.send_json({"error": "Not Found"}, 404)
            return

        rel_path = path[len(expected_prefix):].rstrip("/")

        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8") if content_len > 0 else "{}"
        try:
            req_data = json.loads(body)
        except Exception:
            req_data = {}

        # 1. API: Login / Authentication
        if rel_path == "api/auth":
            ip = self.get_client_ip()
            now = time.time()
            if ip in FAILED_ATTEMPTS:
                if now < FAILED_ATTEMPTS[ip].get("blocked_until", 0):
                    self.send_json({"success": False, "error": "Слишком много попыток. Подождите 15 минут."}, 429)
                    return

            user = req_data.get("user", "")
            password = req_data.get("pass", "")

            expected_user = cfg.get("auth_user", "admin")
            expected_hash = cfg.get("auth_pass_hash", "")

            # Verify password using PBKDF2 or direct compare
            salt = b"server_manager_salt_3x"
            input_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000).hex()

            is_valid = (user == expected_user) and (
                secrets.compare_digest(input_hash, expected_hash) or
                secrets.compare_digest(password, cfg.get("auth_pass_plain", ""))
            )

            if is_valid:
                FAILED_ATTEMPTS.pop(ip, None)
                token = secrets.token_hex(32)
                SESSIONS[token] = {
                    "user": user,
                    "expires": now + (12 * 3600)  # 12 hours session
                }
                cookie = f"mgmt_session={token}; Path=/{secret_path}/; HttpOnly; SameSite=Strict; Max-Age=43200"
                self.send_json({"success": True}, code=200, set_cookie=cookie)
            else:
                entry = FAILED_ATTEMPTS.setdefault(ip, {"count": 0, "blocked_until": 0})
                entry["count"] += 1
                if entry["count"] >= 5:
                    entry["blocked_until"] = now + 900
                self.send_json({"success": False, "error": "Неверное имя пользователя или пароль."}, 401)
            return

        # Check authentication for remaining endpoints
        if not self.is_authenticated():
            self.send_json({"success": False, "error": "Unauthorized"}, 401)
            return

        # 2. API: Apply changes
        if rel_path == "api/apply":
            s_id = req_data.get("service_id")
            n_port = req_data.get("new_port")
            n_path = req_data.get("new_path")
            ok, msg = apply_service_changes(s_id, n_port, n_path, cfg)
            self.send_json({"success": ok, "message" if ok else "error": msg})
            return

        # 3. API: Restart Stack
        if rel_path == "api/restart-stack":
            subprocess.run(["systemctl", "restart", "nginx", "x-ui", "telemt", "AdGuardHome"], check=False)
            self.send_json({"success": True})
            return

        # 4. API: Logout & Lock Firewall
        if rel_path == "api/logout":
            token = self.get_cookie("mgmt_session")
            if token and token in SESSIONS:
                del SESSIONS[token]
            def delayed_lock():
                time.sleep(1.5)
                lock_firewall()
            t = threading.Thread(target=delayed_lock, daemon=True)
            t.start()
            self.send_json({"success": True})
            return

        # 5. API: Change Auth Credentials
        if rel_path == "api/settings/auth":
            new_user = req_data.get("user", "").strip()
            new_pass = req_data.get("pass", "")

            if not new_user:
                self.send_json({"success": False, "error": "Имя пользователя не может быть пустым."}, 400)
                return

            if new_pass and len(new_pass) < 8:
                self.send_json({"success": False, "error": "Пароль должен содержать минимум 8 символов."}, 400)
                return

            cfg["auth_user"] = new_user
            msg = "Имя пользователя успешно обновлено!"

            if new_pass:
                # Compute PBKDF2 hash
                salt = b"server_manager_salt_3x"
                new_hash = hashlib.pbkdf2_hmac("sha256", new_pass.encode("utf-8"), salt, 100000).hex()
                cfg["auth_pass_hash"] = new_hash
                cfg["auth_pass_plain"] = new_pass
                msg = "Логин и пароль успешно обновлены!"

            save_master_config(cfg)

            # Update credentials file if present
            if os.path.exists(CREDENTIALS_FILE):
                try:
                    with open(CREDENTIALS_FILE, "r") as cf:
                        cred_text = cf.read()
                    cred_text = re.sub(r'(\[0\] МОДУЛЬ[\s\S]*?Логин:\s*)[^\n]+', f'\\g<1>{new_user}', cred_text, count=1)
                    if new_pass:
                        cred_text = re.sub(r'(\[0\] МОДУЛЬ[\s\S]*?Пароль:\s*)[^\n]+', f'\\g<1>{new_pass}', cred_text, count=1)
                    with open(CREDENTIALS_FILE, "w") as cf:
                        cf.write(cred_text)
                except Exception as e:
                    print(f"[WARN] Failed to update {CREDENTIALS_FILE}: {e}")

            self.send_json({"success": True, "message": msg})
            return

        self.send_json({"error": "Not Found"}, 404)


# ==============================================================================
# [7] DAEMON ENTRYPOINT & CLI SUPPORT
# ==============================================================================

def run_server():
    cfg = load_master_config()
    port = int(cfg.get("mgmt_port", 54892))
    domain = cfg.get("domain", "example.com")

    # Start firewall monitor thread
    t = threading.Thread(target=firewall_monitor_thread, daemon=True)
    t.start()

    server_address = ("0.0.0.0", port)
    httpd = ThreadingHTTPServer(server_address, ServerManagerHandler)
    httpd.daemon_threads = True

    cert_file = f"/etc/letsencrypt/live/{domain}/fullchain.pem"
    key_file = f"/etc/letsencrypt/live/{domain}/privkey.pem"

    # HTTPS SSL wrapping
    if os.path.exists(cert_file) and os.path.exists(key_file):
        try:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            ctx.load_cert_chain(certfile=cert_file, keyfile=key_file)
            httpd.socket = ctx.wrap_socket(httpd.socket, server_side=True)
            print(f"[✓] Server Manager HTTPS active on port {port}")
        except Exception as e:
            print(f"[!] Warning: SSL wrapping failed: {e}. Running on HTTP.")
    else:
        print(f"[!] SSL certs not found at {cert_file}. Running on plain HTTP.")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        cmd = sys.argv[1].lower()
        if cmd == "open":
            mins = int(sys.argv[2]) if len(sys.argv) > 2 else 30
            unlock_firewall(mins)
        elif cmd == "close":
            lock_firewall()
        elif cmd == "status":
            cfg = load_master_config()
            print(f"Domain: {cfg.get('domain')}")
            print(f"Port: {cfg.get('mgmt_port')}")
            print(f"Secret Path: /{cfg.get('mgmt_secret_path')}/")
            timer_active = os.path.exists(AUTO_LOCK_FILE)
            if timer_active:
                try:
                    with open(AUTO_LOCK_FILE, "r") as f:
                        exp = float(f.read().strip())
                    rem = int(exp - time.time())
                    print(f"Auto-Lock: active ({max(0, rem // 60)} min {max(0, rem % 60)} sec remaining)")
                except Exception:
                    print("Auto-Lock: active")
            else:
                print("Auto-Lock: inactive (permanent open or locked)")
        elif cmd in ("set-user", "set-auth", "passwd"):
            if len(sys.argv) < 3:
                print("Использование: server-manager.py set-user <новый_логин> [новый_пароль]")
                sys.exit(1)
            new_u = sys.argv[2].strip()
            new_p = sys.argv[3] if len(sys.argv) > 3 else None
            if not new_u:
                print("[!] Ошибка: логин не может быть пустым.")
                sys.exit(1)
            cfg = load_master_config()
            cfg["auth_user"] = new_u
            if new_p:
                if len(new_p) < 8:
                    print("[!] Ошибка: пароль должен содержать не менее 8 символов.")
                    sys.exit(1)
                salt = b"server_manager_salt_3x"
                new_h = hashlib.pbkdf2_hmac("sha256", new_p.encode("utf-8"), salt, 100000).hex()
                cfg["auth_pass_hash"] = new_h
                cfg["auth_pass_plain"] = new_p
            save_master_config(cfg)
            if os.path.exists(CREDENTIALS_FILE):
                try:
                    with open(CREDENTIALS_FILE, "r") as cf:
                        ct = cf.read()
                    ct = re.sub(r'(\[0\] МОДУЛЬ[\s\S]*?Логин:\s*)[^\n]+', f'\\g<1>{new_u}', ct, count=1)
                    if new_p:
                        ct = re.sub(r'(\[0\] МОДУЛЬ[\s\S]*?Пароль:\s*)[^\n]+', f'\\g<1>{new_p}', ct, count=1)
                    with open(CREDENTIALS_FILE, "w") as cf:
                        cf.write(ct)
                except Exception as e:
                    print(f"[!] Предупреждение при обновлении {CREDENTIALS_FILE}: {e}")
            if new_p:
                print(f"[✓] Логин изменен на '{new_u}', пароль обновлен.")
            else:
                print(f"[✓] Логин успешно изменен на '{new_u}' (пароль сохранен прежним).")
        else:
            print("Usage: server-manager.py [run|open <minutes>|close|status|set-user <username> [password]]")
    else:
        run_server()
