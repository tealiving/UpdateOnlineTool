# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.11 `src`-layout package for a NAS-based desktop update SDK and CLI. Core code lives in `src/update_online_tool/`: `cli.py` exposes `uot`; `service.py`, `manifest.py`, `nas.py`, and `downloader.py` own NAS release discovery; `runtime.py`, `agent.py`, `bridge_cli.py`, and `bootstrap_cli.py` own durable update handoff; `release_contract.py` validates release artifacts. Tests mirror the package in `tests/test_*.py`. Configuration examples live in `config/`, docs in `docs/`, and the packaged multi-runtime skill in `skills/uot-nas-online-update/`.

## Build, Test, and Development Commands

Use `rtk` when running shell commands from Codex.

- `rtk python3 -m pip install -e .` installs the package and `uot` console script in editable mode.
- `rtk python3 -m pytest -q` runs the full test suite configured by `pyproject.toml`.
- `rtk python3 -m pytest tests/test_cli.py -q` runs focused CLI tests.
- `rtk uot init --nas-root /path/to/nas --skip-nas-check` generates local endpoint/settings files without requiring a live NAS.
- `rtk uot publish --settings config/settings.json --app demo --version 1.0.0 --package dist/app.zip` publishes a package into the configured NAS layout.
- `rtk uot verify --settings config/settings.json --app demo` validates manifest package size and SHA-256.
- `rtk uot validate-release --release-dir dist/release --app demo --version 1.0.0 --platform macos --entry-path Demo.app` validates a release before packaging or switching.

## Coding Style & Naming Conventions

Use standard-library Python unless a dependency is explicitly justified in `pyproject.toml`. Keep the runtime GUI-agnostic: PyQt, Electron, and Tauri call the facade or JSON bridge; they never parse NAS state or mutate `current.json`. Follow the current style: 4-space indentation, type hints, `Path` for filesystem paths, argparse for CLI parsing, and domain errors via `UpdateErrorCode`. Name modules and functions with `snake_case`, classes with `PascalCase`, and tests as `test_<behavior>`.

## Testing Guidelines

Pytest is the test framework. Add focused tests beside related behavior in `tests/`; prefer temporary directories and local files over real NAS dependencies. Cover manifest validation, version comparisons, settings resolution, Agent ready/handoff, CLI return codes, and release contracts. Changes to installation or rollback must test missing settings/bridge, invalid contracts, and safe rejection before `current.json` changes.

Release ZIP changes must use the shared `ReleasePackagePlan`; do not add archive-path rules in CLI, GUI, Bridge, Agent, or host adapters. Cover the strict `windows|macos|linux` target contract, Windows reserved names, case-insensitive and NFC/NFD collisions, component/full-path and decompression-resource budgets, legal Chinese names, symlink preservation, and identical dry-run/install rejection. Manifest hash/size, plan, and extraction must bind the same opened package. Legacy releases may omit `uot-release.json`, but switching and rollback must still validate version, entry type, required paths, and root containment.

## Commit & Pull Request Guidelines

History uses concise conventional-style commits such as `feat(配置): ...`, `fix(readme): ...`, and `docs(readme): ...`. Keep the type lowercase, add a scope when useful, and write the subject in English or Chinese consistently with the touched area. PRs should include a behavior summary, test commands run, linked issues or docs, and screenshots/video only for README or GUI integration changes.

## Security & Configuration Tips

Never store NAS credentials, API tokens, deploy keys, or passwords in settings. The tool relies on OS-managed SMB credentials and mounted shares. Treat `latest.json`, package hashes, `config/settings.json`, and `uot-release.json` as release-critical artifacts; validate them before publishing. Native installers, when introduced, must use a typed delivery contract—never a GUI-supplied command string.
