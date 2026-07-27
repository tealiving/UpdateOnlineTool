---
name: uot-nas-online-update
description: Add, package, publish, validate, or troubleshoot NAS-based desktop application updates with UpdateOnlineTool (UOT). Use for PyQt/PySide, Electron, Tauri, or other desktop hosts; for NAS settings, release contracts, Bootstrap/Agent handoff, uot-bridge integration, version switching, rollback, or UOT release validation.
---

# UOT NAS Online Update

Use UOT as the only update core. It owns NAS discovery, manifests, signatures,
package verification, `releases/<version>`, `current.json`, locks, diagnostics,
install/switch/rollback, Update Agent, and stable Bootstrap. Do not reimplement
these concerns in a GUI, Electron renderer, Tauri WebView, or application script.

## Start With the Runtime Choice

Identify the host and release layout before changing code:

- **PyQt/PySide and other Python GUI hosts:** call `DesktopUpdateClient`; new
  integrations use Agent + Bootstrap, while an existing standalone updater may
  remain on the documented legacy compatibility path.
- **Electron:** call `uot-bridge` only from the Main Process; expose a narrow
  preload IPC surface to the renderer. Start from
  `docs/electron-uot-reference.md`.
- **Tauri:** call `uot-bridge` only from a controlled Rust command. Validate the
  actual resource layout for each target platform.
- **Other hosts:** use the JSON bridge protocol; never construct updater commands
  from GUI input or edit `current.json` directly.

Read these UOT documents from the checked-out repository before implementation:

- `docs/multi-runtime-architecture.md` for the contracts and runtime boundary.
- `docs/integration-guide.md` for configuration and CLI workflows.
- `docs/user-guide.md` for operator deployment and NAS usage.
- `docs/pyqt-agent-migration.md` only for a Python/Qt migration.
- `docs/pyqt-integration.md` only for legacy PyQt updater compatibility.

## Required Handoff Sequence

For a Bootstrap/Agent release, use this order after a user confirms an update:

```text
check -> prepare -> agent-start (wait for ready) -> save host state
-> agent-handoff -> host exits -> Agent updates -> Bootstrap starts active release
```

`prepare` downloads and verifies; it does not switch versions. Do not exit the
host until `agent-start` reports ready. Once `agent-handoff` succeeds, do not
launch a release yourself. The Agent waits for the old PID, performs the UOT
transaction, and launches the stable Bootstrap.

## Release and Security Rules

- Publish `package.zip`, `latest.json`, and `versions.json` with `uot publish`;
  verify with `uot verify`. Keep NAS credentials and private signing keys out of
  settings and application packages.
- Include `uot-release.json` in new releases and validate it with
  `uot validate-release`. Declare settings and bridge files through
  `release_required_paths`.
- Keep Bootstrap and Agent at the stable install root. A release zip must contain
  only the versioned application release and its runtime resources.
- Create framework output ZIPs with `uot package-release` so UOT runs the shared
  cross-platform package plan before publishing. Do not use shell `zip` for Chinese
  `.app` paths, and do not duplicate reserved-name, Unicode-collision, or length
  rules in a host build script.
- New manifests and release contracts must use exactly `windows`, `macos`, or
  `linux`. Historical `current.json` platform aliases may be read only by the UOT
  compatibility boundary and are rewritten canonically on switch or rollback.
- Historical releases may omit `uot-release.json`; they still must pass version,
  entry type, required-path, symlink containment, and release-root checks.
- Use `agent-switch` and `agent-rollback` for local version selection; never
  write `current.json` from the host.
- Treat the legacy `uot-updater`, `_launcher/`, and `updater/` layout as a
  compatibility path only. Do not use it for a new Electron or Tauri integration.

## Validation

Use `scripts/check_uot_artifacts.py` to inspect an assembled install root. Pass
`--mode bootstrap-agent` for the current durable runtime, or `--mode legacy` for
an existing updater-sidecar release. For a same-platform legacy build, always
pass `--smoke-updater --updater-relative <path>` so the gate executes the actual
sidecar with `--help`; file existence alone cannot distinguish a true PyInstaller
onefile from an onedir bootloader missing its `_internal` runtime. When supplying
`updater_bundle`, pass a file only when it is independently executable; otherwise
pass the complete onedir directory and validate the nested updater entry.

Read `references/release-workflow.md` for a full release operation and
`references/troubleshooting.md` when installation, restart, NAS access, or
version switching fails.

Before handoff, verify the package and release contract. Test at least one real
old-process exit, Agent ready/handoff, `current.json` switch, stable Bootstrap
restart, local switch, and rollback on every shipped platform. For a legacy
release, a real old-version-to-new-version update is a publish blocker: confirm
that the updater writes status/result, installs `releases/<version>`, switches
`current.json`, and starts the selected entry. Do not substitute ZIP, signature,
manifest, or path-existence checks for this transaction.
