# PyQt NAS Online Updater Design

## Goal

Build `UpdateOnlineTool` into a reusable Python online-update package for PyQt tools that use a NAS release source. The first version supports only NAS-hosted `latest.json`, NAS-hosted zip packages, checksum verification, and launching the existing standalone updater executable.

This design deliberately excludes Qt Installer Framework, GitHub, Gitee, DevOps, Electron, Rust, and multi-source URL fallback.

## Scope

In scope:

- A pip-installable Python package named `update_online_tool`.
- A backend SDK with check, prepare, and launch operations.
- A CLI named `uot` for publishing and verifying NAS releases.
- NAS access through local filesystem paths, including Windows UNC paths and macOS mounted volumes.
- Existing OS-managed SMB credentials. The package does not store usernames, passwords, or API tokens.
- Migration of the current PyQt updater backend logic out of AutoMationManual into `UpdateOnlineTool`.

Out of scope:

- Qt IFW repository handling and MaintenanceTool launching.
- HTTP manifest sources.
- GitHub, Gitee, DevOps, API tokens, deploy keys, and release APIs.
- Rust, Electron, Tauri, or cross-framework client adapters.
- GUI widgets bundled inside `UpdateOnlineTool`.

## Architecture Brief

### Layer Ownership

Presentation stays in each tool project. For AutoMationManual this includes the settings page, update dialog, progress bar, user prompts, and QThread worker wrappers.

Application moves into `UpdateOnlineTool`. The package exposes `UpdateService.check()`, `UpdateService.prepare()`, and `UpdateService.launch()` as the main backend API.

Domain moves into `UpdateOnlineTool`. This includes manifest models, version comparison, update availability decisions, mandatory update rules, package metadata validation, and checksum rules.

Infrastructure moves into `UpdateOnlineTool`. This includes NAS path resolution, settings loading, package copy, streaming sha256 calculation, pending manifest writing, updater process launching, and the CLI.

### Dependency Direction

```text
PyQt GUI project
  -> update_online_tool SDK
      -> local/NAS filesystem
      -> standalone updater executable
```

`UpdateOnlineTool` must not import PyQt or any project-specific GUI package. GUI projects consume SDK responses and bind progress callbacks to their own thread and signal model.

### Data Contract Changes

`latest.json` remains the release manifest contract. The first NAS-only version supports:

- `schema_version`
- `app_id`
- `channel`
- `version`
- `mandatory`
- `min_supported_version`
- `published_at`
- `notes`
- `package.url`
- `package.size`
- `package.sha256`

IFW fields such as `installer_kind` and `repository_url` are removed from the active first-version contract.

`config/settings.json` stores release-source configuration only:

- NAS root path
- default app id
- default channel
- default minimum supported version
- updater executable name

It does not store credentials. Windows credentials are handled by Windows Credential Manager or the current SMB session. macOS credentials are handled by Keychain or an already-mounted SMB volume.

### Error Strategy

The SDK returns structured domain errors that GUI projects can display without parsing raw exception strings:

- `NAS_SOURCE_UNAVAILABLE`
- `SETTINGS_INVALID`
- `MANIFEST_NOT_FOUND`
- `MANIFEST_INVALID`
- `UPDATE_NOT_AVAILABLE`
- `PACKAGE_NOT_FOUND`
- `PACKAGE_SIZE_MISMATCH`
- `PACKAGE_HASH_MISMATCH`
- `UPDATER_NOT_FOUND`
- `UPDATER_LAUNCH_FAILED`
- `OPERATION_CANCELLED`

No-update is a normal result, not a failure. NAS unavailable, invalid manifest, package mismatch, and launch failure are errors.

### Testing Strategy

Unit tests use temporary directories to simulate the NAS root. They do not require a real NAS connection.

Coverage targets:

- settings parsing
- manifest parsing and validation
- version comparison
- update availability decisions
- NAS path resolution
- package copy progress callbacks
- cancellation during package copy
- size and sha256 validation
- pending manifest writing
- updater launcher command construction
- CLI `publish`, `verify`, and `check`

PyQt GUI projects should keep focused tests for worker signal wiring and dialog state changes. They should not duplicate SDK backend tests.

### Performance Strategy

