"""PyQt 工具运行时升级适配。"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.launcher import LaunchResult
from update_online_tool.manifest import UpdateManifest

PopenFactory = Callable[..., Any]


@dataclass(frozen=True)
class PyQtPendingUpdateRequest:
    """PyQt updater 交接请求。

    :param package_path: 已准备好的升级包路径。
    :param manifest: 远端 manifest。
    :param install_root: 工具安装根目录。
    :param old_pid: 旧 GUI 进程号。
    :param restart_executable: 升级后重启的 GUI 可执行文件名。
    :param from_version: 当前版本。
    :return: None
    """

    package_path: Path
    manifest: UpdateManifest
    install_root: Path
    old_pid: int
    restart_executable: str
    from_version: str = "unknown"


def build_pyqt_pending_payload(request: PyQtPendingUpdateRequest) -> dict[str, object]:
    """构建标准 UOT pending payload。

    :param request: PyQt updater 交接请求。
    :return: pending JSON 字典。
    """
    return {
        "from_version": request.from_version or "unknown",
        "install_root": str(Path(request.install_root)),
        "old_pid": int(request.old_pid),
        "package_path": str(Path(request.package_path)),
        "manifest": request.manifest.to_payload(),
        "restart_executable": request.restart_executable,
    }


def write_pyqt_pending_manifest(*, pending_path: Path, request: PyQtPendingUpdateRequest) -> Path:
    """写入 PyQt updater 兼容 pending manifest。

    :param pending_path: pending-update.json 路径。
    :param request: PyQt updater 交接请求。
    :return: pending-update.json 路径。
    """
    target_path = Path(pending_path)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target_path.with_name(f"{target_path.name}.tmp")
    temp_path.write_text(
        json.dumps(build_pyqt_pending_payload(request), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temp_path.replace(target_path)
    return target_path


def launch_existing_pending(
    *,
    updater_executable: Path,
    pending_manifest_path: Path,
    popen: PopenFactory | None = None,
) -> LaunchResult:
    """启动 updater 并传入已有 pending manifest。

    :param updater_executable: updater 可执行文件路径。
    :param pending_manifest_path: pending-update.json 路径。
    :param popen: 可注入进程启动函数。
    :return: updater 启动结果。
    """
    executable = Path(updater_executable)
    pending_path = Path(pending_manifest_path)
    if not executable.is_file():
        raise UpdateError(UpdateErrorCode.UPDATER_NOT_FOUND, f"updater not found: {executable}")
    if not pending_path.is_file():
        raise UpdateError(UpdateErrorCode.MANIFEST_NOT_FOUND, f"pending manifest not found: {pending_path}")
    popen_factory = popen or subprocess.Popen
    try:
        process = popen_factory(
            [str(executable), "apply", "--pending", str(pending_path), "--restart"],
            cwd=str(executable.parent),
            close_fds=True,
        )
    except OSError as exc:
        raise UpdateError(UpdateErrorCode.UPDATER_LAUNCH_FAILED, f"updater launch failed: {executable}") from exc
    return LaunchResult(
        started=True,
        updater_pid=getattr(process, "pid", None),
        pending_manifest_path=pending_path,
    )
