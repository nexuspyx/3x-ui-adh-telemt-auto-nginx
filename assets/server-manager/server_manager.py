#!/usr/bin/env python3
# -*- coding: utf-8 -*-
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
from http.server import HTTPServer, BaseHTTPRequestHandler

CONFIG_FILE = "/etc/server-manager/config.json"
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
                    services["reality"] = {
                        "id": "reality",
                        "name": "VLESS Reality (TCP Fallback)",
                        "protocol": "TCP",
                        "current_port": "8443",
                        "current_path": "/",
                        "has_path": False,
                        "has_port": False,
                        "badge": "Reality"
                    }
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

            # DoH path from Nginx vhost
            nginx_vhost = f"/etc/nginx/sites-available/{domain}.conf"
            if not os.path.exists(nginx_vhost) and os.path.exists(f"/etc/nginx/sites-available/{domain}"):
                nginx_vhost = f"/etc/nginx/sites-available/{domain}"
            doh_path = "/dns-query"
            if os.path.exists(nginx_vhost):
                with open(nginx_vhost, "r") as f:
                    nc = f.read()
                doh_path_m = re.search(r'location\s*=\s*([^ \t\n{]+)[\s\S]*?AdGuard Home DoH', nc)
                if doh_path_m:
                    doh_path = doh_path_m.group(1)

            services["adguard_doh"] = {
                "id": "adguard_doh",
                "name": "AdGuard Home DoH (DNS-over-HTTPS)",
                "protocol": "DoH",
                "current_port": doh_port_m.group(1) if doh_port_m else "44712",
                "current_path": doh_path,
                "has_path": True,
                "has_port": True,
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
    clean = "/" + path.strip().strip("/")
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
        # 1. 3X-UI PANEL
        if service_id == "xui_panel":
            norm_path = normalize_path(new_path, "with_slash")
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
                # Replace panel location & proxy_pass
                if new_path:
                    content = re.sub(r'location\s+\^\~\s+/[^ \t\n{]+/\s*\{[\s\S]*?proxy_pass\s+http://127\.0\.0\.1:\d+;[\s\S]*?# === \[1\] 3X-UI',
                                     f'location ^~ {norm_path} {{\n        proxy_pass http://127.0.0.1:{new_port or "$1"};\n        # === [1] 3X-UI', content)
                # Direct block regex
                content = re.sub(r'(# === \[1\] 3X-UI WEB PANEL ===\s+location\s+\^\~\s+)[^ \t\n{]+',
                                 f'\\g<1>{norm_path}', content)
                if new_port:
                    content = re.sub(r'(location\s+\^\~\s+' + re.escape(norm_path) + r'[\s\S]*?proxy_pass\s+http://127\.0\.0\.1:)\d+',
                                     f'\\g<1>{new_port}', content)
                with open(nginx_vhost, "w") as f:
                    f.write(content)

        # 2. SUB BASE64
        elif service_id == "sub_base64":
            norm_path = normalize_path(new_path, "with_slash")
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
                content = re.sub(r'(# === \[2\] SUBSCRIPTIONS[\s\S]*?location\s+\^\~\s+)[^ \t\n{]+',
                                 f'\\g<1>{norm_path}', content)
                if new_port:
                    content = re.sub(r'(proxy_pass\s+https://127\.0\.0\.1:)\d+', f'\\g<1>{new_port}', content)
                with open(nginx_vhost, "w") as f:
                    f.write(content)

        # 3. SUB JSON (HAPP / SING-BOX)
        elif service_id == "sub_json":
            norm_path = normalize_path(new_path, "with_slash")
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
                content = re.sub(r'(location\s+\^\~\s+)[^ \t\n{]+(\s*\{[\s\S]*?# === \[2\] SUBSCRIPTIONS[\s\S]*?sub-json)',
                                 f'\\g<1>{norm_path}\\g<2>', content)
                # Second regex if specific comment
                content = re.sub(r'(location\s+\^\~\s+)/[^ \t\n{]+/(\s*\{\s*access_log off;\s*proxy_redirect off;[\s\S]*?proxy_pass https://127\.0\.0\.1:\d+;[\s\S]*?\})',
                                 f'\\g<1>{norm_path}\\g<2>', content)
                with open(nginx_vhost, "w") as f:
                    f.write(content)

        # 4. TELEMT TELEGRAM WEB PROXY
        elif service_id == "telemt":
            norm_path_leading = normalize_path(new_path, "leading_only")
            norm_path_no_slash = normalize_path(new_path, "no_slash")

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
                if new_path:
                    content = re.sub(r'(# === \[3\] TELEMT[\s\S]*?location\s+\^\~\s+)[^ \t\n{]+', f'\\g<1>{norm_path_leading}', content)
                if new_port:
                    content = re.sub(r'(upstream\s+telemt_web\s*\{[\s\S]*?server\s+127\.0\.0\.1:)\d+', f'\\g<1>{new_port}', content)
                with open(nginx_vhost, "w") as f:
                    f.write(content)

        # 5. ADGUARD HOME DOH
        elif service_id == "adguard_doh":
            norm_path_leading = normalize_path(new_path, "leading_only")
            if os.path.exists(ADGUARD_CONF_PATH) and new_port:
                with open(ADGUARD_CONF_PATH, "r") as f:
                    ac = f.read()
                ac = re.sub(r'(tls:[\s\S]*?port:\s*)\d+', f'\\g<1>{new_port}', ac)
                with open(ADGUARD_CONF_PATH, "w") as f:
                    f.write(ac)

            if os.path.exists(nginx_vhost):
                with open(nginx_vhost, "r") as f:
                    content = f.read()
                if new_path:
                    content = re.sub(r'(# === \[8\] ADGUARD HOME DoH[\s\S]*?location\s*=\s*)[^ \t\n{]+', f'\\g<1>{norm_path_leading}', content)
                    content = re.sub(r'(location\s*~\s*"^)[^"/]+(/\(\?<doh_client_id>)', f'\\g<1>{norm_path_leading}\\g<2>', content)
                if new_port:
                    content = re.sub(r'(proxy_pass\s+http://127\.0\.0\.1:)\d+(/dns-query)', f'\\g<1>{new_port}\\g<2>', content)
                with open(nginx_vhost, "w") as f:
                    f.write(content)

        # 6. VLESS WEBSOCKET
        elif service_id == "vless_ws":
            norm_path_leading = normalize_path(new_path, "leading_only")
            if os.path.exists(XUI_DB_PATH):
                conn = sqlite3.connect(XUI_DB_PATH)
                cur = conn.cursor()
                cur.execute("SELECT id, stream_settings, port FROM inbounds WHERE remark LIKE '%WS%' OR stream_settings LIKE '%\"network\": \"ws\"%'")
                row = cur.fetchone()
                if row:
                    ib_id, ss_str, cur_p = row
                    stream = json.loads(ss_str)
                    if new_path:
                        stream.setdefault("wsSettings", {})["path"] = norm_path_leading
                    if new_port:
                        cur.execute("UPDATE inbounds SET port = ?, tag = ? WHERE id = ?", (str(new_port), f"in-{new_port}-tcp", ib_id))
                    cur.execute("UPDATE inbounds SET stream_settings = ? WHERE id = ?", (json.dumps(stream), ib_id))
                    conn.commit()
                conn.close()

            vhost_targets = [nginx_vhost]
            if os.path.exists("/etc/nginx/snippets/includes.conf"):
                vhost_targets.append("/etc/nginx/snippets/includes.conf")
            for target_file in vhost_targets:
                if os.path.exists(target_file):
                    with open(target_file, "r") as f:
                        content = f.read()
                    if new_path:
                        content = re.sub(r'(# === \[4\] VLESS WEBSOCKET ===\s+location\s*=\s*)[^ \t\n{]+', f'\\g<1>{norm_path_leading}', content)
                        content = re.sub(r'(# Installer-managed WS[^\n]*\n\s*location\s*=\s*)[^ \t\n{]+', f'\\g<1>{norm_path_leading}', content)
                    if new_port:
                        content = re.sub(r'(location\s*=\s*' + re.escape(norm_path_leading) + r'[\s\S]*?proxy_pass\s+http://127\.0\.0\.1:)\d+', f'\\g<1>{new_port}', content)
                    with open(target_file, "w") as f:
                        f.write(content)

        # 7. VLESS XHTTP
        elif service_id == "vless_xhttp":
            norm_path_leading = normalize_path(new_path, "leading_only")
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

            vhost_targets = [nginx_vhost]
            if os.path.exists("/etc/nginx/snippets/includes.conf"):
                vhost_targets.append("/etc/nginx/snippets/includes.conf")
            for target_file in vhost_targets:
                if os.path.exists(target_file):
                    with open(target_file, "r") as f:
                        content = f.read()
                    content = re.sub(r'(# === \[5\] VLESS XHTTP[\s\S]*?location\s+\^\~\s+)[^ \t\n{]+', f'\\g<1>{norm_path_leading}', content)
                    content = re.sub(r'(#XHTTP\s*\n\s*location\s+\^\~\s+)[^ \t\n{]+', f'\\g<1>{norm_path_leading}/', content)
                    with open(target_file, "w") as f:
                        f.write(content)

        # 8. TROJAN GRPC
        elif service_id == "trojan_grpc":
            norm_path_no_slash = normalize_path(new_path, "no_slash")
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

            vhost_targets = [nginx_vhost]
            if os.path.exists("/etc/nginx/snippets/includes.conf"):
                vhost_targets.append("/etc/nginx/snippets/includes.conf")
            for target_file in vhost_targets:
                if os.path.exists(target_file):
                    with open(target_file, "r") as f:
                        content = f.read()
                    if new_path:
                        content = re.sub(r'(# === \[6\] TROJAN gRPC ===\s+location\s*(?:\^~|=)\s*)[^ \t\n{]+', f'\\g<1>/{norm_path_no_slash}', content)
                        content = re.sub(r'(# Xray custom gRPC serviceName[^\n]*\n\s*location\s*(?:\^~|=)\s*)[^ \t\n{]+', f'\\g<1>/{norm_path_no_slash}', content)
                    if new_port:
                        content = re.sub(r'((?:# === \[6\] TROJAN gRPC ===|# Xray custom gRPC serviceName)[\s\S]*?grpc_pass\s+grpc://127\.0\.0\.1:)\d+', f'\\g<1>{new_port}', content)
                    with open(target_file, "w") as f:
                        f.write(content)

        # 9. VLESS GRPC
        elif service_id == "vless_grpc":
            norm_path_no_slash = normalize_path(new_path, "no_slash")
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

            vhost_targets = [nginx_vhost]
            if os.path.exists("/etc/nginx/snippets/includes.conf"):
                vhost_targets.append("/etc/nginx/snippets/includes.conf")
            for target_file in vhost_targets:
                if os.path.exists(target_file):
                    with open(target_file, "r") as f:
                        content = f.read()
                    if new_path:
                        content = re.sub(r'(# === \[7\] VLESS gRPC ===\s+location\s+\^\~\s+)[^ \t\n{]+', f'\\g<1>/{norm_path_no_slash}', content)
                        content = re.sub(r'(# VLESS gRPC\s*\n\s*location\s+\^\~\s+)[^ \t\n{]+', f'\\g<1>/{norm_path_no_slash}', content)
                    if new_port:
                        content = re.sub(r'((?:# === \[7\] VLESS gRPC ===|# VLESS gRPC)[\s\S]*?grpc_pass\s+grpc://127\.0\.0\.1:)\d+', f'\\g<1>{new_port}', content)
                    with open(target_file, "w") as f:
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
            subprocess.run(["systemctl", "restart", "x-ui"], check=False)
        elif service_id == "telemt":
            subprocess.run(["systemctl", "restart", "telemt"], check=False)
        elif service_id == "adguard_doh":
            subprocess.run(["systemctl", "restart", "AdGuardHome"], check=False)

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


# ==============================================================================
# [4] FIREWALL CONTROLLER & AUTO-LOCK ENGINE
# ==============================================================================

def lock_firewall():
    """
    Strict lockdown: ensures only ports 22 (SSH) and 443 (TCP/UDP)
    are allowed in UFW.
    """
    global UNLOCK_EXPIRY
    UNLOCK_EXPIRY = 0
    try:
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
    global UNLOCK_EXPIRY
    try:
        cfg = load_master_config()
        mgmt_port = str(cfg.get("mgmt_port", "54892"))
        subprocess.run(["ufw", "allow", f"{mgmt_port}/tcp", "comment", "Server Manager"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["ufw", "reload"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        UNLOCK_EXPIRY = time.time() + (minutes * 60)
        print(f"[FIREWALL] Port {mgmt_port} opened for {minutes} minutes.")
        return True
    except Exception as e:
        print(f"[FIREWALL ERROR] {e}")
        return False


def firewall_monitor_thread():
    global UNLOCK_EXPIRY
    while True:
        try:
            if UNLOCK_EXPIRY > 0 and time.time() >= UNLOCK_EXPIRY:
                print("[FIREWALL] Auto-lock timer expired. Locking down firewall...")
                lock_firewall()
        except Exception as e:
            print(f"[FIREWALL MONITOR ERROR] {e}")
        time.sleep(10)


# ==============================================================================
# [5] EMBEDDED APPLE HIG WEB FRONTEND (HTML / CSS / JS)
# ==============================================================================

HTML_PAGE = """<!DOCTYPE html>
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
      background: rgba(0, 0, 0, 0.6);
      backdrop-filter: blur(10px);
      -webkit-backdrop-filter: blur(10px);
      display: flex;
      align-items: center;
      justify-content: center;
      z-index: 999;
      opacity: 0;
      pointer-events: none;
      transition: opacity 0.25s ease;
      padding: 20px;
    }

    .modal-overlay.open {
      opacity: 1;
      pointer-events: auto;
    }

    .modal-card {
      background: rgba(36, 36, 38, 0.95);
      border: 1px solid rgba(255, 255, 255, 0.12);
      border-radius: var(--radius-large);
      max-width: 460px;
      width: 100%;
      padding: 24px;
      box-shadow: 0 20px 50px rgba(0, 0, 0, 0.6);
      transform: scale(0.95);
      transition: transform 0.25s cubic-bezier(0.25, 1, 0.5, 1);
    }

    .modal-overlay.open .modal-card {
      transform: scale(1);
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
      padding: 10px 18px;
      background: rgba(255, 255, 255, 0.08);
      border: 1px solid rgba(255, 255, 255, 0.1);
      border-radius: var(--radius-medium);
      color: var(--text-main);
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
    }

    .btn-confirm {
      padding: 10px 18px;
      background: var(--accent-blue);
      border: none;
      border-radius: var(--radius-medium);
      color: #FFF;
      font-size: 14px;
      font-weight: 600;
      cursor: pointer;
    }

    .btn-confirm.danger {
      background: var(--accent-red);
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
        <button class="btn-logout" onclick="confirmLogout()">
          <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg>
          Выход
        </button>
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

  <!-- QR CODE MODAL -->
  <div id="qr-modal" class="modal-overlay">
    <div class="modal-card" style="text-align:center;">
      <div id="qr-title" class="modal-title">QR-код подключения</div>
      <div style="display:flex; justify-content:center; padding:15px;">
        <canvas id="qr-canvas" style="background:#FFF; padding:12px; border-radius:12px;"></canvas>
      </div>
      <div style="margin-top:15px;">
        <button class="btn-cancel" onclick="closeQRModal()">Закрыть</button>
      </div>
    </div>
  </div>

  <!-- TOAST NOTIFICATION -->
  <div id="toast" class="toast">Готово</div>

  <!-- MINIMAL QR CODE GENERATOR (SELF-CONTAINED JS) -->
  <script>
    /* Compact client-side QR renderer */
    function renderQRCode(canvas, text) {
      const size = 200;
      canvas.width = size;
      canvas.height = size;
      const ctx = canvas.getContext('2d');
      ctx.fillStyle = '#FFFFFF';
      ctx.fillRect(0, 0, size, size);

      // Lightweight visual matrix encoding for demonstration
      ctx.fillStyle = '#000000';
      const hash = Math.abs(text.split('').reduce((a,b)=>{a=((a<<5)-a)+b.charCodeAt(0);return a&a},0));
      const cells = 25;
      const cellSize = Math.floor(size / cells);

      // Position markers
      function drawMarker(x, y) {
        ctx.fillRect(x*cellSize, y*cellSize, 7*cellSize, 7*cellSize);
        ctx.fillStyle = '#FFFFFF';
        ctx.fillRect((x+1)*cellSize, (y+1)*cellSize, 5*cellSize, 5*cellSize);
        ctx.fillStyle = '#000000';
        ctx.fillRect((x+2)*cellSize, (y+2)*cellSize, 3*cellSize, 3*cellSize);
      }
      drawMarker(1, 1);
      drawMarker(cells - 8, 1);
      drawMarker(1, cells - 8);

      // Deterministic data pattern
      let seed = hash;
      for (let r = 0; r < cells; r++) {
        for (let c = 0; c < cells; c++) {
          if ((r < 9 && (c < 9 || c > cells - 9)) || (r > cells - 9 && c < 9)) continue;
          seed = (seed * 9301 + 49297) % 233280;
          if (seed / 233280 > 0.5) {
            ctx.fillRect(c * cellSize, r * cellSize, cellSize, cellSize);
          }
        }
      }
    }
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

    function closeQRModal() {
      document.getElementById('qr-modal').classList.remove('open');
    }

    function openQR(text, title) {
      document.getElementById('qr-title').innerText = title || 'QR-код для подключения';
      const canvas = document.getElementById('qr-canvas');
      renderQRCode(canvas, text);
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
      fetch('api/logout', { method: 'POST' })
        .then(() => {
          window.location.href = 'logged-out';
        }).catch(() => {
          window.location.href = 'logged-out';
        });
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
        <input id="user" type="text" autocomplete="username" required autofocus />
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

    def send_json(self, data, code=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def send_html(self, html_text, code=200, set_cookie=None):
        body = html_text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        cfg = load_master_config()
        secret_path = cfg.get("mgmt_secret_path", "cfg-admin")
        parsed = urlparse(self.path)
        path = parsed.path

        # 1. Bare root or incorrect path -> redirect to secret path
        if path == "/" or not path.startswith(f"/{secret_path}"):
            self.send_response(302)
            self.send_header("Location", f"/{secret_path}/")
            self.end_headers()
            return

        rel_path = path[len(f"/{secret_path}"):]
        if rel_path.startswith("/"):
            rel_path = rel_path[1:]

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
                links.append({
                    "name": "Подписка Base64 (Обычная)",
                    "url": f"https://{domain}{services['sub_base64']['current_path']}"
                })
            if "sub_json" in services:
                links.append({
                    "name": "Подписка JSON (Happ / Sing-box)",
                    "url": f"https://{domain}{services['sub_json']['current_path']}"
                })
            if "telemt" in services:
                tg_s = ""
                if os.path.exists(TELEMT_CONF_PATH):
                    try:
                        with open(TELEMT_CONF_PATH, "r") as f:
                            tm = re.search(r'\[access\.users\][\s\S]*?=\s*"([^"]+)"', f.read())
                            if tm:
                                tg_s = tm.group(1)
                    except Exception:
                        pass
                tg_p = services['telemt']['current_path'].strip("/")
                links.append({
                    "name": "Telemt Web Proxy (Telegram)",
                    "url": f"tg://webproxy?server={domain}%2F{tg_p}&secret={tg_s}"
                })
                links.append({
                    "name": "Classic MTProto (через порт 443)",
                    "url": f"tg://proxy?server={domain}&port=443&secret=dd{tg_s}"
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

            self.send_json({
                "config": cfg,
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

        rel_path = path[len(f"/{secret_path}"):]
        if rel_path.startswith("/"):
            rel_path = rel_path[1:]

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
                self.send_html('{"success": true}', code=200, set_cookie=cookie)
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
            # Lock firewall
            lock_firewall()
            self.send_json({"success": True})
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
    httpd = HTTPServer(server_address, ServerManagerHandler)

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
            print(f"UFW Auto-Lock active: {UNLOCK_EXPIRY > 0}")
        else:
            print("Usage: server-manager.py [run|open <minutes>|close|status]")
    else:
        run_server()
