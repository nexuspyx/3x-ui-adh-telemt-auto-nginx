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
