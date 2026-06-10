"""Check a PyQt + UOT assembled install directory."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _add_result(results: list[dict[str, object]], ok: bool, message: str) -> None:
    """Append one structured check result."""
    results.append({"ok": ok, "message": message})


def main() -> int:
    """Run artifact checks and print JSON results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", required=True, help="Assembled install root")
    parser.add_argument("--version", required=True, help="Expected release version")
    parser.add_argument("--app-exe", required=True, help="Stable GUI executable name")
    parser.add_argument("--updater-exe", required=True, help="Updater executable name")
    parser.add_argument(
        "--settings-relative",
        default="_internal/config/settings.json",
        help="Settings path relative to the versioned release directory",
    )
    args = parser.parse_args()

    install_dir = Path(args.install_dir)
    release_dir = install_dir / "releases" / args.version
    current_json = install_dir / "current.json"
    results: list[dict[str, object]] = []

    _add_result(results, install_dir.exists(), f"install dir exists: {install_dir}")
    _add_result(results, (install_dir / args.app_exe).exists(), f"root stable exe exists: {args.app_exe}")
    _add_result(results, (install_dir / args.updater_exe).exists(), f"root updater exists: {args.updater_exe}")
    _add_result(results, current_json.exists(), "current.json exists")
    _add_result(results, release_dir.exists(), f"release dir exists: releases/{args.version}")
    _add_result(results, (release_dir / args.app_exe).exists(), f"release GUI exe exists: {args.app_exe}")
    _add_result(results, (release_dir / args.updater_exe).exists(), f"release updater exists: {args.updater_exe}")
    _add_result(
        results,
        (release_dir / args.settings_relative).exists(),
        f"release settings exists: {args.settings_relative}",
    )

    if current_json.exists():
        try:
            current_data = json.loads(current_json.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            _add_result(results, False, f"current.json is invalid JSON: {exc}")
        else:
            _add_result(results, current_data.get("version") == args.version, "current.json version matches")
            _add_result(results, current_data.get("executable") == args.app_exe, "current.json executable matches")

    ok = all(bool(item["ok"]) for item in results)
    print(json.dumps({"ok": ok, "checks": results}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
