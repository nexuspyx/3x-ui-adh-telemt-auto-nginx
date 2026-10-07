<div align="center">

# 🚀 3X-UI AUTO NGINX

### Автоматическое развёртывание 3X-UII / XRAY-CORE на собственном VPS

**REALITY · XHTTP · HYSTERIA2 · WebSocket · gRPC · NGINX · HTTPS · BACKUP / RESTORE**

Два домена, чистый VPS и несколько минут на установку.

[![XHTTP / security](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-xhttp.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-xhttp.yml)
[![Backup / Restore](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-backup.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-backup.yml)
[![Ubuntu](https://img.shields.io/badge/Ubuntu-24.04%20%7C%2026.04-E95420?logo=ubuntu&logoColor=white)](#technical-details)
[![Releases](https://img.shields.io/github/v/release/xPROMSx/3x-ui-auto-nginx)](https://github.com/xPROMSx/3x-ui-auto-nginx/releases)

[English](README_EN.md) · [Релизы](https://github.com/xPROMSx/3x-ui-auto-nginx/releases) · [Telegram Web Proxy Manager](#telemt-web-manager) · [Ошибки](https://github.com/xPROMSx/3x-ui-auto-nginx/issues)

</div>

**3X-UI AUTO NGINX** устанавливает [3x-ui](https://github.com/MHSanaei/3x-ui) и Xray, настраивает nginx, HTTPS, подключения, подписки, диагностику и Backup / Restore. AdGuard Home с DoH — по желанию.

Нужны два домена и чистый VPS. Сайт-прикрытие установщик выбирает и разворачивает автоматически.

## ✨ Почему этот проект

| | |
| --- | --- |
| **Готовые профили подключения**<br>REALITY, XHTTP, Hysteria2, WebSocket и Trojan gRPC уже настроены. | **Закрытые внутренние сервисы**<br>Панель и подписки не публикуют свои служебные порты напрямую в интернет. |
| **Безопасная схема nginx**<br>Нет проксирования запросов на произвольные локальные порты. | **Backup / Restore v3**<br>Откат текущего VPS или восстановление после переустановки. |
| **Автоматические сертификаты**<br>Let's Encrypt выпускается и продлевается без ручной настройки. | **AdGuard Home + DoH**<br>Устанавливается по желанию и не требует третьего домена. |

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

Backup / Restore **v3** позволяет откатить текущий VPS или восстановить совместимый сервер после переустановки. Утилита устанавливается вместе с сервером; выполняйте от root:

```bash
# Создать новую резервную копию
x-ui-backup backup

# Показать доступные копии
x-ui-backup list

# Восстановить выбранную копию
x-ui-backup restore /var/backups/x-ui/<archive>.tar.gz
```

Архивы сохраняются в `/var/backups/x-ui/`.

> **Backup содержит чувствительные данные:** базу клиентов, пароли и приватные ключи сертификатов. Храните копию **вне VPS** и восстанавливайте только доверенные архивы.

[Восстановление на новом VPS](#technical-details).

## 🔐 Безопасность

Служебные интерфейсы не открываются напрямую в интернет: панель, подписки и дополнительные сервисы доступны через nginx и HTTPS, а внутренние порты остаются локальными.

Нет универсального proxy на произвольные localhost-порты. Критическая ошибка останавливает установку или восстановление вместо запуска неполной конфигурации.

Новые релизы проходят автоматические проверки и полноценное тестирование установки и восстановления на реальном VPS.

<a id="technical-details"></a>

<details>
<summary>⚙️ Технические подробности и совместимость</summary>

- **ОС:** Ubuntu 24.04, Ubuntu 26.04 и Debian 13. Debian 12 не поддерживается.
- **UFW:** добавляются 80/tcp, 443/tcp и 443/udp. Неактивный UFW включается только после определения и разрешения SSH-порта; иначе остаётся неактивным с предупреждением. Restore никогда не включает UFW.
- **Сертификаты:** Let's Encrypt webroot и `certbot.timer` продлевают сертификаты автоматически, без остановки nginx.
- **Версия 3x-ui:** по умолчанию — последний стабильный релиз. `-version v3.9.0` выбирает конкретный; минимум — v3.8.0.

**Восстановление на новом VPS:** нужны те же ОС, версия и архитектура, что у копии. Переключите DNS при смене IP. Не запускайте `x-ui-latest.sh` — установите только утилиту и восстановите доверенный архив:

```bash
curl -fSL https://raw.githubusercontent.com/xPROMSx/3x-ui-auto-nginx/main/assets/backup/x-ui-backup.sh -o /tmp/x-ui-backup
install -o root -g root -m 0755 /tmp/x-ui-backup /usr/local/bin/x-ui-backup
rm -f /tmp/x-ui-backup
x-ui-backup restore /root/<archive>.tar.gz
```

SSH, настройки ОС и базовый firewall остаются ответственностью администратора. Для архивов v2 нужна утилита из соответствующего старого релиза.

</details>

<a id="telemt-web-manager"></a>

## ✈️ Telegram Web Proxy Manager

[Мой дополнительный проект](https://github.com/xPROMSx/telegram-web-proxy-manager) для собственного Telegram WEB Proxy: HTTPS, сайт-прикрытие и обновления с аварийным откатом.

Проекты независимы; **этот установщик 3X-UI не устанавливает Telegram Web Proxy Manager**.

## 🤝 Происхождение и авторы

За основу был взят [3x-ui-pro](https://github.com/mozaroc/3x-ui-pro), однако с момента форка более половины основной логики переработано или заменено. Обновлены схема XHTTP, nginx, сертификаты и Backup / Restore; добавлен Hysteria2, устранены выявленные проблемы безопасности. Проект развивается независимо.

Панель предоставляет [MHSanaei/3x-ui](https://github.com/MHSanaei/3x-ui). Права авторов сторонних компонентов и существующие лицензии сохраняются.

<div align="center">

**Два домена. Несколько минут. Собственный 3x-ui / Xray сервер.**

[Установка](#installation) · [Релизы](https://github.com/xPROMSx/3x-ui-auto-nginx/releases) · [Ошибки](https://github.com/xPROMSx/3x-ui-auto-nginx/issues)

</div>
