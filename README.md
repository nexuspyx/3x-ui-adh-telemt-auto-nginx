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

Fresh installer автоматически устанавливает `/usr/local/bin/x-ui-backup` из `personal`.
Запускайте от root:

```bash
x-ui-backup backup
x-ui-backup list
x-ui-backup restore /var/backups/x-ui/<archive>.tar.gz
```

Формат v2 сохраняет БД и runtime панели, nginx, сертификаты, web content, units
x-ui/MTR, managed sysctl и запись обновления Certbot. Большие diagnostic test files
создаются заново. SSH, bootstrap/security state и UFW files не восстанавливаются;
добавляются только application rules 80/tcp, 443/tcp и 443/udp без включения UFW.
Посторонние cron jobs сохраняются.

Restore предназначен для rollback на том же VPS или восстановления на чистом VPS
с **тем же OS ID, VERSION_ID и архитектурой**: Ubuntu 24.04/26.04 либо Debian 12/13.
Для clean-host recovery выполните свой bootstrap, установите утилиту, безопасно
перенесите архив и запустите restore **без запуска `x-ui-latest.sh`**:

```bash
sudo install -d -m 0755 /usr/local/bin
curl -fsSL https://raw.githubusercontent.com/xPROMSx/3x-ui-pro/personal/assets/backup/x-ui-backup.sh -o x-ui-backup.sh
sudo install -o root -g root -m 0755 x-ui-backup.sh /usr/local/bin/x-ui-backup
sudo x-ui-backup restore /path/to/archive.tar.gz
```

Отсутствующие application packages устанавливаются автоматически. Домены остаются
прежними; DNS переключается вручную. Восстановление поверх неизвестного сервера с
чужими сервисами не поддерживается. При ошибке restore завершится с non-zero;
устраните причину и повторите с тем же архивом.

**Архив содержит UUID, пароли и private keys: храните и передавайте его защищённо.**
Локальный backup на том же VPS не является disaster recovery — копируйте архив
off-host. Live-проверка clean-VPS recovery выполняется отдельно.
