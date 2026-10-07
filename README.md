<div align="center">

# 🚀 3x-ui Auto Nginx

### Автоматическое развёртывание 3x-ui / Xray на собственном VPS

**REALITY · XHTTP · Hysteria2 · WebSocket · gRPC · nginx · HTTPS · Backup / Restore**

Два домена, чистый VPS и несколько минут на установку.

[![XHTTP / security](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-xhttp.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-xhttp.yml)
[![Backup / Restore](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-backup.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-backup.yml)
[![Ubuntu](https://img.shields.io/badge/Ubuntu-24.04%20%7C%2026.04-E95420?logo=ubuntu&logoColor=white)](#technical-details)
[![Releases](https://img.shields.io/github/v/release/xPROMSx/3x-ui-auto-nginx)](https://github.com/xPROMSx/3x-ui-auto-nginx/releases)

[English](README_EN.md) · [Релизы](https://github.com/xPROMSx/3x-ui-auto-nginx/releases) · [Telegram Web Proxy Manager](#telemt-web-manager) · [Ошибки](https://github.com/xPROMSx/3x-ui-auto-nginx/issues)

</div>

**3x-ui Auto Nginx** устанавливает [3x-ui](https://github.com/MHSanaei/3x-ui) и Xray, настраивает nginx, HTTPS, подключения, подписки, диагностику и Backup / Restore. AdGuard Home с DoH — по желанию.

Нужны два домена и чистый VPS. Сайт-прикрытие установщик выбирает и разворачивает автоматически.

## ✨ Почему этот проект

| | |
| --- | --- |
| **🚀 Готовые профили подключения**<br>REALITY, XHTTP, Hysteria2, WebSocket и Trojan gRPC уже настроены. | **🔐 Закрытые внутренние сервисы**<br>Панель и подписки не публикуют свои служебные порты напрямую в интернет. |
| **🌐 Безопасная схема nginx**<br>Нет проксирования запросов на произвольные локальные порты. | **💾 Backup / Restore v3**<br>Откат текущего VPS или восстановление после переустановки. |
| **♻️ Автоматические сертификаты**<br>Let's Encrypt выпускается и продлевается без ручной настройки. | **🛡️ AdGuard Home + DoH**<br>Устанавливается по желанию и не требует третьего домена. |

<a id="installation"></a>

## 🚀 Быстрый старт

Направьте **два домена** на чистый VPS: для панели и REALITY. Подключитесь по SSH под **root**. Откройте TCP **80/443** и UDP **443**; [поддерживаемые ОС](#technical-details).

```bash
curl -fSL https://raw.githubusercontent.com/xPROMSx/3x-ui-auto-nginx/main/x-ui-latest.sh -o x-ui-latest.sh
bash x-ui-latest.sh
```

Скрипт запросит домены. Их можно передать сразу:

```bash
bash x-ui-latest.sh -subdomain panel.example.com -reality_domain reality.example.com
```

> **⚠️ Чистая установка или полная переустановка.** `x-ui-latest.sh` удаляет прежнюю базу 3x-ui и конфигурацию nginx — **это не команда обновления рабочего VPS**. Перед переустановкой сохраните Backup вне сервера. Переустановка обнаруженной установки и удаление проекта требуют точного `YES` в верхнем регистре.

В конце — **URL панели, случайные логин/пароль и адрес диагностики** (MTR/LibreSpeed с авторизацией через 3x-ui). При выборе AdGuard Home — его адрес, пароль и DoH.

## 🚀 Профили подключения

| Подключение | Статус |
| --- | :---: |
| VLESS + REALITY | ✅ Готов к работе |
| VLESS + XHTTP | ✅ Готов к работе |
| Hysteria2 | ✅ Готов к работе |
| VLESS + WebSocket | ✅ Готов к работе |
| Trojan + gRPC | ✅ Готов к работе |

Все профили настроены установщиком: нужный можно включить в 3x-ui без настройки nginx. **Клиентов создайте в панели**; совместимость зависит от приложения и версии.

### 🔗 Подписки

Обычная подписка, JSON и **Mihomo / Clash** — через nginx и HTTPS. `provider=1` возвращает исходную подписку для proxy providers вместо полной конфигурации Clash.

## 🛡️ AdGuard Home + DoH

Установка по желанию, по умолчанию **N**. Третий домен не нужен: панель AdGuard Home — под случайным `/adg-.../`, DoH — `https://panel.example.com/dns-query`.

Установщик не открывает публичный TCP/UDP **53**. Конфигурация и данные AdGuard Home входят в **Backup / Restore v3**.

## 💾 Backup / Restore

Формат **v3**: откат текущего VPS или восстановление совместимого сервера после переустановки. Утилита уже установлена; выполняйте от root:

```bash
x-ui-backup backup
x-ui-backup list
x-ui-backup restore /var/backups/x-ui/<archive>.tar.gz
```

> **Архив содержит чувствительные данные:** базу клиентов, пароли и приватные ключи сертификатов. Копии из `/var/backups/x-ui/` храните **вне VPS**. Восстанавливайте только доверенные архивы этой утилиты, хранившиеся под вашим контролем: проверки выявляют повреждения и несовместимость, но не подтверждают происхождение архива.

Нужны **та же ОС, версия и архитектура**. Restore восстанавливает приложение, не всю ОС. [Инструкция для нового VPS](#technical-details).

## 🔐 Безопасность

- Внутренние сервисы не выставляются напрямую в интернет.
- Нет проксирования на произвольные локальные порты.
- 3x-ui и CLI — из одного проверенного SHA-256 архива.
- Критические ошибки останавливают установку и восстановление.
- После запуска проверяются процесс и сокеты Xray.
- Существующие правила и политика UFW сохраняются.

<a id="technical-details"></a>
<a id="-технические-подробности"></a>

<details>
<summary>⚙️ Технические подробности и совместимость</summary>

- **ОС:** Ubuntu 26.04 amd64 проверен на реальном VPS; Ubuntu 24.04 используется в CI. Debian 13 допускается без аналогичной проверки на VPS; Debian 12 не поддерживается. Проверка amd64 не означает, что все архитектуры проверены.
- **nginx / Xray:** TCP 80/443 принимает nginx, UDP 443 — Hysteria2. SNI направляет трафик в REALITY или HTTPS; внутренние сервисы доступны только локально, XHTTP использует Unix socket. Панель/API и подписки используют внутренний HTTPS. Multi-node через API token доступен через домен панели с проверкой TLS; прямой native panel mTLS не поддерживается этой схемой.
- **UFW:** добавляются 80/tcp, 443/tcp и 443/udp. Неактивный UFW включается только после определения и разрешения SSH-порта; иначе остаётся неактивным с предупреждением. Restore никогда не включает UFW. Остальные правила и политика не меняются.
- **Сертификаты:** Let's Encrypt webroot `/var/www/acme` и системный `certbot.timer`. nginx остаётся доступным при продлении; x-ui перезапускается только при обновлении сертификата панели.
- **Версии и CPU:** по умолчанию — последний стабильный 3x-ui; `-version v3.9.0` выбирает конкретный релиз, минимум — v3.8.0. QEMU/KVM не блокирует установку; отсутствие аппаратного AES вызывает только предупреждение о возможном снижении производительности.
- **AdGuard Home:** nginx завершает TLS; веб-интерфейс и DNS AdGuard Home доступны только локально. `x-ui-adguard.sh` — неразрушающая заглушка, отдельная установка через него не выполняется.
- **Проверки:** CI проверяет профили, nginx, firewall, сертификаты и Backup / Restore, но не заменяет подключение реального клиента. Backup v3 с AdGuard Home проверен на Ubuntu 26.04 amd64: архив, повторное восстановление на том же VPS, TLS/DoH, панель/XHTTP и запуск после reboot. Восстановление v3 на чистом сервере, Debian и arm64 не заявляется как проверенное на реальном VPS.

**Новый VPS:** используйте те же OS ID, VERSION_ID и архитектуру, переключите DNS при смене IP. Вместо запуска `x-ui-latest.sh` установите только утилиту и восстановите доверенный архив:

```bash
curl -fSL https://raw.githubusercontent.com/xPROMSx/3x-ui-auto-nginx/main/assets/backup/x-ui-backup.sh -o /tmp/x-ui-backup
install -o root -g root -m 0755 /tmp/x-ui-backup /usr/local/bin/x-ui-backup
rm -f /tmp/x-ui-backup
x-ui-backup restore /root/<archive>.tar.gz
```

SSH, настройки ОС и базовый firewall остаются ответственностью администратора. Архивы v2 требуют утилиты из соответствующего старого релиза. Пути `/usr/local/lib/3x-ui-pro` и `/etc/sysctl.d/99-3x-ui-pro.conf` сохранены для совместимости.

</details>

<a id="telemt-web-manager"></a>

## ✈️ Telegram Web Proxy Manager

[Companion-проект](https://github.com/xPROMSx/telegram-web-proxy-manager) для собственного Telegram WEB Proxy: HTTPS, сайт-прикрытие и обновления с откатом.

Проекты независимы; **этот установщик не устанавливает Telegram Web Proxy Manager**.

## 🤝 Происхождение и авторы

За основу был взят [3x-ui-pro](https://github.com/mozaroc/3x-ui-pro), однако с момента форка более половины основной логики переработано или заменено. Обновлены XHTTP/nginx, сертификаты и Backup / Restore; добавлен Hysteria2, устранены выявленные проблемы безопасности. Проект развивается независимо.

Панель предоставляет [MHSanaei/3x-ui](https://github.com/MHSanaei/3x-ui). Права авторов сторонних компонентов и существующие лицензии сохраняются.

<div align="center">

**Два домена. Несколько минут. Собственный 3x-ui / Xray сервер.**

[Установка](#installation) · [Релизы](https://github.com/xPROMSx/3x-ui-auto-nginx/releases) · [Ошибки](https://github.com/xPROMSx/3x-ui-auto-nginx/issues)

</div>
