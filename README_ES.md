<div align="center">

[🇬🇧 English](README_EN.md) · [🇪🇬 العربية](README_AR.md) · [🇮🇷 فارسی](README_FA.md) · [🇨🇳 简体中文](README_ZH_CN.md) · 🇪🇸 **Español** · [🇷🇺 Русский](README.md) · [🇹🇷 Türkçe](README_TR.md)

# 🚀 3x-ui Auto Nginx

### Despliegue automático de 3x-ui / Xray en tu propio VPS

**REALITY · XHTTP · Hysteria2 · WebSocket · gRPC · nginx · HTTPS · Backup / Restore**

Dos dominios, un VPS limpio y unos minutos para completar la instalación.

[![XHTTP / security](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-xhttp.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-xhttp.yml)
[![Backup / Restore](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-backup.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-backup.yml)
[![Ubuntu](https://img.shields.io/badge/Ubuntu-24.04%20%7C%2026.04-E95420?logo=ubuntu&logoColor=white)](#technical-details)
[![Releases](https://img.shields.io/github/v/release/xPROMSx/3x-ui-auto-nginx)](https://github.com/xPROMSx/3x-ui-auto-nginx/releases)

[Versiones](https://github.com/xPROMSx/3x-ui-auto-nginx/releases) · [Telegram Web Proxy Manager](#telemt-web-manager) · [Incidencias](https://github.com/xPROMSx/3x-ui-auto-nginx/issues)

</div>

**3x-ui Auto Nginx** instala [3x-ui](https://github.com/MHSanaei/3x-ui) y Xray, y configura nginx, HTTPS, perfiles de conexión, suscripciones, diagnósticos de red y copias de seguridad y restauración. Durante la instalación también puedes activar AdGuard Home con DoH.

Solo necesitas dos dominios y un VPS limpio. El instalador selecciona y despliega automáticamente un sitio web de camuflaje.

## ✨ ¿Por qué elegir este proyecto?

| | |
| --- | --- |
| **Conexiones preconfiguradas**<br>REALITY, XHTTP, Hysteria2, WebSocket y Trojan gRPC ya vienen configurados. | **Servicios internos protegidos**<br>El panel y las suscripciones no exponen sus puertos internos directamente a Internet. |
| **Enrutamiento nginx restringido**<br>Las solicitudes no pueden redirigirse a puertos locales arbitrarios. | **Backup / Restore v3**<br>Recupera tu VPS o restaura una copia en un servidor nuevo, incluso de otro proveedor. |
| **Certificados automáticos**<br>Emisión y renovación de Let's Encrypt sin configuración manual. | **AdGuard Home + DoH**<br>Activación opcional durante la instalación, sin necesidad de un tercer dominio. |

<a id="installation"></a>

## 🚀 Inicio rápido

Configura los registros DNS de **dos dominios** para que apunten a la IP de tu VPS: uno para el panel y otro para REALITY. Conéctate por SSH como **root** y permite TCP **80/443** y UDP **443**. Consulta los [sistemas compatibles](#technical-details).

```bash
curl -fSL https://raw.githubusercontent.com/xPROMSx/3x-ui-auto-nginx/main/x-ui-latest.sh -o x-ui-latest.sh && bash x-ui-latest.sh
```

El instalador te pedirá ambos dominios. También puedes indicarlos directamente:

```bash
bash x-ui-latest.sh -subdomain panel.example.com -reality_domain reality.example.com
```

> **⚠️ Solo para instalaciones nuevas o reinstalaciones completas.** `x-ui-latest.sh` elimina la base de datos anterior de 3x-ui y la configuración de nginx. **No lo utilices para actualizar un VPS en funcionamiento.** Guarda primero una copia de seguridad fuera del servidor. Si detecta una instalación existente, la reinstalación o desinstalación requiere escribir exactamente `YES` en mayúsculas.

Al terminar recibirás la **URL del panel, credenciales aleatorias y la URL de diagnóstico** (MTR/LibreSpeed con autenticación mediante 3x-ui). Si instalas AdGuard Home, también verás su URL de administración, contraseña y dirección DoH.

## 🚀 Perfiles de conexión

| Conexión | Estado |
| --- | :---: |
| VLESS + REALITY | ✅ Listo para usar |
| VLESS + XHTTP | ✅ Listo para usar |
| Hysteria2 | ✅ Listo para usar |
| VLESS + WebSocket | ✅ Listo para usar |
| Trojan + gRPC | ✅ Listo para usar |

Los cinco perfiles vienen preconfigurados. Activa en 3x-ui los que necesites sin modificar nginx. **Crea los clientes en el panel**; la compatibilidad depende de la aplicación cliente y de su versión.

### 🔗 Suscripciones

Las suscripciones estándar, JSON y **Mihomo / Clash** se ofrecen mediante nginx y HTTPS. El parámetro `provider=1` devuelve la suscripción original para los proveedores de proxy, en lugar de una configuración Clash completa.

## 🛡️ AdGuard Home + DoH

Es opcional y está desactivado por defecto (**N**). No requiere un tercer dominio: la interfaz de administración utiliza una ruta aleatoria `/adg-.../` bajo el dominio del panel, y DoH está disponible en `https://panel.example.com/dns-query`.

El instalador no abre el puerto público TCP/UDP **53**. La configuración y los datos de AdGuard Home se incluyen en **Backup / Restore v3**.

## 💾 Copias de seguridad y restauración

**Backup / Restore v3** permite recuperar el estado de tu VPS o restaurarlo en un servidor compatible después de reinstalar el sistema operativo. La herramienta se instala automáticamente; ejecútala como root:

```bash
# Crear una copia de seguridad
x-ui-backup backup

# Mostrar las copias disponibles
x-ui-backup list

# Restaurar una copia concreta
x-ui-backup restore /var/backups/x-ui/<archive>.tar.gz
```

Los archivos se guardan en `/var/backups/x-ui/`.

> **Las copias contienen información sensible:** base de datos de clientes, contraseñas y claves privadas de certificados. Conserva una copia **fuera del VPS** y restaura únicamente archivos de confianza.

[Restauración en un VPS nuevo](#technical-details).

## 🔐 Seguridad

Las interfaces de los servicios no se exponen directamente a Internet. Se accede al panel, las suscripciones y los servicios adicionales mediante nginx y HTTPS, mientras que los puertos internos permanecen limitados al servidor.

No existe un proxy genérico hacia puertos arbitrarios de localhost. Si se produce un error crítico, la instalación o restauración se detiene en lugar de iniciar una configuración incompleta.

<a id="technical-details"></a>

<details>
<summary>⚙️ Detalles técnicos y compatibilidad</summary>

- **Sistemas:** Ubuntu 24.04, Ubuntu 26.04 y Debian 13. Debian 12 no es compatible.
- **UFW:** añade las reglas 80/tcp, 443/tcp y 443/udp. Si UFW está desactivado, solo se activa después de detectar y permitir el puerto SSH; de lo contrario, permanece desactivado y muestra una advertencia. La restauración nunca activa UFW.
- **Certificados:** Let's Encrypt con webroot y `certbot.timer` renuevan los certificados automáticamente sin detener nginx.
- **Versión de 3x-ui:** el instalador utiliza por defecto la versión más reciente cuya compatibilidad con 3x-ui Auto Nginx se ha comprobado exhaustivamente; puede no ser la última versión oficial publicada. Después puedes actualizar 3x-ui con su mecanismo integrado, aunque es posible que la nueva versión todavía no haya sido verificada con esta configuración. También puedes seleccionar expresamente otra versión estable mediante `-version <tag>` (mínimo compatible: v3.8.0); el instalador avisará si no se ha verificado su compatibilidad.

**Restauración en un VPS nuevo:** el sistema operativo, su versión y la arquitectura deben coincidir con los de la copia de seguridad. Si cambia la IP, actualiza el DNS. **No ejecutes** `x-ui-latest.sh`; instala únicamente la herramienta de copia y restaura un archivo de confianza:

```bash
curl -fSL https://raw.githubusercontent.com/xPROMSx/3x-ui-auto-nginx/main/assets/backup/x-ui-backup.sh -o /tmp/x-ui-backup
install -o root -g root -m 0755 /tmp/x-ui-backup /usr/local/bin/x-ui-backup
rm -f /tmp/x-ui-backup
x-ui-backup restore /root/<archive>.tar.gz
```

La administración de SSH, el sistema operativo y el firewall básico sigue siendo responsabilidad del administrador. Para archivos v2, utiliza la herramienta de la versión anterior correspondiente.

</details>

<a id="telemt-web-manager"></a>

## ✈️ Telegram Web Proxy Manager

[Un proyecto complementario](https://github.com/xPROMSx/telegram-web-proxy-manager) para crear tu propio Telegram WEB Proxy con HTTPS, un sitio de camuflaje y actualizaciones con posibilidad de reversión.

Los proyectos son independientes: **este instalador no instala Telegram Web Proxy Manager**.

## 🤝 Créditos y origen

Este proyecto se basa en [3x-ui-pro](https://github.com/mozaroc/3x-ui-pro), pero se mantiene de forma independiente. Desde la creación del fork se han rediseñado considerablemente el enrutamiento nginx/SNI y la configuración XHTTP, así como la emisión y renovación automática de certificados TLS. También se ha incorporado Backup / Restore v3 con recuperación y reversión, se ha añadido Hysteria2 sobre UDP/443 y se han corregido problemas de seguridad identificados.

El panel oficial procede de [MHSanaei/3x-ui](https://github.com/MHSanaei/3x-ui). Los autores de los componentes de terceros conservan sus derechos y licencias.

Las contribuciones originales y modificaciones de xPROMSx se distribuyen bajo [GNU GPL-3.0-only](LICENSE). Copyright (C) 2026 xPROMSx contributors. Esta licencia solo cubre los derechos que nos corresponden sobre esos materiales; no cambia la licencia del código heredado ni significa que todo el repositorio esté sujeto a GPL. Consulta el [alcance de la licencia y los avisos de terceros](THIRD_PARTY_NOTICES.md).

<div align="center">

**Dos dominios. Unos minutos. Tu propio servidor 3x-ui / Xray.**

⭐ Si este instalador te ha resultado útil, puedes darle una [estrella en GitHub](https://github.com/xPROMSx/3x-ui-auto-nginx) para ayudar a otros a descubrirlo.

[Instalación](#installation) · [Versiones](https://github.com/xPROMSx/3x-ui-auto-nginx/releases) · [Incidencias](https://github.com/xPROMSx/3x-ui-auto-nginx/issues)

</div>
