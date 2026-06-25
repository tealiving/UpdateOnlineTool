---
name: pyqt-nas-online-update
description: PyQt + NAS + UpdateOnlineTool online update workflow. Use when a user wants to add, adapt, package, publish, verify, or troubleshoot a PyQt desktop application's NAS-based online update capability using UOT; when configuring project-local update-endpoint.json or config/settings.json; when building PyInstaller install/update directories; when publishing package.zip/latest.json to NAS; or when validating launcher/current.json/updater/update-result.json behavior.
---

# PyQt NAS Online Update

## Scope

Use this skill to help a PyQt desktop project use UpdateOnlineTool (UOT) for NAS-based online updates.

Keep the workflow project-agnostic. Do not hardcode one repository's app id, product name, NAS path, version, spec file, virtual environment, or executable name unless the user explicitly provides them.

## Workflow

1. Establish variables:
   - `project_root`
   - `python_exe` or virtualenv path
   - `app_id`
   - `platform` (`windows`, `macos`, or `linux`)
   - `product_name`
   - `app_exe`
   - `updater_exe`
   - `pyinstaller_spec`
   - `current_version`
   - `target_version`
   - `nas_root`
   - `settings_file`, normally `config/settings.json`
   - `endpoint_file`, normally `update-endpoint.json`

2. Check project boundaries:
   - The PyQt project owns GUI update prompts, user-facing progress, application API, tool-specific launcher/updater runtime, and version source.
   - UOT owns NAS settings parsing, NAS read/write probing, latest/package publish and verify, package copy/download, checksum validation, pending manifest writing, updater launch, and standard PyInstaller assembly.
   - Avoid reintroducing generic manifest generators, generic downloaders, generic SHA verifiers, or provider-specific token logic into the PyQt tool project.

3. Initialize project configuration:
   - Prefer project-local settings for build defaults: `config/settings.json`.
   - Prefer a project update endpoint file: `update-endpoint.json`.
   - Use `uot init` with `--nas-root` so NAS read/write checks run.
   - Use `--skip-nas-check` only for offline scaffolding.
   - Use `uot migrate-install-root` when converting an existing flat install directory into `releases/<version>` plus `current.json`.
   - Package `update-endpoint.json` with the app when the GUI reads it at runtime.
   - Pass `config/settings.json` to `uot assemble-pyinstaller --settings`; if not using UOT assembly, copy it to the equivalent runtime config path yourself.
   - Do not prepackage `latest.json`, `pending-update.json`, `update-result.json`, `update-status.json`, or logs. `current.json` is generated in the assembled install root, not maintained as source config.

4. Build and assemble:
   - Build with PyInstaller from the project root.
   - Keep GUI executable naming separate from launcher internal naming when the project uses a launcher.
   - Use `--platform macos` or `--platform linux` for non-Windows onedir output; Windows remains the default.
   - Assemble install and update directories through UOT or the project's thin wrapper around UOT.
   - Stable user entry should be the install root executable, not a versioned release executable.