Package copy and sha256 calculation are streaming operations using fixed-size chunks. The SDK must not load a full release package into memory.

`prepare()` accepts a progress callback and cancellation token. GUI callers must run `prepare()` outside the UI thread, typically inside their own QThread worker.

The first version does not use concurrent downloads or copy workers. NAS release packages are copied as a single stream to keep behavior predictable.

## Package Structure

```text
UpdateOnlineTool/
├── pyproject.toml
├── src/
│   └── update_online_tool/
│       ├── __init__.py
│       ├── cli.py
│       ├── downloader.py
│       ├── errors.py
│       ├── launcher.py
│       ├── manifest.py
│       ├── nas.py
│       ├── service.py
│       ├── settings.py
│       └── versioning.py
├── config/
│   └── settings.template.json
└── tests/
```

## Public SDK

The package exposes a small public API:

```python
from update_online_tool import UpdateService

service = UpdateService.from_settings()
result = service.check(
    app_id="automation-manual-studio",
    current_version="1.0.5",
    channel="stable",
)
```

Recommended operations:

- `UpdateService.from_settings(path=None)`
- `UpdateService.check(app_id, current_version, channel="stable", skipped_version=None)`
- `UpdateService.prepare(manifest, download_dir, progress=None, cancellation_token=None)`
- `UpdateService.launch(package_path, manifest, install_root, old_pid, restart_executable)`

The SDK models should be dataclasses with explicit fields so GUI projects can display results without depending on internal dictionaries.

## CLI Design

The CLI executable is `uot`.

Publish a release package to the NAS root and update `latest.json`:

```bash
uot publish --app automation-manual-studio --version 1.0.6 --package dist/app.zip
```

Verify one app's NAS manifests and packages:

```bash
uot verify --app automation-manual-studio
```

Check whether a specific app version has an update:

```bash
uot check --app automation-manual-studio --current-version 1.0.5
```

The CLI reads `config/settings.json` by default and accepts `--settings` for explicit configuration.

## NAS Layout

The NAS root follows the existing repository layout:

```text
<nas-root>/
└── <app-id>/
    ├── stable/
    │   └── latest.json
    └── v<version>/
        └── package.zip
```

`package.url` should remain a repository-relative path:

```json
{
  "package": {
    "url": "automation-manual-studio/v1.0.6/package.zip",
    "size": 123456,
    "sha256": "..."
  }
}
```

The SDK resolves relative package URLs against the configured NAS root.

## Settings Contract

First-version settings:

```json
{
  "nas": {
    "root": "\\\\nas-server\\release-share\\UpdateOnlineTool"
  },
  "publish": {
    "default_channel": "stable",
    "default_minimum_version": "1.0.0",
    "package_filename": "package.zip"
  },
  "updater": {
    "executable_name": "AutomationManualUpdater.exe"
  }
}
```

On macOS, `nas.root` can point to a mounted volume:

```json
{
  "nas": {
    "root": "/Volumes/release-share/UpdateOnlineTool"
  }
}
```

## Migration Plan Summary

Move from AutoMationManual into `UpdateOnlineTool`:

- manifest models
- version and rollout decision logic needed for first version
- update DTOs
- check update use case
- prepare update use case
- launch update use case
- package downloader and sha256 validation
- updater process launcher

Keep in AutoMationManual:

- settings page controls
- update dialog
- progress display
- user prompt copy
- QThread worker wrappers
- project-specific app id, version, install root, and restart executable values

After migration, AutoMationManual should call the SDK instead of owning update business logic.

## Acceptance Criteria

- `pip install -e D:\tealiving\peoject\UpdateOnlineTool` provides `update_online_tool` imports.
- `uot publish` copies a zip package into the configured NAS layout and updates `stable/latest.json`.
- `uot verify` validates manifest fields, package existence, package size, and sha256.
- `UpdateService.check()` returns no-update, optional-update, or mandatory-update results.
- `UpdateService.prepare()` copies the package from NAS to a local download directory, reports progress, supports cancellation, and verifies sha256.
- `UpdateService.launch()` starts the standalone updater executable with a pending manifest.
- No production code imports PyQt.
- No IFW code remains in the first-version SDK path.

