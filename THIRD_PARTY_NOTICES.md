# License scope and third-party notices

## xPROMSx contributions

Copyright (C) 2026 xPROMSx contributors.

Original developments and changes authored by xPROMSx contributors are offered under GNU GPL version 3 only (`GPL-3.0-only`); see [LICENSE](LICENSE). This grant covers only copyright held by those contributors. It does not grant permission to relicense inherited or third-party material, and does not establish GPL licensing or cleared redistribution rights for the repository as a whole. Previous authors retain their rights.

Project history records xPROMSx developments in the managed AdGuard component (`assets/adguard/managed.sh`), tests, CI and documentation, and changes to the installer and Backup/Restore. In files containing inherited material, the grant applies only to the contributors' own additions and changes; it is not a whole-file license declaration.

## Components and status

| Material | Origin / applicable terms |
| --- | --- |
| `x-ui-latest.sh`, `assets/backup/x-ui-backup.sh`, `assets/clash/clash.yaml` | Derived from [mozaroc/3x-ui-pro](https://github.com/mozaroc/3x-ui-pro). These files include inherited material and subsequent changes; permission to relicense the inherited material has not been established. |
| `assets/diagnostics/mtr-backend.py` | Inherited from [mozaroc/3x-ui-pro](https://github.com/mozaroc/3x-ui-pro), unchanged at the reviewed revisions; permission to relicense this backend has not been established. |
| `assets/diagnostics/index.html` | Original xPROMSx diagnostics interface (HTML, CSS and JavaScript), replacing the inherited UI. Copyright (C) 2026 xPROMSx contributors; offered under `GPL-3.0-only`, see [LICENSE](LICENSE). The LibreSpeed engine and MTR backend retain the separate statuses documented here. |
| `assets/diagnostics/librespeed/speedtest.js`, `speedtest_worker.js` | [LibreSpeed](https://github.com/librespeed/speedtest), by Federico Dossena. The existing headers identify GNU LGPLv3. These files retain that license and attribution; see [LGPLv3 text](LICENSES/LGPL-3.0.txt) and the incorporated [GPLv3 text](LICENSE). They are supplied as JavaScript source, separately from the project license grant. |
| `assets/fake-sites/site-01/index.html` through `site-10/index.html` | Ten original, self-contained HTML pages provided by xPROMSx, replacing the inherited cover templates. Copyright (C) 2026 xPROMSx contributors; offered under `GPL-3.0-only`, see [LICENSE](LICENSE). The former 50 inherited pages are absent from the current tree; historical revisions retain their original provenance and unresolved permissions. |
| Official 3x-ui panel / CLI | [MHSanaei/3x-ui](https://github.com/MHSanaei/3x-ui) is downloaded separately from official releases. It has its own [GNU GPLv3 license](https://github.com/MHSanaei/3x-ui/blob/main/LICENSE); that license does not automatically license this fork's installer or assets. |
| Other separately obtained runtime software | Xray, nginx, AdGuard Home, Certbot and system packages retain their own upstream licenses and notices. Their distributions are not relicensed by this repository's LICENSE. |

## Limited provenance review

Reviewed on 2026-10-08 against project main `5bfeef4b943a6b0bceb9ed6fc7f91c9e6827c6c8` and upstream `a2c430cd6dec7c86d873dcda3544a61e7ac41144`. The upstream tree contains no LICENSE/COPYING file; the reviewed README files, CLAUDE.md and principal script headers provide no explicit redistribution or relicensing grant for the inherited material. Public availability and fork history are not treated as such a grant.

This is a bounded review of provenance and explicit notices, not a line-by-line audit. Unconfirmed permissions remain unconfirmed; recording them here does not resolve them. Future clarification can be documented without removing history or rewriting production code.
