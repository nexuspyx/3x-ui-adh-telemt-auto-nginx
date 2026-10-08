<div align="center">

[🇷🇺 Русский](README.md) · [🇬🇧 English](README_EN.md) · [🇮🇷 فارسی](README_FA.md) · 🇨🇳 **简体中文**

# 🚀 3x-ui Auto Nginx

### 在自己的 VPS 上自动部署 3x-ui / Xray

**REALITY · XHTTP · Hysteria2 · WebSocket · gRPC · nginx · HTTPS · Backup / Restore**

两个域名，一台全新的 VPS，只需几分钟即可完成安装。

[![XHTTP / security](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-xhttp.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-xhttp.yml)
[![Backup / Restore](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-backup.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-backup.yml)
[![Ubuntu](https://img.shields.io/badge/Ubuntu-24.04%20%7C%2026.04-E95420?logo=ubuntu&logoColor=white)](#technical-details)
[![Releases](https://img.shields.io/github/v/release/xPROMSx/3x-ui-auto-nginx)](https://github.com/xPROMSx/3x-ui-auto-nginx/releases)

[发布版本](https://github.com/xPROMSx/3x-ui-auto-nginx/releases) · [Telegram Web Proxy Manager](#telemt-web-manager) · [问题反馈](https://github.com/xPROMSx/3x-ui-auto-nginx/issues)

</div>

**3x-ui Auto Nginx** 自动安装 [3x-ui](https://github.com/MHSanaei/3x-ui) 和 Xray，并配置 nginx、HTTPS、连接配置、订阅、网络诊断以及 Backup / Restore。安装时还可以选择启用带 DoH 的 AdGuard Home。

准备两个域名和一台全新的 VPS 即可。安装脚本会自动选择并部署一个伪装网站。

## ✨ 为什么选择这个项目？

| | |
| --- | --- |
| **预配置的连接方式**<br>REALITY、XHTTP、Hysteria2、WebSocket 和 Trojan gRPC 均已配置。 | **内部服务不直接暴露**<br>面板和订阅服务的内部端口不会直接暴露到互联网。 |
| **受限的 nginx 路由**<br>外部请求无法被代理到任意本地端口。 | **Backup / Restore v3**<br>回滚当前 VPS，或将备份恢复到新服务器，包括其他 VPS 服务商的服务器。 |
| **自动管理证书**<br>自动申请和续期 Let's Encrypt 证书，无需手动配置。 | **AdGuard Home + DoH**<br>按需启用，无需第三个域名。 |

<a id="installation"></a>

## 🚀 快速开始

配置**两个域名**的 DNS 记录，使它们指向你的 VPS IP 地址：一个用于面板，另一个用于 REALITY。通过 SSH 以 **root** 身份登录，并开放 TCP **80/443** 和 UDP **443**。查看[支持的系统](#technical-details)。

```bash
curl -fSL https://raw.githubusercontent.com/xPROMSx/3x-ui-auto-nginx/main/x-ui-latest.sh -o x-ui-latest.sh
bash x-ui-latest.sh
```

安装脚本会交互式询问两个域名，也可以通过参数指定：

```bash
bash x-ui-latest.sh -subdomain panel.example.com -reality_domain reality.example.com
```

> **⚠️ 仅用于全新安装或完全重装。** `x-ui-latest.sh` 会删除原有的 3x-ui 数据库和 nginx 配置。**不要用它来更新正在运行的 VPS。** 重装前请将备份保存到服务器之外。检测到现有安装时，重装或卸载必须输入大写的 `YES` 确认。

完成后会显示**面板地址、随机生成的登录凭据和诊断地址**（通过 3x-ui 认证的 MTR/LibreSpeed）。如安装 AdGuard Home，还会显示管理地址、密码和 DoH 地址。

## 🚀 连接配置

| 连接方式 | 状态 |
| --- | :---: |
| VLESS + REALITY | ✅ 可直接使用 |
| VLESS + XHTTP | ✅ 可直接使用 |
| Hysteria2 | ✅ 可直接使用 |
| VLESS + WebSocket | ✅ 可直接使用 |
| Trojan + gRPC | ✅ 可直接使用 |

五种连接方式均已预配置。按需在 3x-ui 中启用即可，无须修改 nginx。**客户端需要在面板中创建**；实际兼容性取决于客户端应用及其版本。

### 🔗 订阅

标准订阅、JSON 以及 **Mihomo / Clash** 通过 nginx 和 HTTPS 提供。添加 `provider=1` 参数可返回供代理提供者使用的原始订阅，而非完整的 Clash 配置。

## 🛡️ AdGuard Home + DoH

可选安装，默认选项为 **N**。无需第三个域名：管理界面位于面板域名下随机生成的 `/adg-.../` 路径，DoH 地址为 `https://panel.example.com/dns-query`。

安装脚本不会开放公网 TCP/UDP **53** 端口。AdGuard Home 的配置与数据包含在 **Backup / Restore v3** 中。

## 💾 备份与恢复

**Backup / Restore v3** 可用于回滚 VPS 或在重装系统后恢复到兼容的服务器。工具会随安装脚本一同安装，使用 root 身份运行：

```bash
# 创建备份
x-ui-backup backup

# 列出备份
x-ui-backup list

# 恢复指定备份
x-ui-backup restore /var/backups/x-ui/<archive>.tar.gz
```

备份文件保存在 `/var/backups/x-ui/`。

> **备份包含敏感数据：** 客户端数据库、密码和证书私钥。请在 **VPS 之外** 保存副本，并且只恢复来源可信的备份文件。

[在新 VPS 上恢复](#technical-details)。

## 🔐 安全性

服务接口不会直接暴露到互联网。面板、订阅和附加服务均通过 nginx 和 HTTPS 访问，内部端口仅在本机监听。

项目不会将请求任意代理到 localhost 端口。遇到关键错误时，安装或恢复过程会停止，而不是启动不完整的配置。

<a id="technical-details"></a>

<details>
<summary>⚙️ 技术细节与兼容性</summary>

- **系统：** Ubuntu 24.04、Ubuntu 26.04 和 Debian 13。不支持 Debian 12。
- **UFW：** 添加 80/tcp、443/tcp 和 443/udp 规则。仅在识别并允许 SSH 端口后才会启用原本未启用的 UFW；否则保持未启用并给出警告。恢复操作不会启用 UFW。
- **证书：** 通过 Let's Encrypt webroot 和系统 `certbot.timer` 自动续期，无需停止 nginx。
- **3x-ui 版本：** 默认安装经过验证的 v3.9.0，内含 Xray 26.9.30。可通过 `-version <tag>` 明确选择其他稳定版（最低 v3.8.0）；安装程序会提示该版本尚未验证兼容性。删除原有安装前会先校验安装压缩包。

**在新 VPS 上恢复：** 操作系统、版本和 CPU 架构应与备份对应。若 IP 改变，请更新 DNS。**不要运行** `x-ui-latest.sh`；仅安装备份工具，然后恢复可信的归档文件：

```bash
curl -fSL https://raw.githubusercontent.com/xPROMSx/3x-ui-auto-nginx/main/assets/backup/x-ui-backup.sh -o /tmp/x-ui-backup
install -o root -g root -m 0755 /tmp/x-ui-backup /usr/local/bin/x-ui-backup
rm -f /tmp/x-ui-backup
x-ui-backup restore /root/<archive>.tar.gz
```

SSH、系统配置及基础防火墙仍由管理员负责。对于 v2 备份，请使用对应旧版本中的恢复工具。

</details>

<a id="telemt-web-manager"></a>

## ✈️ Telegram Web Proxy Manager

[配套项目](https://github.com/xPROMSx/telegram-web-proxy-manager)，用于搭建自己的 Telegram WEB Proxy，支持 HTTPS、伪装网站以及带回滚机制的更新。

两个项目相互独立；**此安装脚本不会安装 Telegram Web Proxy Manager**。

## 🤝 致谢与项目来源

本项目基于 [3x-ui-pro](https://github.com/mozaroc/3x-ui-pro) 开发，并独立维护。Fork 之后，对 nginx/SNI 路由和 XHTTP 配置进行了大幅重构，重新设计了 TLS 证书签发与自动续期流程，并引入支持恢复和回滚的 Backup / Restore v3。此外还增加了运行于 UDP/443 的 Hysteria2，并修复了已发现的安全问题。

面板本身来自 [MHSanaei/3x-ui](https://github.com/MHSanaei/3x-ui)。第三方组件的权利及其现有许可仍归原作者所有。

<div align="center">

**两个域名，几分钟，搭建自己的 3x-ui / Xray 服务器。**

⭐ 如果这个安装脚本对你有帮助，欢迎在 [GitHub 上点个 Star](https://github.com/xPROMSx/3x-ui-auto-nginx)，让更多人发现它。

[安装](#installation) · [发布版本](https://github.com/xPROMSx/3x-ui-auto-nginx/releases) · [问题反馈](https://github.com/xPROMSx/3x-ui-auto-nginx/issues)

</div>
