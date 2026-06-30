"""独立 updater 进程启动器。"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from update_online_tool.errors import UpdateError, UpdateErrorCode

PopenFactory = Callable[..., Any]


@dataclass(frozen=True)
class LaunchResult:
    """updater 启动结果。

    :param started: 是否已启动。
    :param updater_pid: updater 进程号。
    :param pending_manifest_path: pending manifest 路径。
    :return: None
    """

    started: bool
    updater_pid: int | None
    pending_manifest_path: Path


class StandaloneUpdaterLauncher:
    """独立 updater 启动器。

    :param updater_executable: updater 可执行文件路径。
    :param popen: 可注入进程启动函数。
    :return: None
    """

    def __init__(self, updater_executable: Path, *, popen: PopenFactory | None = None) -> None:
        """保存启动器配置。

        :param updater_executable: updater 可执行文件路径。
        :param popen: 可注入进程启动函数。
        :return: None
        """
        self.updater_executable = Path(updater_executable)
        self._popen = popen or subprocess.Popen

    def launch(self, *, pending_payload: dict[str, object], pending_manifest_path: Path) -> LaunchResult:
        """写入 pending manifest 并启动 updater。

        :param pending_payload: pending manifest 内容。
        :param pending_manifest_path: pending manifest 路径。
        :return: 启动结果。
        """
        if not self.updater_executable.is_file():
            raise UpdateError(UpdateErrorCode.UPDATER_NOT_FOUND, f"updater not found: {self.updater_executable}")
        pending_manifest_path = Path(pending_manifest_path)
        pending_manifest_path.parent.mkdir(parents=True, exist_ok=True)
        pending_manifest_path.write_text(
            json.dumps(pending_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        command = [str(self.updater_executable), "apply", "--pending", str(pending_manifest_path), "--restart"]
        old_pid = _coerce_positive_int(pending_payload.get("old_pid"))
        if old_pid is not None:
            command.extend(["--wait-pid", str(old_pid)])
        try:
            process = self._popen(
                command,
                cwd=str(self.updater_executable.parent),
                close_fds=True,
            )
        except OSError as exc:
            raise UpdateError(
                UpdateErrorCode.UPDATER_LAUNCH_FAILED,
                f"updater launch failed: {self.updater_executable}",
            ) from exc
        return LaunchResult(
            started=True,
            updater_pid=getattr(process, "pid", None),
            pending_manifest_path=pending_manifest_path,
        )


def _coerce_positive_int(value: object) -> int | None:
    """解析正整数 PID。"""
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
