#!/usr/bin/env python3
"""
Синхронизация Xray Core (3x-ui) и AdGuard Home DNS.
Выполняет безопасный идемпотентный JSON-патчинг шаблона xrayTemplateConfig в базе SQLite.
Generated: 21:32.29.10.2026
"""
import json
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path


def get_default_xray_template():
    """Возвращает стандартный шаблон конфигурации Xray для 3x-ui, если он еще не инициализирован в базе."""
    return {
        "log": {
            "access": "",
            "error": "",
            "loglevel": "warning",
            "dnsLog": False,
        },
        "api": {
            "tag": "api",
            "services": [
                "HandlerService",
                "LoggerService",
                "StatsService",
            ],
        },
        "inbounds": [
            {
                "tag": "api",
                "port": 62789,
                "listen": "127.0.0.1",
                "protocol": "dokodemo-door",
                "settings": {
                    "address": "127.0.0.1",
                },
            }
        ],
        "outbounds": [
            {
                "tag": "direct",
                "protocol": "freedom",
                "settings": {
                    "domainStrategy": "UseIPv4",
                },
            },
            {
                "tag": "blocked",
                "protocol": "blackhole",
                "settings": {},
            },
        ],
        "policy": {
            "levels": {
                "0": {
                    "statsUserUplink": True,
                    "statsUserDownlink": True,
                }
            },
            "system": {
                "statsInboundUplink": True,
                "statsInboundDownlink": True,
                "statsOutboundUplink": True,
                "statsOutboundDownlink": True,
            },
        },
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                {
                    "type": "field",
                    "inboundTag": [
                        "api"
                    ],
                    "outboundTag": "api",
                },
                {
                    "type": "field",
                    "ip": [
                        "geoip:private"
                    ],
                    "outboundTag": "blocked",
                },
                {
                    "type": "field",
                    "protocol": [
                        "bittorrent"
                    ],
                    "outboundTag": "blocked",
                },
            ],
        },
        "stats": {},
    }


def get_adh_dns_port(yaml_path="/opt/AdGuardHome/AdGuardHome.yaml", default=57998):
    """Автоматическое определение локального DNS-порта из секции dns в AdGuardHome.yaml."""
    p = Path(yaml_path)
    if p.is_file():
        try:
            text = p.read_text(encoding="utf-8")
            match = re.search(r'^dns:\s*\n((?:[ \t].*\n|\n)*)', text, re.M)
            if match:
                dns_sec = match.group(1)
                port_match = re.search(r'^\s*port:\s*(\d+)', dns_sec, re.M)
                if port_match and int(port_match.group(1)) > 0:
                    return int(port_match.group(1))
        except Exception:
            pass
    return default