5. Publish and verify:
   - Compress the update directory contents, not the whole install directory.
   - Publish the package to NAS with `uot publish`; include `--platform <platform>` for multi-platform projects and `--notes-file <path>` for long release notes.
   - Verify NAS metadata and package integrity with `uot verify`; include the same platform when used.
   - Use `uot keygen --public-output <public_key>` once per release trust domain, `uot publish --sign-key <private_key>`, and `uot verify --signature-key <public_key>` when manifest tamper detection is required.
   - Check that NAS contains channel `latest.json`, channel `versions.json`, and channel-scoped `v<version>/package.zip`, under the platform subdirectory when platform is set.
   - Treat `notes` as the per-version release summary and `versions.json` as the historical source for GUI/SDK version pickers; use `list-remote` or `show-version` to display it.
   - Use `uot list-remote`, `uot show-version`, and `uot prepare-version` when the GUI needs a historical version picker.
   - Use publish policy flags when needed: `--allow-downgrade`, `--hidden`, `--requires-confirmation`, `--rollout-percent`, and `--data-schema-version`.
   - Use `uot list-remote --include-hidden` for operator-only version pickers; hidden versions are filtered from normal lists and normal update checks.
   - Expect `uot publish` to maintain channel `versions.json`; `list-remote` reads the index, supplements channel-scoped `v<version>` directories, and remains compatible with legacy global `<app-id>/v<version>` releases.
   - Treat `prepare-version` as copy-and-verify only; use `uot install-prepared` or `uot apply-update` when the project wants UOT's standard runtime to install the selected package and change `current.json`.
   - Treat same-version cross-channel packages as remote storage isolation only. Local installs still use `releases/<version>` and version comparison still keys on the version number; use increasing versions for a client moving from test to stable, or explicitly use `install-prepared --force` to replace a same-version local release.
   - Prefer packaging `uot-updater` as the final application updater executable; use `uot write-updater-spec` to generate its PyInstaller spec, then pass the built artifact to `uot assemble-pyinstaller --updater-bundle <path>`. Keep full `uot` for developer, CI, and release-operator workflows.
   - Use `uot install-prepared --signature-key <key> --dry-run` before applying a package in automation; runtime uses `update.lock` to reject concurrent updates, writes `update-result.json` for successful and failed installs, and refreshes `update-status.json` with phase-level UI status.
   - Use `--wait-pid <old_gui_pid> --wait-timeout <seconds> --restart` when the standard runtime should wait for the old GUI, install the selected release, and restart the current entry.
   - Keep `latest.json` and `versions.json` on NAS only. Bundle `update-endpoint.json` into the app; keep `config/settings*.json` out of final user packages unless it is a deliberately generated runtime-safe settings file. Treat `update-status.json` as runtime state under the install root, not as a packaged file.

6. Test update behavior:
   - Start from an older install root.
   - For legacy flat installs, generate and verify a migration package with `uot write-migration-package` and `uot verify-migration-package`, then run `uot migrate-install-root --dry-run` before enabling the new runtime flow.
   - Use `uot list-installed` to inspect local releases when validating rollback or local version switching.
   - Use `uot switch-installed` only when the target release already exists under the install root.
   - Use `uot rollback` after a switch or runtime install when `current.json.previous_version` should become active again.
   - Check that update discovery sees the target version.
   - Trigger update from GUI or CLI.
   - Confirm `current.json` points to the new version.
   - Confirm `update-result.json.success` is true.
   - Confirm `update-status.json.phase` is `success`; on failure, the GUI should read `phase`, `message`, `version`, and `previous_version` on next launch. Live progress after the old GUI exits requires an updater-owned window or polling process.
   - Confirm no stale `update.lock` remains after a successful or failed runtime update.
   - Use `uot doctor --install-root <install_root> --archive <doctor.zip>` to collect a support bundle when an update fails.
   - Inspect updater and launcher logs if the GUI does not restart or the version does not switch.

## References

Read `references/release-workflow.md` when executing a full setup, package, publish, or local upgrade validation.

Read `references/troubleshooting.md` when an update fails, the wrong executable starts, NAS cannot be accessed, or `current.json` does not switch.

## Optional Script

Use `scripts/check_pyqt_uot_artifacts.py` after assembly to verify the expected install root, stable executable, `current.json`, release directory, GUI executable, updater executable, and embedded settings file shape.

Windows example:

```powershell
python C:\Users\Administrator\.codex\skills\pyqt-nas-online-update\scripts\check_pyqt_uot_artifacts.py `
  --install-dir dist\MyTool_install_v1.0.5 `
  --version 1.0.5 `
  --platform windows `
  --app-exe MyTool.exe `
  --updater-exe MyToolUpdater.exe
```

macOS onedir example:

```bash
python ~/.codex/skills/pyqt-nas-online-update/scripts/check_pyqt_uot_artifacts.py \
  --install-dir dist/MyTool_install_v1.0.5 \
  --version 1.0.5 \
  --platform macos \
  --app-exe MyTool
```
