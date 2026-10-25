# 3X-UI + Telemt + Hardened Nginx + AdGuard Home (Anti-TSPU Stealth Edition)

> 🛡️ **Автоматизированный комплекс для развертывания персонального прокси-сервера нового поколения.**  
> Полная защита от ТСПУ и активного зондирования, единый вход через порт 443, тихий выпуск SSL через Cloudflare DNS-01 (порт 80 закрыт навсегда), встроенный Telegram Web Proxy (Telemt) с маскировкой 200 OK, AdGuard Home с локальным DNS-кэшем 0 мс и защита от ботов.

---

## 🚀 Установка одной командой

Подключитесь к вашему чистому VPS по SSH как `root` и выполните:

```bash
curl -sSL https://raw.githubusercontent.com/nexuspyx/3x-ui-adh-telemt-auto-nginx/main/install.sh | bash
```

### Что делает скрипт:
1. **Авто-апгрейд ОС**: Полное обновление пакетов системы (`apt update && apt full-upgrade -y`).
2. **Интерактивный мастер**:
   - Запрашивает домен и Cloudflare API данные.
   - Предлагает задать свои секретные пути (`Locations`) и порты или генерирует стойкие случайные значения:
     - Путь веб-панели 3x-ui (`PANEL_PATH`)
     - Обычная подписка Base64 (`SUB_PATH`)
     - JSON подписка для Happ/Sing-box (`SUB_JSON_PATH`)
     - **Telemt Telegram Web Proxy (`TG_PATH`, например `/tg` или `/fbrfbrbH1vbyrT`)**
     - **AdGuard Home DoH (`ADH_DOH_PATH`, например `/dns-query` или `/my-secret-doh`)**
     - VLESS WebSocket, XHTTP, gRPC и Trojan gRPC
3. **Тихий SSL (Cloudflare DNS-01)**:
   - Сертификаты выпускаются через API Cloudflare.
   - **Порт 80 наглухо закрыт в UFW и Nginx** — сервер не светится в сканерах портов.
4. **Сетевой тюнинг и Stealth**:
   - **IPv6 полностью отключен** на уровне ядра (устраняет задержки 1–3 сек при открытии сайтов).
   - **ICMP Ping выключен**: сервер не отвечает на `ping` (100% packet drop, скрыт от сканеров).
   - Активирован алгоритм контроля перегрузок TCP **BBR** и TCP Fast Open.
5. **Развертывание 3X-UI (все 7 inbounds)**:
   - Reality TCP (`:8443` -> Fallback `:9443` с `xver: 1`).
   - VLESS WebSocket.
   - VLESS XHTTP (через Unix Domain Socket).
   - Trojan gRPC.
   - VLESS gRPC.
   - Hysteria 2 UDP (`:443`).
   - AmneziaWG (`:8443`).
   - **Два типа подписок**: Обычная (Base64) и JSON (Happ / Sing-box / v2rayNG).
6. **Telemt (Telegram Web Proxy) с маскировкой 200 OK**:
   - Проксирование трафика Telegram поверх HTTPS (по выбранному пути `TG_PATH`).
   - Поддержка WebSocket lanes и HTTPS lanes.
   - Внутренний Decoy-сервер (`:18082`): возвращает реальный маскировочный сайт с кодом **200 OK** на любые зондирования ТСПУ вместо подозрительного 404.
   - Веб-панель управления Telemt Panel (`:65001` HTTPS).
7. **AdGuard Home (DNS 0 мс + DoH с кастомным путем)**:
   - Локальный резолвер DNS (`127.0.0.1:57998`) обеспечивает **кэширование DNS за 0 мс** для Xray.
   - DoH доступен по вашему секретному пути (например `https://domain.com/my-secret-doh` или `https://domain.com/my-secret-doh/Client1`).
   - Веб-панель управления AdGuard (`:3000`).
8. **Anti-Bot Shield в Nginx**:
   - Сброс соединений (`return 444`) для поисковых ботов (Google, Yandex), сетевых сканеров (Shodan, Censys, sqlmap, nikto и др.) и сканеров уязвимостей (`.env`, `wp-login`).
   - Заголовки `X-Robots-Tag: noindex, nofollow` и запрещающий `robots.txt`.

---

## 🗺️ Сетевая архитектура и порты

Все внешние веб-сервисы мультиплексируются через **единый порт 443**:

```
                              Входящий трафик (Интернет)
                                         │
                   ┌─────────────────────┴─────────────────────┐
                   │                                           │
             TCP ПОРТ 443                                UDP ПОРТ 443
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
Маскировочный  Панель 3X-UI    Подписки (x2)   Telemt Proxy     VLESS /        AdGuard DoH
    сайт          (Admin)     (Base64 & JSON)    (Кастом путь)   Trojan       (Кастом путь)
    (:9443)    (Внутр. порт)   (Внутр. порт)     (:18080)    (WS/gRPC/UDS)       (:44712)
                                                     │
                                             При зондировании
                                                     │
                                            Decoy Server (:18082)
                                                (Код: 200 OK)
```

---

## 📁 Telemt: Хранение ключей клиентов и быстрая миграция

Чтобы при переносе прокси на другой сервер у ваших клиентов **не менялись ссылки**:

- **Файл настроек и ключей:** `/etc/telemt/config.toml`
  - Секция `[access.users]` содержит имена клиентов и их 32-значные 16-ричные ключи:
    ```toml
    [access.users]
    Client1 = "89bbf4be3eb7f2a085ec010caa541f26"
    Client2 = "6d4a813aeca5b00578e7bf4e9a7243d2"
    ```
- **Лимиты и статистика:** `/etc/telemt/telemt.limit.json`
- **Конфиг панели управления:** `/etc/telemt-panel/config.toml`

### Как перенести клиентов на новый VPS:
1. Установите данный скрипт на новом сервере на том же домене.
2. Скопируйте файл `/etc/telemt/config.toml` со старого сервера на новый.
3. Перезапустите службу: `systemctl restart telemt`.
4. Все ссылки `tg://proxy?...` и `tg://webproxy?...` у клиентов продолжат работать без каких-либо повторных настроек!

---

## 🔒 Безопасность и файрвол (UFW)

После установки UFW настроен в строгом режиме:
- `80/tcp` — **НАГЛУХО ЗАКРЫТ** (`ufw deny 80/tcp`).
- `443/tcp` — Открыт (Nginx Stream + Reality).
- `443/udp` — Открыт (Hysteria 2).
- `SSH` — Открыт (только ваш текущий порт SSH).
- `65001/tcp` — Веб-панель Telemt Panel (HTTPS).
- `3000/tcp` — Веб-интерфейс AdGuard Home.
- Все остальные порты — **ЗАКРЫТЫ**.

---

## 📄 Сводка данных доступа

После завершения установки скрипт сохраняет полный отчет с паролями и ссылками в защищенный локальный файл:
```bash
cat /root/server_credentials.txt
```
В файле содержатся:
- Ссылка, логин и пароль к панели 3x-ui.
- Обе ссылки на подписки (Base64 и JSON).
- Ссылка на панель Telemt и обе ссылки подключения Telegram (`tg://webproxy?...` с кастомным путем и `tg://proxy?...`).
- Ссылка, логин и пароль к панели AdGuard Home, а также ссылка на DoH с вашим кастомным путем.
- Список всех портов и путей.
