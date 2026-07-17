"""供 Electron、Tauri 等宿主调用的 JSON bridge。"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from update_online_tool.agent import (
    UpdateAgentLauncher,
    create_apply_request,
    create_rollback_request,
    create_switch_request,
    write_agent_handoff,
)
from update_online_tool.desktop import DesktopUpdateClient, DesktopUpdateConfig
from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.release_contract import normalize_release_required_paths


@dataclass(frozen=True)
class BridgeConfig:
    """JSON bridge 的桌面客户端配置。"""

    app_id: str
    install_root: Path
    settings_path: Path | None = None
    platform: str = ""
    channel: str = ""
    download_dir: Path | None = None
    signature_key: Path | None = None
    wait_timeout: float = 60.0
    agent_executable: Path | None = None
    bootstrap_command: tuple[str, ...] = ()
    # PyInstaller runtimes on removable or newly mounted volumes can require a
    # first-launch security scan.  Five seconds is not a reliable readiness
    # contract for a background updater; hosts may still override this value.
    agent_ready_timeout: float = 30.0
    handoff_timeout: float = 60.0
    release_required_paths: tuple[str, ...] = ()

    def desktop_config(self) -> DesktopUpdateConfig:
        """转换为 UOT 桌面客户端配置。"""
        return DesktopUpdateConfig(
            app_id=self.app_id,
            install_root=self.install_root,
            settings_path=self.settings_path,
            platform=self.platform,
            channel=self.channel,
            download_dir=self.download_dir,
            signature_key=self.signature_key,
            wait_timeout=self.wait_timeout,
            release_required_paths=self.release_required_paths,
        )


def main(argv: list[str] | None = None) -> int:
    """执行 bridge 命令并始终输出 JSON。"""
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        bridge_config = _load_bridge_config(Path(args.config))
        client = DesktopUpdateClient.from_config(bridge_config.desktop_config())
        payload = _execute_command(client, args, bridge_config)
    except UpdateError as exc:
        _write_json({"ok": False, "error": {"code": exc.code.value, "message": exc.message}}, stream=sys.stderr)
        return 1
    _write_json({"ok": True, **payload})
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """构建 JSON bridge 参数解析器。"""
    parser = argparse.ArgumentParser(prog="uot-bridge", description="JSON bridge for desktop application hosts.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("check", "list-remote", "list-installed", "launch-pending", "status", "result"):
        item = subparsers.add_parser(command)
        item.add_argument("--config", required=True, type=Path, help="Bridge JSON configuration.")
    check = subparsers.choices["check"]
    check.add_argument("--skipped-version", default="", help="Version skipped by the user.")
    remote = subparsers.choices["list-remote"]
    remote.add_argument("--include-hidden", action="store_true", help="Include hidden releases.")
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--config", required=True, type=Path, help="Bridge JSON configuration.")
    prepare.add_argument("--version", required=True, help="Remote version to download and verify.")
    prepare.add_argument("--old-pid", default=None, type=int, help="Application PID to wait for after exit.")
    prepare.add_argument("--no-restart", action="store_true", help="Do not restart after updater installation.")
    prepare.add_argument("--force", action="store_true", help="Replace an existing release directory.")
    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--config", required=True, type=Path, help="Bridge JSON configuration.")
    rollback.add_argument("--old-pid", default=None, type=int, help="Application PID to wait for after exit.")
    rollback.add_argument("--no-restart", action="store_true", help="Do not restart after rollback.")
    agent_start = subparsers.add_parser("agent-start")
    agent_start.add_argument("--config", required=True, type=Path, help="Bridge JSON configuration.")
    agent_start.add_argument("--old-pid", default=None, type=int, help="Application PID to wait for after host exit.")
    agent_start.add_argument("--operation-id", default="", help="Optional durable update operation id.")
    agent_switch = subparsers.add_parser("agent-switch")
    agent_switch.add_argument("--config", required=True, type=Path, help="Bridge JSON configuration.")
    agent_switch.add_argument("--version", required=True, help="Installed version to activate through Agent.")
    agent_switch.add_argument("--old-pid", default=None, type=int, help="Application PID to wait for after host exit.")
    agent_switch.add_argument("--operation-id", default="", help="Optional durable update operation id.")
    agent_rollback = subparsers.add_parser("agent-rollback")
    agent_rollback.add_argument("--config", required=True, type=Path, help="Bridge JSON configuration.")
    agent_rollback.add_argument("--old-pid", default=None, type=int, help="Application PID to wait for after host exit.")
    agent_rollback.add_argument("--operation-id", default="", help="Optional durable update operation id.")
    agent_handoff = subparsers.add_parser("agent-handoff")
    agent_handoff.add_argument("--config", required=True, type=Path, help="Bridge JSON configuration.")
    agent_handoff.add_argument("--request", required=True, type=Path, help="Ready agent request returned by agent-start.")
    return parser


def _execute_command(
    client: DesktopUpdateClient,
    args: argparse.Namespace,
    bridge_config: BridgeConfig,
) -> dict[str, object]:
    """调用桌面 facade 并转换为稳定 JSON 负载。"""
    if args.command == "check":
        result = client.check(skipped_version=args.skipped_version or None)
        return {"decision": result.decision.value, "manifest": result.manifest.to_payload(), "notes": result.notes}
    if args.command == "list-remote":
        versions = client.list_remote_versions(include_hidden=bool(args.include_hidden))
        return {
            "versions": [
                {
                    "version": item.version,
                    "channel": item.channel,
                    "platform": item.platform,
                    "notes": item.notes,
                    "package_exists": item.package_exists,
                    "manifest": item.manifest.to_payload(),
                }
                for item in versions
            ]
        }
    if args.command == "list-installed":
        return {
            "versions": [
                {"version": item.version, "path": str(item.release_dir), "current": item.version == client.current_version()}
                for item in client.list_installed_versions()
            ]
        }
    if args.command == "prepare":
        _validate_pid(args.old_pid)
        prepared = client.prepare_remote_version(
            args.version,
            old_pid=args.old_pid,
            restart=not bool(args.no_restart),
            force=bool(args.force),
        )
        return {
            "version": prepared.version,
            "package_path": str(prepared.package_path),
            "pending_path": str(prepared.pending_manifest_path),
            "manifest": prepared.manifest.to_payload(),
        }
    if args.command == "launch-pending":
        result = client.launch_pending_update()
        return {"started": result.started, "updater_pid": result.updater_pid, "pending_path": str(result.pending_manifest_path)}
    if args.command == "status":
        return {"status": client.read_status()}
    if args.command == "result":
        return {"result": client.read_result()}
    if args.command == "rollback":
        _validate_pid(args.old_pid)
        result = client.rollback(old_pid=args.old_pid, restart=not bool(args.no_restart))
        return {"started": result.started, "updater_pid": result.updater_pid}
    if args.command in {"agent-start", "agent-switch", "agent-rollback"}:
        _validate_pid(args.old_pid)
        if bridge_config.agent_executable is None:
            raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "bridge config agent_executable is required for Agent commands")
        if not bridge_config.bootstrap_command:
            raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "bridge config bootstrap_command is required for Agent commands")
        if args.command == "agent-start":
            pending_path = client.pending_path()
            _validate_prepared_pending(pending_path)
            request = create_apply_request(
                install_root=bridge_config.install_root,
                pending_path=pending_path,
                old_pid=args.old_pid,
                wait_timeout=bridge_config.wait_timeout,
                handoff_timeout=bridge_config.handoff_timeout,
                bootstrap_command=bridge_config.bootstrap_command,
                release_required_paths=bridge_config.release_required_paths,
                operation_id=args.operation_id,
            )
        elif args.command == "agent-switch":
            request = create_switch_request(
                install_root=bridge_config.install_root,
                version=args.version,
                old_pid=args.old_pid,
                wait_timeout=bridge_config.wait_timeout,
                handoff_timeout=bridge_config.handoff_timeout,
                bootstrap_command=bridge_config.bootstrap_command,
                release_required_paths=bridge_config.release_required_paths,
                operation_id=args.operation_id,
            )
        else:
            request = create_rollback_request(
                install_root=bridge_config.install_root,
                old_pid=args.old_pid,
                wait_timeout=bridge_config.wait_timeout,
                handoff_timeout=bridge_config.handoff_timeout,
                bootstrap_command=bridge_config.bootstrap_command,
                release_required_paths=bridge_config.release_required_paths,
                operation_id=args.operation_id,
            )
        started = UpdateAgentLauncher(bridge_config.agent_executable).start(
            request,
            ready_timeout=bridge_config.agent_ready_timeout,
        )
        return {
            "operation_id": started.operation_id,
            "agent_pid": started.agent_pid,
            "request_path": str(started.request_path),
        }
    if args.command == "agent-handoff":
        request_path = write_agent_handoff(Path(args.request))
        return {"request_path": str(request_path), "confirmed": True}
    raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"unsupported bridge command: {args.command}")


def _load_bridge_config(path: Path) -> BridgeConfig:
    """读取并验证 bridge JSON 配置。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"bridge config cannot be read: {path}") from exc
    except json.JSONDecodeError as exc:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"bridge config is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "bridge config must be a JSON object")
    app_id = _required_text(payload, "app_id")
    install_root = Path(_required_text(payload, "install_root"))
    wait_timeout = payload.get("wait_timeout", 60.0)
    if not isinstance(wait_timeout, (int, float)) or isinstance(wait_timeout, bool) or wait_timeout < 0:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "bridge config wait_timeout must be a non-negative number")
    agent_ready_timeout = payload.get("agent_ready_timeout", 30.0)
    if not isinstance(agent_ready_timeout, (int, float)) or isinstance(agent_ready_timeout, bool) or agent_ready_timeout < 0:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "bridge config agent_ready_timeout must be a non-negative number")
    handoff_timeout = payload.get("handoff_timeout", 60.0)
    if not isinstance(handoff_timeout, (int, float)) or isinstance(handoff_timeout, bool) or handoff_timeout < 0:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "bridge config handoff_timeout must be a non-negative number")
    return BridgeConfig(
        app_id=app_id,
        install_root=install_root,
        settings_path=_optional_path(payload, "settings_path"),
        platform=_optional_text(payload, "platform"),
        channel=_optional_text(payload, "channel"),
        download_dir=_optional_path(payload, "download_dir"),
        signature_key=_optional_path(payload, "signature_key"),
        wait_timeout=float(wait_timeout),
        agent_executable=_optional_path(payload, "agent_executable"),
        bootstrap_command=_optional_command(payload, "bootstrap_command"),
        agent_ready_timeout=float(agent_ready_timeout),
        handoff_timeout=float(handoff_timeout),
        release_required_paths=_optional_release_required_paths(payload),
    )


