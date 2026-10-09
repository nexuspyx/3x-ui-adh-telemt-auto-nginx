<div align="center">

[🇷🇺 Русский](README_RU.md) · [🇬🇧 English](README.md) · 🇪🇬 **العربية** · [🇮🇷 فارسی](README_FA.md) · [🇨🇳 简体中文](README_ZH_CN.md) · [🇪🇸 Español](README_ES.md) · [🇹🇷 Türkçe](README_TR.md)

# 🚀 3x-ui Auto Nginx

### نشر 3x-ui / Xray تلقائيًا على خادم VPS الخاص بك

**REALITY · XHTTP · Hysteria2 · WebSocket · gRPC · nginx · HTTPS · Backup / Restore**

نطاقان وخادم VPS جديد وبضع دقائق لإكمال التثبيت.

[![XHTTP / security](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-xhttp.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-xhttp.yml)
[![Backup / Restore](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-backup.yml/badge.svg?branch=main)](https://github.com/xPROMSx/3x-ui-auto-nginx/actions/workflows/stack-backup.yml)
[![Ubuntu](https://img.shields.io/badge/Ubuntu-24.04%20%7C%2026.04-E95420?logo=ubuntu&logoColor=white)](#technical-details)
[![Releases](https://img.shields.io/github/v/release/xPROMSx/3x-ui-auto-nginx)](https://github.com/xPROMSx/3x-ui-auto-nginx/releases)

[الإصدارات](https://github.com/xPROMSx/3x-ui-auto-nginx/releases) · [Telegram Web Proxy Manager](#telemt-web-manager) · [الإبلاغ عن المشكلات](https://github.com/xPROMSx/3x-ui-auto-nginx/issues)

</div>

يُثبّت **3x-ui Auto Nginx** لوحة [3x-ui](https://github.com/MHSanaei/3x-ui) وXray، ويضبط nginx وHTTPS وملفات الاتصال والاشتراكات وأدوات تشخيص الشبكة والنسخ الاحتياطي والاستعادة. ويمكنك اختيار تثبيت AdGuard Home مع DoH أثناء الإعداد.

كل ما تحتاج إليه نطاقان وخادم VPS جديد. يختار المُثبّت موقع تمويه وينشره تلقائيًا.

## ✨ لماذا هذا المشروع؟

| | |
| --- | --- |
| **اتصالات مُعدّة مسبقًا**<br>إعدادات REALITY وXHTTP وHysteria2 وWebSocket وTrojan gRPC جاهزة. | **خدمات داخلية غير مكشوفة**<br>لا تُعرَض منافذ اللوحة وخدمة الاشتراكات الداخلية مباشرةً للإنترنت. |
| **توجيه nginx مقيّد**<br>لا يمكن تمرير الطلبات إلى منافذ محلية عشوائية. | **Backup / Restore v3**<br>استعادة حالة خادمك أو نقل النسخة الاحتياطية إلى خادم جديد، حتى لدى مزوّد استضافة مختلف. |
| **شهادات تلقائية**<br>إصدار شهادات Let's Encrypt وتجديدها دون إعداد يدوي. | **AdGuard Home + DoH**<br>خيار إضافي أثناء التثبيت، ولا يتطلب نطاقًا ثالثًا. |

<a id="installation"></a>

## 🚀 بدء التثبيت

وجّه سجلات DNS الخاصة بـ **نطاقين** إلى عنوان IP لخادمك: أحدهما للوحة والآخر لاتصالات REALITY. اتصل عبر SSH بحساب **root** واسمح بالوصول عبر TCP **80/443** وUDP **443**. راجع [الأنظمة المدعومة](#technical-details).

```bash
curl -fSL https://raw.githubusercontent.com/xPROMSx/3x-ui-auto-nginx/main/x-ui-latest.sh -o x-ui-latest.sh && bash x-ui-latest.sh
```

سيطلب المُثبّت إدخال النطاقين، ويمكنك أيضًا تحديدهما في الأمر:

```bash
bash x-ui-latest.sh -subdomain panel.example.com -reality_domain reality.example.com
```

> **⚠️ للتثبيت الجديد أو إعادة التثبيت الكاملة فقط.** يحذف `x-ui-latest.sh` قاعدة بيانات 3x-ui وإعدادات nginx السابقة. **لا تستخدمه لتحديث خادم VPS يعمل حاليًا.** احتفظ أولًا بنسخة احتياطية خارج الخادم. عند اكتشاف تثبيت موجود، تتطلب إعادة التثبيت أو الإزالة إدخال `YES` بأحرف كبيرة تمامًا.

عند الانتهاء ستظهر **رابط اللوحة وبيانات دخول عشوائية ورابط التشخيص** (MTR/LibreSpeed مع تسجيل الدخول عبر 3x-ui). وإذا ثبّتَّ AdGuard Home فستظهر أيضًا لوحة إدارته وكلمة المرور وعنوان DoH.

## 🚀 ملفات الاتصال

| الاتصال | الحالة |
| --- | :---: |
| VLESS + REALITY | ✅ جاهز للاستخدام |
| VLESS + XHTTP | ✅ جاهز للاستخدام |
| Hysteria2 | ✅ جاهز للاستخدام |
| VLESS + WebSocket | ✅ جاهز للاستخدام |
| Trojan + gRPC | ✅ جاهز للاستخدام |

ملفات الاتصال الخمسة مُعدّة مسبقًا. فعّل ما تحتاج إليه من لوحة 3x-ui دون تعديل nginx. **أنشئ العملاء من داخل اللوحة**؛ وتعتمد التوافقية على تطبيق العميل وإصداره.

### 🔗 الاشتراكات

تُقدَّم الاشتراكات العادية وJSON و**Mihomo / Clash** عبر nginx وHTTPS. يعيد المعامل `provider=1` الاشتراك الأصلي لمزوّدي البروكسي بدلًا من ملف إعداد Clash الكامل.

## 🛡️ AdGuard Home + DoH

تثبيته اختياري، والقيمة الافتراضية **N**. لا حاجة لنطاق ثالث: تستخدم واجهة الإدارة مسارًا عشوائيًا يبدأ بـ `/adg-.../` على نطاق اللوحة، وتتوفر خدمة DoH على `https://panel.example.com/dns-query`.

لا يفتح المُثبّت المنفذ العام TCP/UDP **53**. وتتضمن **Backup / Restore v3** إعدادات AdGuard Home وبياناته.

## 💾 النسخ الاحتياطي والاستعادة

تتيح **Backup / Restore v3** استرجاع حالة VPS أو استعادة النظام على خادم متوافق بعد إعادة تثبيت نظام التشغيل. تُثبَّت الأداة مع المشروع؛ شغّل الأوامر بحساب root:

```bash
# إنشاء نسخة احتياطية
x-ui-backup backup

# عرض النسخ الاحتياطية
x-ui-backup list

# استعادة نسخة محددة
x-ui-backup restore /var/backups/x-ui/<archive>.tar.gz
```

تُحفظ الأرشيفات في `/var/backups/x-ui/`.

> **تحتوي النسخ الاحتياطية على بيانات حساسة:** قاعدة بيانات العملاء وكلمات المرور والمفاتيح الخاصة للشهادات. احتفظ بنسخة **خارج VPS** ولا تستعد إلا أرشيفات موثوقة.

[الاستعادة على خادم VPS جديد](#technical-details).

## 🔐 الأمان

لا تُعرَض واجهات الخدمات مباشرةً على الإنترنت. تُتاح اللوحة والاشتراكات والخدمات الإضافية عبر nginx وHTTPS، بينما تظل المنافذ الداخلية محلية.

لا يوجد مسار بروكسي عام يتيح التوجيه إلى منافذ localhost عشوائية. عند حدوث خطأ حرج تتوقف عملية التثبيت أو الاستعادة بدلًا من تشغيل إعداد غير مكتمل.

<a id="technical-details"></a>

<details>
<summary>⚙️ التفاصيل التقنية والتوافقية</summary>

- **الأنظمة:** Ubuntu 24.04 وUbuntu 26.04 وDebian 13. لا يُدعم Debian 12.
- **UFW:** يضيف القواعد 80/tcp و443/tcp و443/udp. لا يفعّل UFW غير النشط إلا بعد اكتشاف منفذ SSH والسماح به؛ وإلا يبقى غير نشط مع إظهار تحذير. لا تفعّل عملية الاستعادة UFW.
- **الشهادات:** يجري إصدار شهادات Let's Encrypt وتجديدها تلقائيًا باستخدام webroot و`certbot.timer`، دون إيقاف nginx.
- **إصدار 3x-ui:** يثبّت المُثبّت افتراضيًا أحدث إصدار جرى التحقق جيدًا من توافقه مع 3x-ui Auto Nginx، وقد لا يكون أحدث إصدار صادر عن المشروع الأصلي. يمكنك تحديث 3x-ui لاحقًا بآليته المدمجة، لكن قد لا يكون توافق الإصدار الجديد مع إعداداتنا قد اختُبر بعد. ويمكنك اختيار إصدار مستقر آخر صراحةً باستخدام `-version <tag>` (الحد الأدنى المدعوم v3.8.0)؛ وسيظهر تحذير إذا لم يُتحقق من توافقه.

**الاستعادة على خادم VPS جديد:** يجب أن تتطابق توزيعة نظام التشغيل وإصداره ومعمارية المعالج مع النسخة الاحتياطية. حدّث سجلات DNS إذا تغير عنوان IP. **لا تشغّل** `x-ui-latest.sh`؛ ثبّت أداة النسخ الاحتياطي فقط، ثم استعد أرشيفًا موثوقًا:

```bash
curl -fSL https://raw.githubusercontent.com/xPROMSx/3x-ui-auto-nginx/main/assets/backup/x-ui-backup.sh -o /tmp/x-ui-backup
install -o root -g root -m 0755 /tmp/x-ui-backup /usr/local/bin/x-ui-backup
rm -f /tmp/x-ui-backup
x-ui-backup restore /root/<archive>.tar.gz
```

تبقى إدارة SSH ونظام التشغيل وجدار الحماية الأساسي مسؤولية مدير الخادم. لاستعادة أرشيفات v2، استخدم أداة الإصدار الأقدم المطابق.

</details>

<a id="telemt-web-manager"></a>

## ✈️ Telegram Web Proxy Manager

[مشروع مرافق](https://github.com/xPROMSx/telegram-web-proxy-manager) لإنشاء Telegram WEB Proxy خاص بك، مع HTTPS وموقع تمويه وتحديثات تدعم التراجع عند حدوث خطأ.

المشروعان مستقلان؛ **هذا المُثبّت لا يثبّت Telegram Web Proxy Manager**.

## 🤝 الشكر وأصل المشروع

يستند المشروع إلى [3x-ui-pro](https://github.com/mozaroc/3x-ui-pro)، ويُطوَّر ويُصان بصورة مستقلة. بعد إنشاء التفريع، أُعيدت هندسة توجيه nginx/SNI وإعداد XHTTP بشكل واسع، وأُعيد تصميم إصدار شهادات TLS وتجديدها التلقائي، وأضيفت Backup / Restore v3 مع إمكانات الاستعادة والتراجع. كما أضيف Hysteria2 على UDP/443 وعولجت مشكلات أمنية مكتشفة.

اللوحة الأصلية من [MHSanaei/3x-ui](https://github.com/MHSanaei/3x-ui). يحتفظ مؤلفو المكونات الخارجية بحقوقهم وتراخيصهم الحالية.

تُتاح مساهمات xPROMSx الأصلية وتعديلاته بموجب [GNU GPL-3.0-only](LICENSE). Copyright (C) 2026 xPROMSx contributors. ينطبق الترخيص فقط على الحقوق التي نملكها في هذه المواد، ولا يعيد ترخيص الشيفرة الموروثة أو يثبت أن المستودع بأكمله خاضع لـ GPL. راجع [إشعارات المكونات الخارجية ونطاق الترخيص](THIRD_PARTY_NOTICES.md).

<div align="center">

**نطاقان. بضع دقائق. خادم 3x-ui / Xray خاص بك.**

⭐ إذا أفادك المُثبّت، فادعم المشروع بإضافة [نجمة على GitHub](https://github.com/xPROMSx/3x-ui-auto-nginx) لتساعد الآخرين على اكتشافه.

[التثبيت](#installation) · [الإصدارات](https://github.com/xPROMSx/3x-ui-auto-nginx/releases) · [المشكلات](https://github.com/xPROMSx/3x-ui-auto-nginx/issues)

</div>
