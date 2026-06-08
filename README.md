# UpdateOnlineTool

NAS-based online update SDK and CLI for PyQt tools.

## First-version Scope

Supported:

- PyQt tool projects
- NAS release root
- OS-managed SMB credentials
- `latest.json`
- zip update package
- standalone updater executable

Not supported in the first version:

- Qt Installer Framework
- GitHub, Gitee, DevOps, or HTTP update sources
- API tokens, deploy keys, or account credentials in settings
- Electron, Rust, Tauri, or cross-framework adapters
- bundled GUI widgets

## Install

```bash
pip install -e D:\tealiving\peoject\UpdateOnlineTool
```

## Configure

Create `config/settings.json` from `config/settings.template.json`.

Windows NAS example:

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

macOS NAS example:

```json
{
  "nas": {
    "root": "/Volumes/release-share/UpdateOnlineTool"
  }
}
```

The package does not store NAS usernames or passwords. Windows uses Credential Manager or the current SMB session. macOS uses Keychain or an already-mounted SMB volume.

## NAS Layout

```text
<nas-root>/
└── <app-id>/
    ├── stable/
    │   └── latest.json
    └── v<version>/
        └── package.zip
```

`package.url` is a NAS-root-relative path:

```json
{
  "package": {
    "url": "automation-manual-studio/v1.0.6/package.zip",
    "size": 123456,
    "sha256": "..."
  }
}
```

## Publish

```bash
uot publish --app automation-manual-studio --version 1.0.6 --package dist/app.zip
```

## Verify

```bash
uot verify --app automation-manual-studio
```

## Check

```bash
uot check --app automation-manual-studio --current-version 1.0.5
```

## SDK Usage

```python
from update_online_tool import UpdateService

service = UpdateService.from_settings()
result = service.check(
    app_id="automation-manual-studio",
    current_version="1.0.5",
)
```

GUI projects own the UI, QThread wrappers, progress display, and user prompts. `update_online_tool` owns manifest parsing, version decisions, NAS package copy, checksum verification, pending manifest writing, and updater process launching.