def _required_text(payload: dict[str, Any], key: str) -> str:
    """读取 bridge 必填字符串。"""
    value = _optional_text(payload, key)
    if not value:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"bridge config {key} must be a non-empty string")
    return value


def _optional_text(payload: dict[str, Any], key: str) -> str:
    """读取 bridge 可选字符串。"""
    value = payload.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"bridge config {key} must be a string")
    return value.strip()


def _optional_path(payload: dict[str, Any], key: str) -> Path | None:
    """读取 bridge 可选路径。"""
    value = _optional_text(payload, key)
    return Path(value) if value else None


def _optional_command(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    """读取可选的稳定 Bootstrap 命令。"""
    value = payload.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not value:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"bridge config {key} must be a non-empty string array")
    command = tuple(item.strip() for item in value if isinstance(item, str))
    if len(command) != len(value) or any(not item for item in command):
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"bridge config {key} must contain non-empty strings")
    return command


def _optional_release_required_paths(payload: dict[str, Any]) -> tuple[str, ...]:
    """读取宿主声明的 release 必需资源路径。"""
    try:
        return normalize_release_required_paths(payload.get("release_required_paths"))
    except UpdateError as exc:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"bridge config {exc.message}") from exc


def _validate_prepared_pending(path: Path) -> None:
    """在宿主退出前确认 prepare 已写入可读取的 pending 文件。"""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        raise UpdateError(UpdateErrorCode.MANIFEST_NOT_FOUND, f"pending update not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"pending update is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "pending update must be a JSON object")


def _validate_pid(value: int | None) -> None:
    """验证应用进程号。"""
    if value is not None and value <= 0:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "old_pid must be positive")


def _write_json(payload: dict[str, object], *, stream: Any | None = None) -> None:
    """输出 UTF-8 JSON，供宿主进程解析。"""
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=stream or sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
