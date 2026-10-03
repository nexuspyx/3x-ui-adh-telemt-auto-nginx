# 3x-ui-pro — personal

🇷🇺 [Русская версия](README.md)

`personal` is a personal branch of the [mozaroc/3x-ui-pro](https://github.com/mozaroc/3x-ui-pro) fork used to test and operate custom improvements to the [3x-ui](https://github.com/MHSanaei/3x-ui) fresh installer.

Main focus:

- VLESS + REALITY
- VLESS + XHTTP
- nginx / SNI / TLS on a shared port 443
- modern and minimal Xray configuration
- automated validation of fresh-install templates

Primary test platform: **Ubuntu 26.04 LTS**.

Compatible with [Telemt WEB Manager](https://github.com/xPROMSx/telemt-web-manager).

> This branch is primarily intended for fresh installations and rebuilds. `x-ui-latest.sh` is not a safe in-place updater for an existing VPS.

## Installation

```bash
wget -qO x-ui-latest.sh https://raw.githubusercontent.com/xPROMSx/3x-ui-pro/personal/x-ui-latest.sh
bash x-ui-latest.sh
```

The latest stable 3x-ui release is installed by default.
