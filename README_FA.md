<div align="center">

[🇷🇺 Русский](README.md) · [🇬🇧 English](README_EN.md) · 🇮🇷 **فارسی** · [🇨🇳 简体中文](README_ZH_CN.md)

# 🚀 3x-ui Auto Nginx

### راه‌اندازی خودکار 3x-ui / Xray روی VPS شخصی

**REALITY · XHTTP · Hysteria2 · WebSocket · gRPC · nginx · HTTPS · Backup / Restore**

دو دامنه، یک VPS تازه و تنها چند دقیقه برای نصب.

[![XHTTP / security](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-xhttp.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-xhttp.yml)
[![Backup / Restore](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-backup.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-backup.yml)
[![Ubuntu](https://img.shields.io/badge/Ubuntu-24.04%20%7C%2026.04-E95420?logo=ubuntu&logoColor=white)](#technical-details)
[![Releases](https://img.shields.io/github/v/release/xPROMSx/3x-ui-auto-nginx)](https://github.com/xPROMSx/3x-ui-auto-nginx/releases)

[نسخه‌های منتشرشده](https://github.com/xPROMSx/3x-ui-auto-nginx/releases) · [Telegram Web Proxy Manager](#telemt-web-manager) · [گزارش مشکل](https://github.com/xPROMSx/3x-ui-auto-nginx/issues)

</div>

**3x-ui Auto Nginx** پنل [3x-ui](https://github.com/MHSanaei/3x-ui) و Xray را نصب می‌کند و nginx، HTTPS، پروفایل‌های اتصال، اشتراک‌ها، ابزارهای عیب‌یابی و Backup / Restore را پیکربندی می‌کند. در صورت نیاز می‌توانید هنگام نصب AdGuard Home همراه با DoH را نیز فعال کنید.

تنها به دو دامنه و یک VPS تازه نیاز دارید. اسکریپت به‌صورت خودکار یک وب‌سایت پوششی انتخاب و راه‌اندازی می‌کند.

## ✨ چرا این پروژه؟

| | |
| --- | --- |
| **اتصال‌های از پیش پیکربندی‌شده**<br>REALITY، XHTTP، Hysteria2، WebSocket و Trojan gRPC آماده‌اند. | **سرویس‌های داخلی غیرعمومی**<br>پورت‌های داخلی پنل و سرویس اشتراک مستقیماً در اینترنت در دسترس نیستند. |
| **مسیریابی محدود nginx**<br>درخواست‌ها به پورت‌های دلخواه localhost هدایت نمی‌شوند. | **Backup / Restore v3**<br>بازگشت به وضعیت پیشین VPS یا بازیابی روی سرور جدید، حتی نزد ارائه‌دهنده میزبانی دیگر. |
| **گواهی‌های خودکار**<br>صدور و تمدید گواهی Let's Encrypt بدون تنظیم دستی انجام می‌شود. | **AdGuard Home + DoH**<br>اختیاری است و به دامنه سوم نیازی ندارد. |

<a id="installation"></a>

## 🚀 شروع سریع

رکوردهای DNS **دو دامنه** را طوری تنظیم کنید که به نشانی IP سرور VPS شما اشاره کنند: یکی برای پنل و دیگری برای REALITY. با SSH و کاربر **root** وارد شوید و پورت‌های TCP **80/443** و UDP **443** را باز کنید. [سیستم‌عامل‌های پشتیبانی‌شده](#technical-details).

```bash
curl -fSL https://raw.githubusercontent.com/xPROMSx/3x-ui-auto-nginx/main/x-ui-latest.sh -o x-ui-latest.sh && bash x-ui-latest.sh
```

اسکریپت هنگام نصب هر دو دامنه را می‌پرسد. همچنین می‌توانید آن‌ها را مستقیماً مشخص کنید:

```bash
bash x-ui-latest.sh -subdomain panel.example.com -reality_domain reality.example.com
```

> **⚠️ فقط برای نصب تازه یا نصب مجدد کامل.** اجرای `x-ui-latest.sh` پایگاه داده فعلی 3x-ui و تنظیمات nginx را حذف می‌کند. **برای به‌روزرسانی VPS در حال کار از این اسکریپت استفاده نکنید.** پیش از نصب مجدد، یک نسخه پشتیبان خارج از سرور ذخیره کنید. در صورت شناسایی نصب قبلی، نصب مجدد یا حذف برنامه به تأیید با عبارت `YES` (با حروف بزرگ) نیاز دارد.

در پایان، **آدرس پنل، اطلاعات ورود تصادفی و نشانی عیب‌یابی** (MTR/LibreSpeed با احراز هویت 3x-ui) نمایش داده می‌شود. در صورت انتخاب AdGuard Home، آدرس مدیریت، گذرواژه و نشانی DoH نیز ارائه می‌شوند.

## 🚀 پروفایل‌های اتصال

| اتصال | وضعیت |
| --- | :---: |
| VLESS + REALITY | ✅ آماده استفاده |
| VLESS + XHTTP | ✅ آماده استفاده |
| Hysteria2 | ✅ آماده استفاده |
| VLESS + WebSocket | ✅ آماده استفاده |
| Trojan + gRPC | ✅ آماده استفاده |

هر پنج پروفایل از پیش پیکربندی شده‌اند. هرکدام را که می‌خواهید در 3x-ui فعال کنید؛ نیازی به تغییر nginx نیست. **کاربران را در پنل ایجاد کنید**؛ سازگاری به برنامه کلاینت و نسخه آن بستگی دارد.

### 🔗 اشتراک‌ها

اشتراک‌های استاندارد، JSON و **Mihomo / Clash** از طریق nginx و HTTPS ارائه می‌شوند. پارامتر `provider=1` به‌جای پیکربندی کامل Clash، اشتراک اصلی مناسب برای proxy provider را برمی‌گرداند.

## 🛡️ AdGuard Home + DoH

نصب اختیاری است و مقدار پیش‌فرض **N** است. نیازی به دامنه سوم ندارید: پنل مدیریت در یک مسیر تصادفی مانند `/adg-.../` زیر دامنه پنل قرار می‌گیرد و DoH در `https://panel.example.com/dns-query` در دسترس است.

اسکریپت پورت عمومی TCP/UDP **53** را باز نمی‌کند. تنظیمات و داده‌های AdGuard Home در **Backup / Restore v3** گنجانده شده‌اند.

## 💾 پشتیبان‌گیری و بازیابی

**Backup / Restore v3** امکان بازگشت به وضعیت پیشین VPS یا بازیابی روی سرور سازگار پس از نصب مجدد سیستم‌عامل را فراهم می‌کند. این ابزار همراه با نصب سرور نصب می‌شود و باید با دسترسی root اجرا شود:

```bash
# ایجاد نسخه پشتیبان
x-ui-backup backup

# نمایش نسخه‌های پشتیبان
x-ui-backup list

# بازیابی یک نسخه پشتیبان
x-ui-backup restore /var/backups/x-ui/<archive>.tar.gz
```

فایل‌های پشتیبان در `/var/backups/x-ui/` ذخیره می‌شوند.

> **نسخه‌های پشتیبان حاوی اطلاعات حساس‌اند:** پایگاه داده کاربران، گذرواژه‌ها و کلیدهای خصوصی گواهی‌ها. یک نسخه را **خارج از VPS** نگه دارید و فقط فایل‌های پشتیبان قابل اعتماد را بازیابی کنید.

[بازیابی روی VPS جدید](#technical-details).

## 🔐 امنیت

رابط‌های سرویس‌ها مستقیماً در اینترنت منتشر نمی‌شوند. پنل، اشتراک‌ها و سرویس‌های جانبی از طریق nginx و HTTPS در دسترس‌اند و پورت‌های داخلی فقط روی میزبان محلی گوش می‌دهند.

پروکسی عمومی به پورت‌های دلخواه localhost وجود ندارد. در صورت بروز خطای مهم، نصب یا بازیابی متوقف می‌شود تا پیکربندی ناقص اجرا نشود.

<a id="technical-details"></a>

<details>
<summary>⚙️ جزئیات فنی و سازگاری</summary>

- **سیستم‌عامل‌ها:** Ubuntu 24.04، Ubuntu 26.04 و Debian 13. نسخه Debian 12 پشتیبانی نمی‌شود.
- **UFW:** قوانین 80/tcp، 443/tcp و 443/udp افزوده می‌شوند. UFW غیرفعال فقط پس از شناسایی و مجاز کردن پورت SSH فعال خواهد شد؛ در غیر این صورت غیرفعال می‌ماند و هشدار داده می‌شود. بازیابی هیچ‌گاه UFW را فعال نمی‌کند.
- **گواهی‌ها:** گواهی‌های Let's Encrypt با روش webroot و تایمر استاندارد `certbot.timer` بدون توقف nginx تمدید می‌شوند.
- **نسخه 3x-ui:** به‌صورت پیش‌فرض جدیدترین نسخه‌ای نصب می‌شود که سازگاری آن با 3X-UI AUTO NGINX به‌دقت آزمایش شده است؛ این نسخه لزوماً آخرین انتشار رسمی 3x-ui نیست. پس از نصب می‌توانید 3x-ui را با روش به‌روزرسانی داخلی خودش ارتقا دهید، اما ممکن است سازگاری نسخه جدید با پیکربندی فعلی هنوز تأیید نشده باشد. همچنین می‌توانید با `-version <tag>` نسخه پایدار دیگری را انتخاب کنید (حداقل نسخه پشتیبانی‌شده v3.8.0 است)؛ نصب‌کننده درباره تأیید نشدن سازگاری هشدار می‌دهد.

**بازیابی روی VPS جدید:** سیستم‌عامل، نسخه آن و معماری پردازنده باید با نسخه پشتیبان مطابقت داشته باشند. در صورت تغییر IP، رکوردهای DNS را به‌روزرسانی کنید. **اسکریپت `x-ui-latest.sh` را اجرا نکنید**؛ فقط ابزار بازیابی را نصب کرده و یک آرشیو معتبر را بازیابی کنید:

```bash
curl -fSL https://raw.githubusercontent.com/xPROMSx/3x-ui-auto-nginx/main/assets/backup/x-ui-backup.sh -o /tmp/x-ui-backup
install -o root -g root -m 0755 /tmp/x-ui-backup /usr/local/bin/x-ui-backup
rm -f /tmp/x-ui-backup
x-ui-backup restore /root/<archive>.tar.gz
```

SSH، تنظیمات سیستم‌عامل و فایروال پایه همچنان بر عهده مدیر سرور است. برای آرشیوهای v2 باید از ابزار نسخه قدیمی متناظر استفاده شود.

</details>

<a id="telemt-web-manager"></a>

## ✈️ Telegram Web Proxy Manager

[پروژه مکمل](https://github.com/xPROMSx/telegram-web-proxy-manager) برای راه‌اندازی Telegram WEB Proxy شخصی با HTTPS، وب‌سایت پوششی و به‌روزرسانی با امکان بازگشت به نسخه قبلی.

این دو پروژه مستقل‌اند؛ **این نصب‌کننده، Telegram Web Proxy Manager را نصب نمی‌کند**.

## 🤝 خاستگاه پروژه و قدردانی

این پروژه بر پایه [3x-ui-pro](https://github.com/mozaroc/3x-ui-pro) ساخته شده و به‌صورت مستقل نگهداری می‌شود. پس از فورک، مسیریابی nginx/SNI و پیکربندی XHTTP به‌طور گسترده بازطراحی شده‌اند، فرایند صدور و تمدید خودکار گواهی‌های TLS اصلاح شده و Backup / Restore v3 با قابلیت بازیابی و بازگشت به وضعیت پیشین ارائه شده است. Hysteria2 روی UDP/443 اضافه شده و مشکلات امنیتی شناسایی‌شده برطرف شده‌اند.

پنل اصلی از [MHSanaei/3x-ui](https://github.com/MHSanaei/3x-ui) ارائه می‌شود. حقوق نویسندگان اجزای شخص ثالث و مجوزهای موجود آن‌ها محفوظ است.

<div align="center">

**دو دامنه، چند دقیقه و سرور 3x-ui / Xray شخصی شما.**

⭐ اگر این نصب‌کننده برایتان مفید بود، با دادن [Star در GitHub](https://github.com/xPROMSx/3x-ui-auto-nginx) از پروژه حمایت کنید تا دیگران راحت‌تر آن را پیدا کنند.

[نصب](#installation) · [نسخه‌های منتشرشده](https://github.com/xPROMSx/3x-ui-auto-nginx/releases) · [گزارش مشکل](https://github.com/xPROMSx/3x-ui-auto-nginx/issues)

</div>
