"""Check a PyQt + UOT assembled install directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _add_result(results: list[dict[str, object]], ok: bool, message: str) -> None:
    """Append one structured check result."""
    results.append({"ok": ok, "message": message})


def _path_exists(base: Path, relative_path: str) -> bool:
    """Return whether a slash-separated relative path exists below base."""
    return (base / Path(relative_path)).exists()


def _default_settings_relative(platform: str) -> str:
    """Return the default runtime settings path for the target platform."""
    if platform == "macos":
        return "Contents/Resources/config/settings.json"
    return "_internal/config/settings.json"


def _settings_base(release_dir: Path, app_exe: str, platform: str) -> Path:
    """Return the base directory used for runtime settings checks."""
    if platform == "macos":
        return release_dir / app_exe
    return release_dir


def main() -> int:
    """Run artifact checks and print JSON results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", required=True, help="Assembled install root")
    parser.add_argument("--version", required=True, help="Expected release version")
    parser.add_argument("--platform", default="windows", choices=["windows", "macos", "linux"], help="Target platform")
    parser.add_argument("--app-exe", required=True, help="Stable GUI entry name")
    parser.add_argument("--updater-exe", default="", help="Optional updater entry name")
    parser.add_argument(
        "--updater-relative",
        default="",
        help=(
            "Updater path relative to install root. Defaults to "
            "updater/<updater-exe> when --updater-exe is provided."
        ),
    )
    parser.add_argument(
        "--release-updater-relative",
        default="",
        help="Optional updater path relative to the versioned release directory",
    )
    parser.add_argument(
        "--settings-relative",
        default="",
        help=(
            "Settings path relative to the app bundle on macOS, or relative to "
            "the versioned release directory on Windows/Linux."
        ),
    )
    args = parser.parse_args()

    install_dir = Path(args.install_dir)
    release_dir = install_dir / "releases" / args.version
    current_json = install_dir / "current.json"
    updater_relative = args.updater_relative
    if args.updater_exe and not updater_relative:
        updater_relative = f"updater/{args.updater_exe}"
    settings_relative = args.settings_relative or _default_settings_relative(args.platform)
    settings_base = _settings_base(release_dir, args.app_exe, args.platform)
    results: list[dict[str, object]] = []

    _add_result(results, install_dir.exists(), f"install dir exists: {install_dir}")
    _add_result(results, (install_dir / args.app_exe).exists(), f"root stable entry exists: {args.app_exe}")
    if updater_relative:
        _add_result(results, _path_exists(install_dir, updater_relative), f"root updater exists: {updater_relative}")
    _add_result(results, current_json.exists(), "current.json exists")
    _add_result(results, release_dir.exists(), f"release dir exists: releases/{args.version}")
    _add_result(results, (release_dir / args.app_exe).exists(), f"release GUI entry exists: {args.app_exe}")
    if args.release_updater_relative:
        _add_result(
            results,
            _path_exists(release_dir, args.release_updater_relative),
            f"release updater exists: {args.release_updater_relative}",
        )
    _add_result(
        results,
        _path_exists(settings_base, settings_relative),
        f"release settings exists: {settings_base.relative_to(release_dir)}/{settings_relative}",
    )

    if current_json.exists():
        try:
            current_data = json.loads(current_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            _add_result(results, False, f"current.json is invalid JSON: {exc}")
        else:
            _add_result(results, current_data.get("version") == args.version, "current.json version matches")
            _add_result(results, current_data.get("executable") == args.app_exe, "current.json executable matches")
            entry = current_data.get("entry")
            if isinstance(entry, dict):
                _add_result(results, entry.get("path") == args.app_exe, "current.json entry.path matches")
                _add_result(results, entry.get("platform") == args.platform, "current.json entry.platform matches")

    ok = all(bool(item["ok"]) for item in results)
    print(json.dumps({"ok": ok, "checks": results}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
