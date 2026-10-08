# License scope and third-party notices

## xPROMSx contributions

Copyright (C) 2026 xPROMSx contributors.

Original developments and changes authored by xPROMSx contributors are offered under GNU GPL version 3 only (`GPL-3.0-only`); see [LICENSE](LICENSE). This grant covers only copyright held by those contributors. It does not grant permission to relicense inherited or third-party material, and does not establish GPL licensing or cleared redistribution rights for the repository as a whole. Previous authors retain their rights.

Project history records xPROMSx developments in the managed AdGuard component (`assets/adguard/managed.sh`), tests, CI and documentation, and changes to the installer and Backup/Restore. In files containing inherited material, the grant applies only to the contributors' own additions and changes; it is not a whole-file license declaration.

## Components and status

| Material | Origin / applicable terms |
| --- | --- |
| `x-ui-latest.sh`, `assets/backup/x-ui-backup.sh`, `assets/clash/clash.yaml` | Derived from [mozaroc/3x-ui-pro](https://github.com/mozaroc/3x-ui-pro). These files include inherited material and subsequent changes; permission to relicense the inherited material has not been established. |
| `assets/diagnostics/mtr-backend.py`, `assets/diagnostics/index.html` | Inherited from the same upstream; unchanged Git blobs at the reviewed revisions. Permission to relicense these files has not been established. |
| `assets/diagnostics/librespeed/speedtest.js`, `speedtest_worker.js` | [LibreSpeed](https://github.com/librespeed/speedtest), by Federico Dossena. The existing headers identify GNU LGPLv3. These files retain that license and attribution; see [LGPLv3 text](LICENSES/LGPL-3.0.txt) and the incorporated [GPLv3 text](LICENSE). They are supplied as JavaScript source, separately from the project license grant. |
| `assets/fake-sites/site-01/index.html` through `site-50/index.html` | All 50 files are inherited unchanged from mozaroc/3x-ui-pro at the reviewed revisions. The limited review found no explicit license grant or independently established original source. Visible copyright / “All rights reserved” text occurs in sites 09, 30, 40, 42, 45 and 50; its presence does not establish the identity of a rights holder. These pages are retained with unresolved permissions and are not declared xPROMSx-owned GPL material. |
| Official 3x-ui panel / CLI | [MHSanaei/3x-ui](https://github.com/MHSanaei/3x-ui) is downloaded separately from official releases. It has its own [GNU GPLv3 license](https://github.com/MHSanaei/3x-ui/blob/main/LICENSE); that license does not automatically license this fork's installer or assets. |
| Other separately obtained runtime software | Xray, nginx, AdGuard Home, Certbot and system packages retain their own upstream licenses and notices. Their distributions are not relicensed by this repository's LICENSE. |

## Limited provenance review

Reviewed on 2026-10-08 against project main `5bfeef4b943a6b0bceb9ed6fc7f91c9e6827c6c8` and upstream `a2c430cd6dec7c86d873dcda3544a61e7ac41144`. The upstream tree contains no LICENSE/COPYING file; the reviewed README files, CLAUDE.md and principal script headers provide no explicit redistribution or relicensing grant for the inherited material. Public availability and fork history are not treated as such a grant.

This is a bounded review of provenance and explicit notices, not a line-by-line audit. Unconfirmed permissions remain unconfirmed; recording them here does not resolve them. Future clarification can be documented without removing history or rewriting production code.
