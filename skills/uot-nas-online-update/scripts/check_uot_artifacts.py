"""Check an assembled UOT install directory for either supported runtime mode."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path


def _add_result(results: list[dict[str, object]], ok: bool, message: str) -> None:
    """Append one structured check result."""
    results.append({"ok": ok, "message": message})


def _path_exists(base: Path, relative_path: str) -> bool:
    """Return whether a slash-separated relative path exists below base."""
    resolved = _resolve_below(base, relative_path)
    return resolved is not None and resolved.exists()


def _resolve_below(base: Path, relative_path: str) -> Path | None:
    """Resolve a relative artifact path without allowing root escape."""
    resolved_base = base.resolve()
    candidate = (resolved_base / Path(relative_path)).resolve()
    try:
        candidate.relative_to(resolved_base)
    except ValueError:
        return None
    return candidate


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


def _host_platform() -> str:
    """Return the UOT platform name for the current host."""
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("win"):
        return "windows"
    return "linux"


def _resolve_updater_entry(install_dir: Path, updater_relative: str) -> Path:
    """Resolve either a flat onefile updater or a nested onedir entry."""
    resolved_install_dir = install_dir.resolve()
    configured = _resolve_below(resolved_install_dir, updater_relative)
    if configured is None:
        raise ValueError("legacy updater path escapes the install directory")
    if configured.is_file():
        return configured
    nested = (configured / configured.name).resolve()
    try:
        nested.relative_to(resolved_install_dir)
    except ValueError as exc:
        raise ValueError("legacy updater entry escapes the install directory") from exc
    if nested.is_file():
        return nested
    return configured


def _smoke_updater(updater: Path, *, timeout: float) -> tuple[bool, str]:
    """Run the updater help command and return a structured verdict."""
    if not updater.is_file():
        return False, f"legacy updater smoke entry missing: {updater}"
    try:
        completed = subprocess.run(
            [str(updater), "--help"],
            cwd=str(updater.parent),
            check=False,
            timeout=float(timeout),
            capture_output=True,
            text=True,
        )
    except subprocess.TimeoutExpired:
        return False, f"legacy updater smoke timed out after {timeout}s: {updater}"
    except OSError as exc:
        return False, f"legacy updater smoke could not start: {updater}: {exc}"
    if completed.returncode != 0:
        detail = str(completed.stderr or completed.stdout or "").strip()
        suffix = f": {detail}" if detail else ""
        return (
            False,
            f"legacy updater smoke exited with {completed.returncode}: {updater}{suffix}",
        )
    return True, f"legacy updater smoke passed: {updater}"


def main() -> int:
    """Run artifact checks and print JSON results."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--install-dir", required=True, help="Assembled UOT install root"
    )
    parser.add_argument("--version", required=True, help="Expected release version")
    parser.add_argument(
        "--platform",
        default="windows",
        choices=["windows", "macos", "linux"],
        help="Target platform",
    )
    parser.add_argument(
        "--entry-path",
        required=True,
        help="Versioned release entry, for example Product.exe or Product.app",
    )
    parser.add_argument(
        "--mode",
        choices=["legacy", "bootstrap-agent"],
        default="bootstrap-agent",
        help="Installed UOT runtime mode",
    )
    parser.add_argument(
        "--bootstrap-relative",
        default="uot-bootstrap",
        help="Stable Bootstrap path relative to install root",
    )
    parser.add_argument(
        "--agent-relative",
        default="",
        help="Optional Update Agent path relative to install root",
    )
    parser.add_argument(
        "--settings-relative",
        default="",
        help="Optional settings path relative to the versioned release",
    )
    parser.add_argument(
        "--legacy-root-entry",
        default="",
        help="Legacy stable GUI entry path relative to install root",
    )
    parser.add_argument(
        "--updater-relative",
        default="",
        help="Optional legacy updater path relative to install root",
    )
    parser.add_argument(
        "--smoke-updater",
        action="store_true",
        help="Execute the legacy updater with --help; requires a same-platform build host",
    )
    parser.add_argument(
        "--updater-smoke-timeout",
        type=float,
        default=30.0,
        help="Legacy updater smoke timeout",
    )
    args = parser.parse_args()

    install_dir = Path(args.install_dir)
    release_dir = install_dir / "releases" / args.version
    current_json = install_dir / "current.json"
    results: list[dict[str, object]] = []

    _add_result(
        results, install_dir.is_dir(), f"install directory exists: {install_dir}"
    )
    _add_result(results, current_json.is_file(), "current.json exists")
    _add_result(
        results,
        release_dir.is_dir(),
        f"release directory exists: releases/{args.version}",
    )
    _add_result(
        results,
        _path_exists(release_dir, args.entry_path),
        f"release entry exists: {args.entry_path}",
    )
    if args.settings_relative:
        _add_result(
            results,
            _path_exists(release_dir, args.settings_relative),
            f"release settings exist: {args.settings_relative}",
        )

    if args.mode == "bootstrap-agent":
        _add_result(
            results,
            _path_exists(install_dir, args.bootstrap_relative),
            f"stable Bootstrap exists: {args.bootstrap_relative}",
        )
        if args.agent_relative:
            _add_result(
                results,
                _path_exists(install_dir, args.agent_relative),
                f"Update Agent exists: {args.agent_relative}",
            )
    else:
        if args.legacy_root_entry:
            _add_result(
                results,
                _path_exists(install_dir, args.legacy_root_entry),
                f"legacy stable entry exists: {args.legacy_root_entry}",
            )
        if args.updater_relative:
            _add_result(
                results,
                _path_exists(install_dir, args.updater_relative),
                f"legacy updater exists: {args.updater_relative}",
            )
        if args.smoke_updater:
            if not args.updater_relative:
                _add_result(
                    results, False, "--smoke-updater requires --updater-relative"
                )
            elif _host_platform() != args.platform:
                _add_result(
                    results,
                    False,
                    f"legacy updater smoke requires {args.platform} host; current host is {_host_platform()}",
                )
            else:
                try:
                    updater_entry = _resolve_updater_entry(
                        install_dir, args.updater_relative
                    )
                except ValueError as exc:
                    _add_result(results, False, str(exc))
                else:
                    smoke_ok, smoke_message = _smoke_updater(
                        updater_entry, timeout=args.updater_smoke_timeout
                    )
                    _add_result(results, smoke_ok, smoke_message)

    if current_json.is_file():
        try:
            current_data = json.loads(current_json.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _add_result(results, False, f"current.json cannot be read: {exc}")
        else:
            if not isinstance(current_data, dict):
                _add_result(
                    results,
                    False,
                    "current.json root must be an object",
                )
            else:
                _add_result(
                    results,
                    current_data.get("version") == args.version,
                    "current.json version matches",
                )
                _add_result(
                    results,
                    _current_entry(current_data) == args.entry_path,
                    "current.json entry matches",
                )
                entry = current_data.get("entry")
                if isinstance(entry, dict):
                    _add_result(
                        results,
                        entry.get("platform") == args.platform,
                        "current.json entry.platform matches",
                    )

    ok = all(bool(item["ok"]) for item in results)
    print(json.dumps({"ok": ok, "checks": results}, ensure_ascii=False, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
