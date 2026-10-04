# 3x-ui-pro — personal

🇬🇧 [English version](README_EN.md)

`personal` — персональная ветка форка [mozaroc/3x-ui-pro](https://github.com/mozaroc/3x-ui-pro) для тестирования и эксплуатации собственных доработок fresh installer панели [3x-ui](https://github.com/MHSanaei/3x-ui).

Основной фокус:

- VLESS + REALITY
- VLESS + XHTTP
- nginx / SNI / TLS на едином порту 443
- современные и минимальные настройки Xray
- автоматические проверки fresh-install конфигурации

Основная тестовая платформа: **Ubuntu 26.04 LTS**.

Совместимость: [Telemt WEB Manager](https://github.com/xPROMSx/telemt-web-manager).

> Ветка предназначена прежде всего для новых установок и rebuild. `x-ui-latest.sh` не является безопасным in-place updater существующего VPS.

## Установка

```bash
wget -qO x-ui-latest.sh https://raw.githubusercontent.com/xPROMSx/3x-ui-pro/personal/x-ui-latest.sh
bash x-ui-latest.sh
```

По умолчанию устанавливается последний стабильный релиз 3x-ui.

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
`Restore completed successfully.` Это откат состояния 3x-ui-pro, а не всей ОС.

### Restore на новом VPS

Нужен чистый VPS с **тем же OS ID, VERSION_ID и архитектурой**, что и backup.
Например, Ubuntu 26.04 amd64 → Ubuntu 26.04 amd64; переход на Ubuntu 24.04
или arm64 не поддерживается.

1. Установите совместимую ОС и при необходимости выполните свой OS/bootstrap script.
2. **Не запускайте `x-ui-latest.sh`.** Установите только backup utility из `personal`:

   ```bash
   curl -fsSL \
     https://raw.githubusercontent.com/xPROMSx/3x-ui-pro/personal/assets/backup/x-ui-backup.sh \
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
состояние 3x-ui-pro.

### Что восстанавливается / что не восстанавливается

Backup покрывает panel/runtime, nginx, сертификаты, diagnostics/web state,
systemd units проекта, managed sysctl и managed Certbot cron. SSH, `authorized_keys`,
fail2ban, базовая политика UFW и OS/bootstrap state не восстанавливаются — их
готовит bootstrap или администратор нового сервера. Restore добавляет только
application rules 80/tcp, 443/tcp и 443/udp; посторонние cron jobs сохраняются.

### Validation

Backup/Restore v2 проверен на реальном Ubuntu 26.04 amd64 VPS
(3x-ui 3.9.0, Xray 26.9.30, nginx 1.28.3): backup/rollback на том же сервере,
восстановление чистого VPS после bootstrap без запуска `x-ui-latest.sh`,
сохранение работы сервисов после reboot, работа панели и реальных клиентских подключений.
