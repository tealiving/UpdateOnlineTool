# Repository Guidelines

## Project Structure & Module Organization

This is a Python 3.11 `src`-layout package for a NAS-based online update SDK and CLI. Core code lives in `src/update_online_tool/`: `cli.py` exposes `uot`, `service.py` coordinates update checks, `manifest.py` and `versioning.py` model release data, and `nas.py`, `downloader.py`, `launcher.py`, and `pyinstaller_assembly.py` handle infrastructure. Tests mirror the package in `tests/test_*.py`. Configuration examples live in `config/`, docs in `docs/`, compatibility scripts in `_scripts/`, and the packaged skill in `skills/pyqt-nas-online-update/`.

## Build, Test, and Development Commands

Use `rtk` when running shell commands from Codex.

- `rtk python -m pip install -e .` installs the package and `uot` console script in editable mode.
- `rtk python -m pytest -q` runs the full test suite configured by `pyproject.toml`.
- `rtk python -m pytest tests/test_cli.py -q` runs focused CLI tests.
- `rtk uot init --nas-root /path/to/nas --skip-nas-check` generates local endpoint/settings files without requiring a live NAS.
- `rtk uot publish --settings config/settings.json --app demo --version 1.0.0 --package dist/app.zip` publishes a package into the configured NAS layout.
- `rtk uot verify --settings config/settings.json --app demo` validates manifest package size and SHA-256.

## Coding Style & Naming Conventions

Use standard-library Python unless a dependency is explicitly justified in `pyproject.toml`. Keep runtime code GUI-agnostic; `pyqt_runtime.py` is an integration helper, not a reason to import PyQt in core modules. Follow the current style: 4-space indentation, type hints, `Path` for filesystem paths, argparse for CLI parsing, and domain errors via `UpdateErrorCode`. Name modules and functions with `snake_case`, classes with `PascalCase`, and tests as `test_<behavior>`.

## Testing Guidelines

Pytest is the test framework. Add focused tests beside related behavior in `tests/`; prefer temporary directories and local files over real NAS dependencies. Cover manifest validation, version comparisons, settings resolution, CLI return codes, and PyInstaller assembly edge cases when those paths change.

## Commit & Pull Request Guidelines

History uses concise conventional-style commits such as `feat(配置): ...`, `fix(readme): ...`, and `docs(readme): ...`. Keep the type lowercase, add a scope when useful, and write the subject in English or Chinese consistently with the touched area. PRs should include a behavior summary, test commands run, linked issues or docs, and screenshots/video only for README or GUI integration changes.

## Security & Configuration Tips

Never store NAS credentials, API tokens, deploy keys, or passwords in settings. The tool relies on OS-managed SMB credentials and mounted shares. Treat `latest.json`, package hashes, and `config/settings.json` examples as release-critical artifacts; verify them before publishing.
