"""标准 updater 可执行体入口。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.manifest import UpdateManifest
from update_online_tool.runtime import (
    apply_pending_update,
    install_prepared_package,
    launch_current,
    rollback_installation,
)
from update_online_tool.signature import verify_manifest_signature_with_key_file


def main(argv: list[str] | None = None) -> int:
    """运行标准 updater CLI。"""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "install":
            return _install(args)
        if args.command == "apply":
            return _apply(args)
        if args.command == "rollback":
            return _rollback(args)
        if args.command == "launch-current":
            return _launch_current(args)
    except UpdateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    parser.print_help()
    return 2


def _build_parser() -> argparse.ArgumentParser:
    """构建 updater 参数解析器。"""
    parser = argparse.ArgumentParser(prog="uot-updater", description="Standard UpdateOnlineTool updater runtime.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    install = subparsers.add_parser("install", help="Install a prepared package into an install root.")
    install.add_argument("--install-root", required=True, type=Path, help="Install root.")
    install.add_argument("--package", required=True, type=Path, help="Prepared package zip.")
    install.add_argument("--manifest", required=True, type=Path, help="Manifest JSON for the package.")
    install.add_argument("--entry-name", default="", help="Release entry name. Defaults to current.json entry.")
    install.add_argument("--force", action="store_true", help="Replace an existing release directory.")
    install.add_argument("--dry-run", action="store_true", help="Validate the install plan without writing files.")
    install.add_argument("--signature-key", default=None, type=Path, help="Key file used to verify manifest.")
    install.add_argument("--wait-pid", default=None, type=int, help="Old application PID to wait for before install.")
    install.add_argument("--wait-timeout", default=60.0, type=float, help="Seconds to wait for --wait-pid.")
    install.add_argument("--restart", action="store_true", help="Restart the current release after install.")

    apply = subparsers.add_parser("apply", help="Apply pending-update.json.")
    apply.add_argument("--pending", required=True, type=Path, help="pending-update.json path.")
    apply.add_argument("--entry-name", default="", help="Release entry name. Defaults to current.json entry.")
    apply.add_argument("--force", action="store_true", help="Replace an existing release directory.")
    apply.add_argument("--dry-run", action="store_true", help="Validate the pending update without writing files.")
    apply.add_argument("--signature-key", default=None, type=Path, help="Key file used to verify manifest.")
    apply.add_argument("--wait-pid", default=None, type=int, help="Old application PID to wait for before install.")
    apply.add_argument("--wait-timeout", default=60.0, type=float, help="Seconds to wait for --wait-pid.")
    apply.add_argument("--restart", action="store_true", help="Restart the current release after install.")

    rollback = subparsers.add_parser("rollback", help="Rollback current.json to previous_version.")
    rollback.add_argument("--install-root", required=True, type=Path, help="Install root.")
    rollback.add_argument("--entry-name", default="", help="Release entry name. Defaults to current.json entry.")

    launch = subparsers.add_parser("launch-current", help="Launch the current release entry.")
    launch.add_argument("--install-root", required=True, type=Path, help="Install root.")
    return parser


def _install(args: argparse.Namespace) -> int:
    """安装已准备包。"""
    manifest_payload = _read_json_object(Path(args.manifest), "manifest")
    if args.signature_key is not None:
        verify_manifest_signature_with_key_file(manifest_payload, key_path=Path(args.signature_key))
    result = install_prepared_package(
        install_root=Path(args.install_root),
        package_path=Path(args.package),
        manifest=UpdateManifest.from_payload(manifest_payload),
        entry_name=args.entry_name,
        force=bool(args.force),
        dry_run=bool(args.dry_run),
        wait_pid=args.wait_pid,
        wait_timeout=float(args.wait_timeout),
        restart=bool(args.restart),
    )
    print(json.dumps(result.to_payload(), ensure_ascii=False, indent=2))
    return 0


def _apply(args: argparse.Namespace) -> int:
    """应用 pending-update.json。"""
    if args.signature_key is not None:
        pending_payload = _read_json_object(Path(args.pending), "pending update")
        manifest_payload = pending_payload.get("manifest")
        if not isinstance(manifest_payload, dict):
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "pending manifest must be an object")
        verify_manifest_signature_with_key_file(manifest_payload, key_path=Path(args.signature_key))
    result = apply_pending_update(
        pending_path=Path(args.pending),
        entry_name=args.entry_name,
        force=bool(args.force),
        dry_run=bool(args.dry_run),
        wait_pid=args.wait_pid,
        wait_timeout=float(args.wait_timeout),
        restart=bool(args.restart),
    )
    print(json.dumps(result.to_payload(), ensure_ascii=False, indent=2))
    return 0


def _rollback(args: argparse.Namespace) -> int:
    """回滚安装根。"""
    result = rollback_installation(install_root=Path(args.install_root), entry_name=args.entry_name)
    print(json.dumps(result.to_payload(), ensure_ascii=False, indent=2))
    return 0


def _launch_current(args: argparse.Namespace) -> int:
    """启动当前版本。"""
    process = launch_current(install_root=Path(args.install_root))
    print(json.dumps({"pid": process.pid}, ensure_ascii=False, indent=2))
    return 0


def _read_json_object(path: Path, context: str) -> dict[str, object]:
    """读取 JSON 对象。"""
    if not path.is_file():
        raise UpdateError(UpdateErrorCode.MANIFEST_NOT_FOUND, f"{context} not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"{context} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"{context} must be a JSON object")
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
