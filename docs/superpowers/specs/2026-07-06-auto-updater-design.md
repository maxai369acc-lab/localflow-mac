# Auto-updater via GitHub Releases — design

Date: 2026-07-06
Status: approved

## Goal

Ship LocalFlow updates over the air. The packaged app detects a newer
version on GitHub when online, notifies the user, and — on explicit user
action from the tray menu — downloads and installs it, so builds no longer
have to be sent around by hand.

## Decisions (from brainstorming)

- **Approach A**: self-contained swap helper. Rejected: Velopack/Squirrel
  (heavy toolchain for a personal app), download-only (keeps the manual step).
- **Notify only**: nothing downloads until the user clicks the tray item.
- **Host**: GitHub Releases on a public repo. Tag `vX.Y.Z`, one asset
  `LocalFlow-win64.zip` (zipped PyInstaller one-folder build).
- **Zipped folder**, not onefile exe: keeps startup fast and avoids
  re-extracting ~300 MB to the tight C: drive on every launch.
- Before the repo goes public: audit tracked files and git history for
  secrets/personal data.

## Components

### `src/localflow/updater.py` (new)

- `is_newer(remote: str, local: str) -> bool` — semver-ish comparison,
  tolerant of a leading `v` and missing parts.
- `UpdateChecker(repo, current_version, fetch=None)`:
  - `check() -> UpdateInfo | None` — GET
    `https://api.github.com/repos/{repo}/releases/latest` (anonymous, 10s
    timeout), returns `UpdateInfo(version, url, size)` when the tag is newer
    and a `LocalFlow-win64.zip` asset exists; `None` otherwise. All failures
    (offline, rate-limit, bad JSON) return `None` silently.
  - `fetch` injectable for tests.
- `Updater`:
  - `download_and_stage(info, updates_dir) -> Path` — stream the zip to
    `updates_dir`, extract, validate the staged folder contains
    `LocalFlow.exe`, return staged path.
  - `apply_and_restart(staged: Path) -> None` — write `swap.cmd` beside the
    install, spawn it detached, then ask the app to quit.
- `is_packaged() -> bool` — True only under PyInstaller
  (`sys.frozen`); source runs never check or update.

### swap.cmd (generated)

1. Poll up to 60s until `LocalFlow.exe` is deletable (app fully exited).
2. `rmdir` previous `LocalFlow.backup` if present.
3. Rename live folder -> `LocalFlow.backup`, staged folder -> live name.
4. Start new `LocalFlow.exe`.
5. On rename failure: rename backup back and start the old exe (never leave
   the user without a working app).

### Integration (`app.py`, `ui.py`)

- Background thread: first check ~30s after startup, then every 24h.
  Packaged builds only, and only if `update.check_enabled`.
- On update found: tray notification “Local Flow vX.Y.Z is available — use
  the tray menu to install” + tray menu item “Install update vX.Y.Z”.
- On click: notify “Downloading update…”, download+stage in a worker
  thread, then `apply_and_restart` (spawns helper, quits app). Errors
  notify “Update failed — will try again later” and clean the staging dir.
- Downloads land in `DATA_DIR/updates/` (D: drive), cleaned after success.

### Config (`config.py`)

`DEFAULTS["update"] = {"check_enabled": True, "repo": "<owner>/localflow"}`
(owner filled in once the GitHub repo exists).

### Versioning & release process

- Bump `src/localflow/__init__.py` `__version__` to `0.2.0`.
- Build: `uv run pyinstaller LocalFlow.spec`; zip `dist/LocalFlow` as
  `LocalFlow-win64.zip`.
- Publish: `gh release create vX.Y.Z LocalFlow-win64.zip` on the public
  repo. The releases page is the update feed; no extra manifest file.

## Pre-publish security audit

- `git ls-files` review: no tokens, passwords, personal data. App data
  (config.json, db, audio, logs) lives outside the repo in D:\AI.
- Grep tracked files and full history for secrets and the user's email.
- Un-ignore `LocalFlow.spec` (needed to build from a clone; not sensitive).

## Out of scope (YAGNI)

- Delta updates, signatures/hash pinning (HTTPS to GitHub is the trust
  anchor), macOS updater, release notes UI, downgrade support.

## Testing

- Unit (`tests/test_updater.py`): version comparison matrix; check() with
  faked fetch — newer/same/older tags, missing asset, network error, junk
  JSON; staged-folder validation.
- Local E2E before publishing: build a fake newer release zip, point the
  checker at a stubbed API response, run detect -> download -> stage ->
  swap -> relaunch on the real dist folder.
- Live E2E after publishing v0.2.0: run a copy of the old build pointed at
  the real repo and watch it self-update.
