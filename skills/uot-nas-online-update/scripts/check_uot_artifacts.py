"""Check an assembled UOT install directory for either supported runtime mode."""

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


def _current_entry(current_data: dict[str, object]) -> str:
    """Read the current release entry from either supported current.json shape."""
    executable = current_data.get("executable")
    if isinstance(executable, str) and executable:
        return executable
    entry = current_data.get("entry")
    if isinstance(entry, dict):
        path = entry.get("path")
        if isinstance(path, str):
            return path
    return ""


def main() -> int:
    """Run artifact checks and print JSON results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", required=True, help="Assembled UOT install root")
    parser.add_argument("--version", required=True, help="Expected release version")
    parser.add_argument("--platform", default="windows", choices=["windows", "macos", "linux"], help="Target platform")
    parser.add_argument("--entry-path", required=True, help="Versioned release entry, for example Product.exe or Product.app")
    parser.add_argument("--mode", choices=["legacy", "bootstrap-agent"], default="bootstrap-agent", help="Installed UOT runtime mode")
    parser.add_argument("--bootstrap-relative", default="uot-bootstrap", help="Stable Bootstrap path relative to install root")
    parser.add_argument("--agent-relative", default="", help="Optional Update Agent path relative to install root")
    parser.add_argument("--settings-relative", default="", help="Optional settings path relative to the versioned release")
    parser.add_argument("--legacy-root-entry", default="", help="Legacy stable GUI entry path relative to install root")
    parser.add_argument("--updater-relative", default="", help="Optional legacy updater path relative to install root")
    args = parser.parse_args()

    install_dir = Path(args.install_dir)
    release_dir = install_dir / "releases" / args.version
    current_json = install_dir / "current.json"
    results: list[dict[str, object]] = []

    _add_result(results, install_dir.is_dir(), f"install directory exists: {install_dir}")
    _add_result(results, current_json.is_file(), "current.json exists")
    _add_result(results, release_dir.is_dir(), f"release directory exists: releases/{args.version}")
    _add_result(results, _path_exists(release_dir, args.entry_path), f"release entry exists: {args.entry_path}")
    if args.settings_relative:
        _add_result(results, _path_exists(release_dir, args.settings_relative), f"release settings exist: {args.settings_relative}")

    if args.mode == "bootstrap-agent":
        _add_result(results, _path_exists(install_dir, args.bootstrap_relative), f"stable Bootstrap exists: {args.bootstrap_relative}")
        if args.agent_relative:
            _add_result(results, _path_exists(install_dir, args.agent_relative), f"Update Agent exists: {args.agent_relative}")
    else:
        if args.legacy_root_entry:
            _add_result(results, _path_exists(install_dir, args.legacy_root_entry), f"legacy stable entry exists: {args.legacy_root_entry}")
        if args.updater_relative:
            _add_result(results, _path_exists(install_dir, args.updater_relative), f"legacy updater exists: {args.updater_relative}")

    if current_json.is_file():
        try:
            current_data = json.loads(current_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _add_result(results, False, f"current.json cannot be read: {exc}")
        else:
            _add_result(results, current_data.get("version") == args.version, "current.json version matches")
            _add_result(results, _current_entry(current_data) == args.entry_path, "current.json entry matches")
            entry = current_data.get("entry")
            if isinstance(entry, dict):
                _add_result(results, entry.get("platform") == args.platform, "current.json entry.platform matches")

    ok = all(bool(item["ok"]) for item in results)
    print(json.dumps({"ok": ok, "checks": results}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
