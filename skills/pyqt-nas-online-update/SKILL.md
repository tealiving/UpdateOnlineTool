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

4. Build and assemble:
   - Build with PyInstaller from the project root.
   - Keep GUI executable naming separate from launcher internal naming when the project uses a launcher.
   - Assemble install and update directories through UOT or the project's thin wrapper around UOT.
   - Stable user entry should be the install root executable, not a versioned release executable.

5. Publish and verify:
   - Compress the update directory contents, not the whole install directory.
   - Publish the package to NAS with `uot publish`.
   - Verify NAS metadata and package integrity with `uot verify`.
   - Check that NAS contains stable `latest.json` and versioned `package.zip`.

6. Test update behavior:
   - Start from an older install root.
   - Check that update discovery sees the target version.
   - Trigger update from GUI or CLI.
   - Confirm `current.json` points to the new version.
   - Confirm `update-result.json.success` is true.
   - Inspect updater and launcher logs if the GUI does not restart or the version does not switch.

## References

Read `references/release-workflow.md` when executing a full setup, package, publish, or local upgrade validation.

Read `references/troubleshooting.md` when an update fails, the wrong executable starts, NAS cannot be accessed, or `current.json` does not switch.

## Optional Script

Use `scripts/check_pyqt_uot_artifacts.py` after assembly to verify the expected install root, stable executable, `current.json`, release directory, GUI executable, updater executable, and embedded settings file shape.

Example:

```powershell
python C:\Users\Administrator\.codex\skills\pyqt-nas-online-update\scripts\check_pyqt_uot_artifacts.py `
  --install-dir dist\MyTool_install_v1.0.5 `
  --version 1.0.5 `
  --app-exe MyTool.exe `
  --updater-exe MyToolUpdater.exe
```