def patch_xui_dns(db_path="/etc/x-ui/x-ui.db", dns_port=None):
    """
    Точечно обновляет шаблон Xray в 3x-ui:
    - Задает 127.0.0.1:<dns_port> в качестве основного DNS Xray
    - Устанавливает domainStrategy: "UseIPv4" в freedom outbound
    - Добавляет разрешение опроса 127.0.0.1:<dns_port> перед блокировкой geoip:private
    - Направляет запросы к 53 порту в dns-out
    - Включает sniffing для http, tls, quic
    """
    dns_port = dns_port or get_adh_dns_port()
    db = Path(db_path)
    if not db.is_file():
        raise FileNotFoundError(f"База данных {db_path} не найдена")

    # Резервная копия базы перед внесением изменений
    backup_path = f"{db}.bak_{int(time.time())}"
    shutil.copy2(db, backup_path)
    print(f"[INFO] Создана резервная копия базы: {backup_path}")

    with sqlite3.connect(db) as conn:
        cur = conn.cursor()
        cur.execute("SELECT value FROM settings WHERE key=?", ("xrayTemplateConfig",))
        row = cur.fetchone()
        if not row or not row[0]:
            print("[INFO] xrayTemplateConfig отсутствует в таблице settings — инициализируем шаблон по умолчанию")
            cfg = get_default_xray_template()
        else:
            try:
                cfg = json.loads(row[0])
            except Exception:
                print("[WARN] Не удалось распарсить существующий xrayTemplateConfig — используется шаблон по умолчанию")
                cfg = get_default_xray_template()

        # 1. Секция DNS
        cfg.setdefault("dns", {})
        cfg["dns"]["tag"] = "dns_inbound"
        cfg["dns"]["queryStrategy"] = "UseIP"
        cfg["dns"]["servers"] = [
            {
                "address": "127.0.0.1",
                "port": int(dns_port),
                "domains": [],
                "expectedIPs": [],
                "unexpectedIPs": [],
                "queryStrategy": "UseIP",
                "skipFallback": False,
                "disableCache": False,
                "finalQuery": False,
                "serveStale": False,
                "serveExpiredTTL": 0,
                "timeoutMs": 4000,
            }
        ]

        # 2. Секция Outbounds (freedom -> direct)
        outbounds = cfg.setdefault("outbounds", [])
        dns_out_found = False

        for ob in outbounds:
            if ob.get("tag") == "direct" and ob.get("protocol") == "freedom":
                ob.setdefault("settings", {})
                ob["settings"]["domainStrategy"] = "UseIPv4"
                final_rules = ob["settings"].get("finalRules", [])

                # Разрешаем опрос 127.0.0.1:<PORT> ПЕРЕД правилом блокировки geoip:private
                has_allow = any(
                    r.get("action") == "allow"
                    and "127.0.0.1" in r.get("ip", [])
                    and str(r.get("port", "")) == str(dns_port)
                    for r in final_rules
                )
                if not has_allow:
                    final_rules.insert(
                        0,
                        {
                            "action": "allow",
                            "ip": ["127.0.0.1"],
                            "port": str(dns_port),
                        },
                    )
                ob["settings"]["finalRules"] = final_rules

            elif ob.get("tag") == "dns-out" and ob.get("protocol") == "dns":
                dns_out_found = True

        if not dns_out_found:
            outbounds.append({"tag": "dns-out", "protocol": "dns"})

        # 3. Секция Routing Rules
        routing = cfg.setdefault("routing", {})
        routing["domainStrategy"] = "IPIfNonMatch"
        rules = routing.setdefault("rules", [])

        # Удаляем старые правила при повторном запуске (идемпотентность)
        rules = [
            r
            for r in rules
            if not (
                r.get("outboundTag") == "direct"
                and "127.0.0.1" in r.get("ip", [])
                and str(dns_port) in str(r.get("port", ""))
            )
            and not (
                r.get("outboundTag") == "dns-out"
                and "53" in str(r.get("port", ""))
            )
        ]

        # Вставляем правила перехвата после системных API правил (индекс 1)
        rules.insert(1, {"type": "field", "port": "53", "outboundTag": "dns-out"})
        rules.insert(
            1,
            {
                "type": "field",
                "ip": ["127.0.0.1"],
                "port": str(dns_port),
                "outboundTag": "direct",
            },
        )
        routing["rules"] = rules

        # Сохранение обновленного шаблона
        cur.execute("DELETE FROM settings WHERE key=?", ("xrayTemplateConfig",))
        cur.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)",
            ("xrayTemplateConfig", json.dumps(cfg, indent=2, ensure_ascii=False)),
        )

        # 4. Обновление Sniffing на всех Inbounds (http, tls, quic)
        try:
            cur.execute("SELECT id, sniffing FROM inbounds")
            for iid, sn in cur.fetchall():
                try:
                    data = json.loads(sn)
                    if data.get("enabled"):
                        dest = data.get("destOverride", [])
                        for proto in ["http", "tls", "quic"]:
                            if proto not in dest:
                                dest.append(proto)
                        data["destOverride"] = dest
                        cur.execute(
                            "UPDATE inbounds SET sniffing=? WHERE id=?",
                            (json.dumps(data), iid),
                        )
                except Exception:
                    pass
        except sqlite3.OperationalError:
            pass

        conn.commit()
    print(f"[SUCCESS] DNS AdGuard Home (порт {dns_port}) успешно интегрирован в {db_path}")


if __name__ == "__main__":
    db_file = sys.argv[1] if len(sys.argv) > 1 and sys.argv[1] else "/etc/x-ui/x-ui.db"
    port_arg = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else None
    patch_xui_dns(db_file, port_arg)
