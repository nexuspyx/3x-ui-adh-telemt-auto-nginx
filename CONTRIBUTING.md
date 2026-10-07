# Contributing to 3x-ui Auto Nginx

## Development

`main` is the maintained production line. Create `feat/*`, `fix/*`, `docs/*` or `chore/*` branches and open pull requests against `main`. Upstream is a reference source; do not automatically synchronize it.

Keep Russian and English README files consistent. Preserve upstream attribution. Do not introduce a new license for inherited code as part of repository maintenance.

## Required checks

Both checks must pass on the current PR revision:

- `Stack XHTTP and security` (`.github/workflows/stack-xhttp.yml`)
- `Stack Backup and restore` (`.github/workflows/stack-backup.yml`)

The jobs have unique names so GitHub can require them separately. The workflows run for pushes to `main`, pull requests targeting `main`, and manual `workflow_dispatch`. They have read-only repository permissions and no repository-secret dependency.

Reproduce the full suites on a disposable Ubuntu 24.04 test environment:

```bash
sudo apt-get update
sudo apt-get install -y --no-install-recommends nginx certbot sqlite3 openssl curl python3 python3-configobj python3-cryptography tar gzip shellcheck
mapfile -d '' -t scripts < <(git ls-files -z -- '*.sh')
for script in "${scripts[@]}"; do bash -n "$script"; done
shellcheck --severity=error "${scripts[@]}"
python3 tests/run_ci.py --check-completeness
NGINX_BIN=/usr/sbin/nginx python3 tests/run_ci.py --suite nonroot
export NGINX_BIN=/usr/sbin/nginx
sudo --preserve-env=NGINX_BIN python3 tests/run_ci.py --suite root
git diff --check
```

The canonical runner collects only TestCase classes defined in each `tests/test_*.py` module, so imported classes run once. Assign every new TestCase to exactly one suite in `tests/run_ci.py`; completeness fails for unassigned classes or stale/duplicate entries. New test methods in an existing class are included automatically. Do not use `unittest discover` as the CI entry point: existing imports would execute tests twice.

The `nonroot` suite requires EUID != 0; the `root` suite requires EUID 0 for ownership/permission coverage. Do not run both under sudo. Legacy entry commands remain available for compatibility but are not the canonical CI interface.

CI checks every tracked shell script with `bash -n` and ShellCheck **error-only**. Existing warning/info/style findings are not suppressed or claimed clean. All tracked workflow YAML is checked with actionlint 1.7.12, downloaded from its official release with a pinned SHA-256 verified before extraction; the workflow records the exact download and checksum.

Every confirmed bug must follow: **reproducing RED regression test → fix → same test PASS → full suite PASS**.

The suites use isolated fixtures and mock host services. Do not run `x-ui-latest.sh` to test syntax: it removes an existing installation. CI does not certify real network/client acceptance. Use a disposable VPS for live checks.

## Main protection

The main branch protection requires PRs, both checks and an up-to-date PR branch, with zero required external approvals. Force pushes and deletion are disallowed. Protection applies to administrators; no standing bypass actor is configured.

Emergency recovery is an explicit maintainer action: preserve the current commit, document the incident and exact intended change, and prefer a repair PR. If CI or protection configuration itself blocks recovery, the repository owner may temporarily amend the protection settings, perform the smallest repair, immediately restore the protections, rerun both checks and record the outcome. This is not permission for routine direct pushes or a permanently exempt automation account.

## Migration compatibility

The initial main checkpoint is `e02bd9e87768afb11b7cad18fc64d77ee637c3b3`, promoted by fast-forward from `personal` after PR #5.

`personal` and historical feature branches/tags/releases are preserved. Do not remove them as part of migration or routine maintenance. Old runtime paths and test identifiers are retained for Backup/Restore compatibility.

## Releases

Publish `vX.Y.Z` project releases from validated `main` with scope, test results and compatibility notes. Preserve historical `personal-v*` releases. Project versioning is separate from the upstream 3x-ui/Xray release versions. Pin the panel with `-version <tag>` when validating a deployment.

User-facing project releases may include a short companion-project footer linking to [Telegram Web Proxy Manager](https://github.com/xPROMSx/3x-ui-auto-nginx#telemt-web-manager); keep it secondary to the actual release changes.
