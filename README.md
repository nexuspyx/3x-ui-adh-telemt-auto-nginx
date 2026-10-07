<div align="center">

# 🚀 3x-ui Auto Nginx

**Два домена — готовый 3x-ui/Xray сервер.**

Автоматически: **nginx · Let's Encrypt · Fake Site · REALITY · XHTTP · Hysteria2 · опциональный AdGuard Home + DoH · Backup / Restore**

[![XHTTP / security](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-xhttp.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-xhttp.yml)
[![Backup / Restore](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-backup.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-backup.yml)
[![Ubuntu](https://img.shields.io/badge/Ubuntu-24.04%20%7C%2026.04-E95420?logo=ubuntu&logoColor=white)](#-технические-подробности)
[![Releases](https://img.shields.io/github/v/release/xPROMSx/3x-ui-auto-nginx)](https://github.com/xPROMSx/3x-ui-auto-nginx/releases)

[English](README_EN.md) · [Релизы](https://github.com/xPROMSx/3x-ui-auto-nginx/releases) · [Telemt WEB Manager](#telemt-web-manager) · [Ошибки](https://github.com/xPROMSx/3x-ui-auto-nginx/issues)

</div>

Готовое развёртывание [3x-ui](https://github.com/MHSanaei/3x-ui) и Xray: укажите домен панели и REALITY-домен — скрипт установит сервер, настроит nginx, TLS и сайт-прикрытие. При желании во время установки он также развернёт AdGuard Home с DNS-over-HTTPS через тот же домен панели, без публичного DNS-порта 53.

## ⚡ Быстрый старт

1. Привяжите **два домена к VPS**: один для панели, второй для REALITY.
2. На чистой поддерживаемой ОС подключитесь к серверу по SSH под **root**.
3. Выполните:

```bash
curl -fSL https://raw.githubusercontent.com/xPROMSx/3x-ui-auto-nginx/main/x-ui-latest.sh -o x-ui-latest.sh
bash x-ui-latest.sh
```

Скрипт сам запросит оба домена во время запуска. Если удобнее передать их сразу:

```bash
bash x-ui-latest.sh -subdomain panel.example.com -reality_domain reality.example.com
```

Нужны входящие TCP 80/443 и UDP 443; для получения сертификатов DNS доменов должен указывать на этот сервер.

> **⚠️ Только для чистой установки или полной переустановки.** `x-ui-latest.sh` удаляет существующую установку 3x-ui на сервере, включая базу панели и конфигурацию nginx. **Не используйте его как обычное обновление рабочего VPS.** При обнаружении 3x-ui/nginx/TLS требуется точное `YES` в верхнем регистре до удаления файлов, включая uninstall. Если сервер уже настроен, сначала сохраните резервную копию вне VPS.

## 🌐 Два домена → готовый сервер

| Вы вводите | Скрипт подготавливает | Вы получаете |
| --- | --- | --- |
| Домен панели + REALITY-домен | 3x-ui/Xray, nginx, Let's Encrypt TLS, автоматический fake site, пять inbound-профилей, subscriptions, diagnostics, Backup / Restore v3, интеграцию с UFW и опциональный AdGuard Home + DoH | URL панели, сгенерированные логин/пароль и URL diagnostics; при выборе AGH — admin URL, его пароль и DoH endpoint без третьего домена |

**Клиентов создайте в 3x-ui после установки.** Share links и subscriptions управляются через панель; installer не создаёт клиентские аккаунты.

## ✨ Всё для быстрого развёртывания

| | |
| --- | --- |
| **⚡ Полная автоматизация**<br>Установка и конфигурация сервера по двум доменам. | **🔐 Генерация секретов**<br>Логин/пароль панели, REALITY keys/short IDs и пути генерируются автоматически. |
| **🌐 nginx + SNI + TLS**<br>Готовая маршрутизация и сертификаты Let's Encrypt. | **🥸 Automatic Fake Site**<br>Сайт-прикрытие случайно выбирается из коллекции и разворачивается за вас. |
| **🚀 Современные transport'ы**<br>REALITY, XHTTP stream-up и Hysteria2, дополнительные WS/gRPC-профили. | **💾 Backup / Restore**<br>Откат на текущем VPS и восстановление на совместимой чистой ОС. |
| **📊 Diagnostics**<br>MTR и LibreSpeed с авторизацией через панель. | **🔥 UFW-aware**<br>Добавляет application rules, сохраняя существующие правила и policy. |
| **🛡 AdGuard Home + DoH**<br>Опциональная интеграция через тот же nginx/TLS, без публичного :53. | **♻️ Автоматическое TLS renewal**<br>Let's Encrypt webroot + `certbot.timer`; nginx остаётся online при renewal. |

### 🚀 Transport-профили

| Transport | По умолчанию | Схема подключения |
| --- | --- | --- |
| VLESS + REALITY | ✅ Включён | TCP 443 → nginx SNI → Xray |
| VLESS + XHTTP `stream-up` | ✅ Включён | TLS/HTTP/2 → nginx → Unix socket |
| Hysteria2 | ✅ Включён | UDP 443 → Xray, TLS + H3 |
| VLESS + WebSocket | ⏸ Выключен | TLS → фиксированный nginx route → Xray |
| Trojan + gRPC | ⏸ Выключен | TLS/HTTP/2 → фиксированный nginx route → Xray |

Создаются все пять профилей. При необходимости включите WS или Trojan gRPC вручную в 3x-ui. Поддержка зависит от transport'а и версии клиента.

### 🥸 Automatic Fake Site

Не нужно самостоятельно создавать сайт-прикрытие: installer выбирает страницу из встроенной коллекции fake sites и разворачивает её вместе с nginx и TLS. Это часть развёртывания, а не гарантия незаметности трафика.

### 🛡 AdGuard Home + DNS-over-HTTPS

Установка **опциональна, по умолчанию N**. Третий домен не нужен: используется существующий домен панели. Admin UI доступен под случайным `/adg-.../`, DoH endpoint — `https://panel.example.com/dns-query`.

AGH web и native DNS слушают только loopback; публичный TCP/UDP 53 не открывается. TLS завершается nginx. Конфигурация и данные AGH включаются в **Backup / Restore v3**.

<a id="telemt-web-manager"></a>

## ✈️ Нужен ещё и Telegram proxy?

### [Telemt WEB Manager](https://github.com/xPROMSx/telemt-web-manager)

Companion-проект для автоматической установки и управления Telemt WEB proxy: обновления, rollback и интеграция с nginx/TLS.

**3x-ui Auto Nginx** разворачивает сервер 3x-ui/Xray, **Telemt WEB Manager** — Telemt WEB proxy. Это отдельные проекты: данный installer не устанавливает Telemt. Для совместного размещения нужно проверить порты, домены и конфигурацию nginx.

## 💾 Backup / Restore

Команды выполняются от root. Fresh install включает `/usr/local/bin/x-ui-backup`:

```bash
x-ui-backup backup
x-ui-backup list
```

Текущий backup format — **v3**. Архивы v2 новым tool не поддерживаются: восстановите их утилитой из matching older release.

Восстанавливайте только доверенные архивы, созданные этой утилитой и хранившиеся под вашим контролем. Проверки restore выявляют повреждённое или несовместимое managed state, но не проверяют происхождение архива.

Архивы сохраняются в `/var/backups/x-ui/` с доступом только для root. Внутри — состояние клиентов/базы, сертификаты/private keys и runtime secrets. **Обязательно скопируйте архив с VPS** на ПК, NAS или в другое безопасное хранилище.

Для отката на текущем VPS:

```bash
x-ui-backup restore /var/backups/x-ui/<archive>.tar.gz
```

Успешное восстановление заканчивается `Restore completed successfully.` Восстанавливается управляемое состояние приложения, а не вся ОС.

Backup / Restore v3 проверен на реальном Ubuntu 26.04 amd64 VPS: создание архива с AdGuard Home, повторное восстановление на том же сервере, сохранение TLS/Certbot/AGH/DoH, работоспособность панели и XHTTP после restore и полный запуск после reboot.

<details>
<summary>🛟 Восстановление на новом VPS / установка backup utility</summary>

Нужны **те же OS ID, VERSION_ID и архитектура**, что у backup: например, Ubuntu 26.04 amd64 → Ubuntu 26.04 amd64. Ubuntu 24.04 или arm64 не подходят для такого архива.

1. Установите совместимую чистую ОС и при необходимости выполните свой OS/bootstrap script.
2. **Не запускайте `x-ui-latest.sh`.** Установите только backup utility (этот же блок подходит для существующей установки без утилиты):

   ```bash
   curl -fsSL https://raw.githubusercontent.com/xPROMSx/3x-ui-auto-nginx/main/assets/backup/x-ui-backup.sh -o /tmp/x-ui-backup
   install -o root -g root -m 0755 /tmp/x-ui-backup /usr/local/bin/x-ui-backup
   rm -f /tmp/x-ui-backup
   ```

3. Безопасно передайте сохранённый архив и выполните restore:

   ```bash
   x-ui-backup restore /root/x-ui-backup-....tar.gz
   ```

4. Если IP изменился, переключите DNS существующих доменов на новый VPS. После восстановления используйте прежние конфигурации панели и клиентов.

Restore сам устанавливает отсутствующие application dependencies и восстанавливает panel/runtime, nginx, сертификаты, web/diagnostics, project systemd units, managed sysctl и webroot renewal через `certbot.timer`. SSH, `authorized_keys`, fail2ban, базовая UFW policy и OS/bootstrap state остаются ответственностью администратора. Restore добавляет правила 80/tcp, 443/tcp и 443/udp, но никогда не включает UFW; посторонние cron jobs сохраняются.

</details>

## 🔧 Технические подробности

<details>
<summary>Платформы, маршрутизация, firewall и optional tools</summary>

- **Платформы:** Ubuntu 26.04 amd64 проверен на реальном VPS; Ubuntu 24.04 используется в CI. Debian 13 допускается OS checks без аналогичной live-проверки; Debian 12 не поддерживается. Не все архитектуры протестированы. QEMU CPU не блокирует installation; отсутствие hardware AES даёт performance advisory.
- **Маршрутизация:** nginx принимает TCP 443 и направляет по SNI в REALITY/Xray на 8443 либо TLS vhost панели на 7443. Camouflage target REALITY использует 9443. XHTTP работает через Unix socket; WS/gRPC имеют фиксированные paths/backends. Hysteria2 независимо принимает UDP 443.
- **Panel/API и subscriptions:** panel backend — только `127.0.0.1:<random_port>` с HTTPS; subscription backend — loopback HTTP за nginx TLS. Публичные panel/API доступны через nginx :443, включая штатный multi-node по API token с проверкой TLS. Прямой native panel mTLS не входит в managed topology.
- **UFW:** активный UFW получает только правила 80/tcp, 443/tcp и 443/udp. Неактивный включается только после определения и разрешения SSH ports; иначе остаётся выключенным с warning. Существующие rules/default policy сохраняются, жёстко заданного SSH port 22 нет.
- **Версии:** по умолчанию выбирается latest stable 3x-ui. `-version <tag>` позволяет выбрать релиз панели; binary и CLI берутся из одного tag.
- **Diagnostics/subscriptions:** MTR/LibreSpeed с авторизацией через панель, JSON и Clash/Mihomo subscriptions. Новые пользовательские WS/gRPC inbounds требуют явных nginx routes; generic proxy к произвольному localhost port отсутствует.
- **Optional AdGuard Home:** по умолчанию **N**; тот же panel domain/nginx TLS, без публичного :53, с Backup / Restore v3. `x-ui-adguard.sh` — retired non-destructive stub. `x-ui-patch.sh` остаётся secondary maintenance utility, а не универсальным updater.
- **TLS renewal:** webroot `/var/www/acme` + distro `certbot.timer`. Nginx остаётся online и graceful reload применяется после renewal; x-ui перезапускается только при обновлении сертификата панели. Release acceptance включает `certbot renew --dry-run` для обоих сертификатов.
- **Совместимость:** `/usr/local/lib/3x-ui-pro` и `/etc/sysctl.d/99-3x-ui-pro.conf` намеренно сохранены для Backup/Restore.

</details>

<details>
<summary>Проверки, разработка и релизы</summary>

CI проверяет transport/Host-профили, nginx syntax, отрицательный тест arbitrary localhost-port access, firewall, version pinning, destructive guard, webroot/timer renewal и canonical sources. Backup/Restore tests используют изолированные fixtures с настоящими tar/gzip, SQLite и nginx. Bash syntax проверяется для installer, patch, AdGuard и backup scripts. CI не заменяет live acceptance с реальным клиентом.

Разработка ведётся через PR в `main`; см. [CONTRIBUTING.md](CONTRIBUTING.md). Имена обязательных jobs остаются `Stack XHTTP and security` и `Stack Backup and restore` для совместимости branch protection.

Исторические `personal-v*` releases и branches сохраняются. Следующие project releases относятся к 3x-ui Auto Nginx; versioning проекта отделён от версий upstream 3x-ui/Xray. Branding changes сами по себе не создают новый релиз.

</details>

## 🤝 Credits / Origins

Проект основан на [mozaroc/3x-ui-pro](https://github.com/mozaroc/3x-ui-pro). Панель предоставляется [MHSanaei/3x-ui](https://github.com/MHSanaei/3x-ui). **3x-ui Auto Nginx** развивается независимо и может выборочно принимать полезные upstream changes после ревью.

Права авторов сторонних компонентов и существующие license/copyright statements сохраняются. Новая лицензия для унаследованного кода не добавляется.
