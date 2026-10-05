<div align="center">

# 3x-ui Stack

**REALITY · XHTTP · Hysteria2 · nginx · Backup/Restore**

[![XHTTP / security](https://github.com/xPROMSx/3x-ui-stack/actions/workflows/stack-xhttp.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-stack/actions/workflows/stack-xhttp.yml)
[![Backup / restore](https://github.com/xPROMSx/3x-ui-stack/actions/workflows/stack-backup.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-stack/actions/workflows/stack-backup.yml)
[![Tested OS](https://img.shields.io/badge/tested-Ubuntu%2026.04%20amd64-E95420?logo=ubuntu&logoColor=white)](#platforms)
[![Releases](https://img.shields.io/github/v/release/xPROMSx/3x-ui-stack)](https://github.com/xPROMSx/3x-ui-stack/releases)

[English](README_EN.md) · [Релизы](https://github.com/xPROMSx/3x-ui-stack/releases) · [Обсуждение ошибок](https://github.com/xPROMSx/3x-ui-stack/issues)

</div>

Поддерживаемый стек автоматического развёртывания [3x-ui](https://github.com/MHSanaei/3x-ui) и Xray: современные транспорты, nginx с SNI/TLS, подписки, диагностика и проверяемое восстановление. Развитие ведётся в `main` через отдельные PR с проверками безопасности и Backup/Restore.

> **Для новой установки или rebuild.** `x-ui-latest.sh` останавливает и очищает прежнюю установку, включая БД панели и конфигурацию nginx. Это не безопасное обновление существующего VPS. Сначала сохраните backup за пределами сервера.

## Возможности и транспорты

| Возможность | Реализация | Назначение |
| --- | --- | --- |
| VLESS + REALITY | TCP, внешний 443 через nginx SNI, Xray на 8443 | Основной профиль с REALITY |
| VLESS + XHTTP | `stream-up`, Unix socket, TLS через nginx | XHTTP без произвольного проксирования локальных портов |
| Hysteria2 | UDP/QUIC на 443, TLS в Xray | Альтернативный транспорт при доступном UDP |
| VLESS + WebSocket | Фиксированный маршрут nginx с TLS | Дополнительный профиль для совместимых клиентов |
| Trojan + gRPC | Фиксированный маршрут nginx, HTTP/2 и TLS | Дополнительный профиль для совместимых клиентов |
| Подписки и диагностика | Clash/Mihomo, JSON; MTR и LibreSpeed | Управление клиентами и проверка сети |
| Backup/Restore v2 | Проверки архива, БД и состояния сервисов | Откат и перенос на совместимую чистую ОС |

Installer создаёт все пять inbound-профилей. Использование WS и Trojan gRPC необязательно; отдельного переключателя их установки сейчас нет. Поддержка транспортов зависит от версии клиента и Xray.

### nginx, SNI и TLS

TCP 443 разделяется по SNI: REALITY направляется в Xray, домен панели - в TLS-vhost nginx. Сертификаты выпускает Let's Encrypt/Certbot. XHTTP использует Unix socket; WS и Trojan gRPC используют только известные маршруты и фиксированные backend-порты. Произвольные пути вида `/<port>/...` не должны открывать доступ к localhost-сервисам. Hysteria2 использует UDP 443 независимо от TCP-маршрутизации nginx.

### Поведение UFW

При активном UFW installer сохраняет существующую политику и добавляет 80/tcp, 443/tcp и 443/udp. При неактивном UFW перед включением определяет SSH-порт из текущего соединения или эффективной конфигурации sshd. Если порт определить нельзя, предупреждает и не включает firewall автоматически. Сброс правил и жёстко заданный SSH-порт 22 не используются.

<a id="platforms"></a>
## Поддерживаемые и проверенные платформы

| Платформа | Статус |
| --- | --- |
| Ubuntu 26.04 LTS amd64 | Основная платформа; ранее проверена на реальном VPS |
| Ubuntu 24.04 LTS | Допускается installer и restore; автоматические проверки выполняются в CI |
| Debian 12 / 13 | Допускаются проверкой ОС; равная полнота проверки на реальном VPS не заявляется |

Восстановление требует совпадения `ID`, `VERSION_ID` и архитектуры исходного сервера. Проверка версии ОС не означает, что все архитектуры проверены. Installer отклоняет CPU с моделью QEMU; используйте VPS с доступной реальной моделью CPU.

## Установка

Нужны root-доступ, чистая поддерживаемая ОС, домены панели и REALITY с корректным DNS, доступные TCP 80/443 и UDP 443 для Hysteria2.

Скачайте и просмотрите скрипт перед запуском:

```bash
curl -fSL https://raw.githubusercontent.com/xPROMSx/3x-ui-stack/main/x-ui-latest.sh -o x-ui-latest.sh
less x-ui-latest.sh
bash x-ui-latest.sh -subdomain panel.example.com -reality_domain reality.example.com
```

Замените домены своими. По умолчанию выбирается последний стабильный релиз 3x-ui. Для воспроизводимой установки укажите проверенную версию панели через `-version <tag>`; сама утилита 3x-ui Stack и версия панели имеют отдельные циклы релизов.

## Companion project: Telemt WEB Manager

[**Telemt WEB Manager**](https://github.com/xPROMSx/telemt-web-manager) дополняет этот стек установкой и управлением Telemt WEB proxy на Ubuntu, с nginx, Let's Encrypt, systemd и опциональным SOCKS5.

Это отдельный companion project для тех, кому вместе с 3x-ui нужен Telemt WEB proxy. Он не устанавливается этим скриптом автоматически. При совместном размещении заранее согласуйте домены, занятые порты и конфигурацию nginx по документации обоих проектов.

## Backup / Restore

Все команды ниже выполняются от root.

### Backup

Fresh install автоматически устанавливает `/usr/local/bin/x-ui-backup`.
Если на существующем VPS утилиты нет, используйте блок её установки ниже;
повторно запускать installer не нужно.

Создать backup и посмотреть локальные архивы:

```bash
x-ui-backup backup
x-ui-backup list
```

Архивы сохраняются в `/var/backups/x-ui/` с доступом только для root. Они содержат
БД, UUID и состояние клиентов, сертификаты/private keys и REALITY/runtime secrets.
**Обязательно скопируйте архив с VPS на ПК, NAS или в другое безопасное хранилище:**
локальный backup не поможет при потере сервера.

### Restore на текущем VPS

Выберите архив и восстановите его:

```bash
x-ui-backup list
x-ui-backup restore /var/backups/x-ui/<archive>.tar.gz
```

Restore сам выполняет необходимые проверки. Успех подтверждается сообщением
`Restore completed successfully.` Это откат состояния 3x-ui Stack, а не всей ОС.

### Restore на новом VPS

Нужен чистый VPS с **тем же OS ID, VERSION_ID и архитектурой**, что и backup.
Например, Ubuntu 26.04 amd64 -> Ubuntu 26.04 amd64; переход на Ubuntu 24.04
или arm64 не поддерживается.

1. Установите совместимую ОС и при необходимости выполните свой OS/bootstrap script.
2. **Не запускайте `x-ui-latest.sh`.** Установите только backup utility из `main`:

   ```bash
   curl -fsSL \
     https://raw.githubusercontent.com/xPROMSx/3x-ui-stack/main/assets/backup/x-ui-backup.sh \
     -o /tmp/x-ui-backup
   install -o root -g root -m 0755 /tmp/x-ui-backup /usr/local/bin/x-ui-backup
   rm -f /tmp/x-ui-backup
   ```

3. Безопасно передайте сохранённый архив на VPS, например в `/root/`, и выполните:

   ```bash
   x-ui-backup restore /root/x-ui-backup-....tar.gz
   ```

4. Если IP изменился, переключите DNS старых доменов на новый VPS.
   После успешного restore используйте существующие настройки панели и клиентов.

Restore сам устанавливает отсутствующие application dependencies и восстанавливает
состояние 3x-ui Stack.

### Что восстанавливается / что не восстанавливается

Backup покрывает panel/runtime, nginx, сертификаты, diagnostics/web state,
systemd units проекта, managed sysctl и managed Certbot cron. SSH, `authorized_keys`,
fail2ban, базовая политика UFW и OS/bootstrap state не восстанавливаются - их
готовит bootstrap или администратор нового сервера. Restore добавляет только
application rules 80/tcp, 443/tcp и 443/udp; посторонние cron jobs сохраняются.

### Validation

Backup/Restore v2 проверен на реальном Ubuntu 26.04 amd64 VPS
(3x-ui 3.9.0, Xray 26.9.30, nginx 1.28.3): backup/rollback на том же сервере,
восстановление чистого VPS после bootstrap без запуска `x-ui-latest.sh`,
сохранение работы сервисов после reboot, работа панели и реальных клиентских подключений.

## Проверки безопасности

CI запускает полный текущий набор регрессий: генерация inbound/Host для обеих схем БД, XHTTP stream-up, Hysteria2, REALITY, фиксированные WS/gRPC маршруты, отрицательный тест доступа к постороннему localhost-порту, синтаксис nginx, UFW и согласованность версии панели/CLI. Отдельный workflow проверяет Backup/Restore v2 на изолированных фикстурах с настоящими tar/gzip, SQLite и nginx, включая опасные пути/ссылки в архиве, несовместимую ОС, сбои и проверки сервисов.

Проверяется Bash-синтаксис installer, patch, AdGuard и backup utility. Автоматические проверки не заменяют проверку реальных клиентских подключений на одноразовом VPS. Описанная выше live validation относится к ранее зафиксированному checkpoint, а не к каждому будущему коммиту.

## Разработка и релизы

- `main` - основная линия проекта. Изменения проходят через `feat/*`, `fix/*`, `docs/*` или `chore/*` и PR.
- Обязательные проверки: `Stack XHTTP and security` и `Stack Backup and restore`. Внешние reviewers для единственного maintainer не обязательны.
- Релизы стека публикуются как версии `vX.Y.Z` из проверенного `main` с описанием изменений. Новое имя само по себе не создаёт новый релиз.
- Старые `personal-v*` tags/releases сохраняют историческое значение. Их зелёный badge не подтверждает свежий security checkpoint.
- `personal` временно сохраняется для совместимости старых ссылок. Новые команды используют `main`; upstream не синхронизируется автоматически.
- Для деталей защиты ветки и аварийного восстановления см. [CONTRIBUTING.md](CONTRIBUTING.md).

Исторические пути `/usr/local/lib/3x-ui-pro` и `/etc/sysctl.d/99-3x-ui-pro.conf` сохранены для совместимости установки и Backup/Restore v2.

## Происхождение проекта

3x-ui Stack остаётся в fork network [mozaroc/3x-ui-pro](https://github.com/mozaroc/3x-ui-pro) и развивается независимо. Панель 3x-ui и её релизы предоставляются [MHSanaei/3x-ui](https://github.com/MHSanaei/3x-ui). Полезные upstream-изменения рассматриваются отдельно и переносятся после проверки.

Авторы исходных компонентов сохраняют свои права. Новая лицензия на унаследованный код в рамках этой миграции не добавляется.
