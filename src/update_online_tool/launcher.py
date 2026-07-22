"""独立 updater 进程启动器。"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from update_online_tool.errors import UpdateError, UpdateErrorCode

PopenFactory = Callable[..., Any]
UPDATER_STARTUP_PROBE_TIMEOUT_SECONDS = 0.25


@dataclass(frozen=True)
class LaunchResult:
    """updater 启动结果。

    :param started: 是否已启动。
    :param updater_pid: updater 进程号。
    :param pending_manifest_path: pending manifest 路径；非 pending 命令为空。
    :return: None
    """

    started: bool
    updater_pid: int | None
    pending_manifest_path: Path | None


class StandaloneUpdaterLauncher:
    """独立 updater 启动器。

    :param updater_executable: updater 可执行文件路径。
    :param popen: 可注入进程启动函数。
    :return: None
    """

    def __init__(
        self, updater_executable: Path, *, popen: PopenFactory | None = None
    ) -> None:
        """保存启动器配置。

        :param updater_executable: updater 可执行文件路径。
        :param popen: 可注入进程启动函数。
        :return: None
        """
        self.updater_executable = Path(updater_executable)
        self._popen = popen or subprocess.Popen

    def launch(
        self, *, pending_payload: dict[str, object], pending_manifest_path: Path
    ) -> LaunchResult:
        """写入 pending manifest 并启动 updater。

        :param pending_payload: pending manifest 内容。
        :param pending_manifest_path: pending manifest 路径。
        :return: 启动结果。
        """
        self.write_pending(
            pending_payload=pending_payload, pending_manifest_path=pending_manifest_path
        )
        return self.launch_existing_pending(pending_manifest_path=pending_manifest_path)

    def write_pending(
        self, *, pending_payload: dict[str, object], pending_manifest_path: Path
    ) -> Path:
        """写入 pending manifest，但不启动或校验 updater。

        Bootstrap/Agent 模式由独立 Agent 消费 pending 文件，不携带旧
        ``uot-updater`` sidecar；仅在真正启动标准 updater 时才校验它。
        """
        pending_path = Path(pending_manifest_path)
        pending_path.parent.mkdir(parents=True, exist_ok=True)
        pending_path.write_text(
            json.dumps(pending_payload, ensure_ascii=False, indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        return pending_path

    def launch_existing_pending(self, *, pending_manifest_path: Path) -> LaunchResult:
        """启动已写入的 pending manifest。"""
        self._ensure_updater_exists()
        pending_path = Path(pending_manifest_path)
        try:
            pending_payload = json.loads(pending_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise UpdateError(
                UpdateErrorCode.MANIFEST_NOT_FOUND,
                f"pending manifest not found: {pending_path}",
            ) from exc
        except json.JSONDecodeError as exc:
            raise UpdateError(
                UpdateErrorCode.MANIFEST_INVALID,
                f"pending manifest is not valid JSON: {pending_path}",
            ) from exc
        if not isinstance(pending_payload, dict):
            raise UpdateError(
                UpdateErrorCode.MANIFEST_INVALID,
                f"pending manifest must be a JSON object: {pending_path}",
            )
        updater_executable, staged_root = self._stage_sidecar_updater(pending_path)
        command = [
            str(updater_executable),
            "apply",
            "--pending",
            str(pending_path),
        ]
        restart = _coerce_bool(pending_payload.get("restart"), default=True)
        if restart:
            command.append("--restart")
        if _coerce_bool(pending_payload.get("force"), default=False):
            command.append("--force")
        signature_key = _coerce_non_empty_string(pending_payload.get("signature_key"))
        if signature_key is not None:
            command.extend(["--signature-key", signature_key])
        restart_executable = _coerce_non_empty_string(
            pending_payload.get("restart_executable")
        )
        if restart_executable is not None:
            command.extend(["--entry-name", restart_executable])
        old_pid = _coerce_positive_int(pending_payload.get("old_pid"))
        if old_pid is not None:
            command.extend(["--wait-pid", str(old_pid)])
            wait_timeout = _coerce_float(pending_payload.get("wait_timeout"))
            if wait_timeout is not None:
                command.extend(["--wait-timeout", str(wait_timeout)])
        try:
            process = launch_updater_process(
                command,
                updater_executable=updater_executable,
                popen=self._popen,
                require_running=old_pid is not None,
            )
        except UpdateError:
            if staged_root is not None:
                shutil.rmtree(staged_root, ignore_errors=True)
            raise
        return LaunchResult(
            started=True,
            updater_pid=getattr(process, "pid", None),
            pending_manifest_path=pending_path,
        )

    def _ensure_updater_exists(self) -> None:
        """确认独立 updater 已就绪。"""
        if not self.updater_executable.is_file():
            raise UpdateError(
                UpdateErrorCode.UPDATER_NOT_FOUND,
                f"updater not found: {self.updater_executable}",
            )

    def _stage_sidecar_updater(self, pending_path: Path) -> tuple[Path, Path | None]:
        """从临时副本运行安装根 sidecar，避免 Windows 锁住待升级的 updater。"""
        sidecar_root = pending_path.parent / "updater"
        try:
            relative_executable = self.updater_executable.relative_to(sidecar_root)
        except ValueError:
            return self.updater_executable, None
        staged_root = Path(tempfile.mkdtemp(prefix="uot-updater-"))
        try:
            shutil.copytree(sidecar_root, staged_root / "updater", symlinks=True)
        except OSError as exc:
            shutil.rmtree(staged_root, ignore_errors=True)
            raise UpdateError(
                UpdateErrorCode.UPDATER_LAUNCH_FAILED,
                f"updater staging failed: {self.updater_executable}",
            ) from exc
        return staged_root / "updater" / relative_executable, staged_root


def launch_updater_process(
    command: Sequence[str],
    *,
    updater_executable: Path,
    popen: PopenFactory | None = None,
    require_running: bool = False,
) -> object:
    """启动 updater 并拒绝启动窗口内的异常退出。

    :param command: 完整 updater 命令。
    :param updater_executable: updater 可执行文件路径。
    :param popen: 可注入进程启动函数。
    :param require_running: 启动探测结束时是否必须仍在运行。
    :return: 已通过启动探测的进程对象。
    """
    factory = popen or subprocess.Popen
    try:
        process = factory(
            list(command),
            cwd=str(Path(updater_executable).parent),
            close_fds=True,
        )
    except OSError as exc:
        raise UpdateError(
            UpdateErrorCode.UPDATER_LAUNCH_FAILED,
            f"updater launch failed: {updater_executable}",
        ) from exc
    _raise_for_immediate_updater_failure(
        process,
        updater_executable=Path(updater_executable),
        require_running=require_running,
    )
    return process


def _raise_for_immediate_updater_failure(
    process: object, *, updater_executable: Path, require_running: bool = False
) -> None:
    """在有限启动窗口内拒绝立即异常退出的 updater。

    :param process: ``subprocess.Popen`` 或兼容进程对象。
    :param updater_executable: updater 可执行文件路径。
    :param require_running: 探测结束时是否必须仍在运行。
    :return: None。
    """

    wait = getattr(process, "wait", None)
    if not callable(wait):
        return
    try:
        return_code = wait(timeout=UPDATER_STARTUP_PROBE_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired:
        return
    except TypeError:
        return
    if require_running and return_code is not None:
        raise UpdateError(
            UpdateErrorCode.UPDATER_LAUNCH_FAILED,
            f"updater exited before old process handoff with exit code {return_code}: {updater_executable}",
        )
    if return_code not in {None, 0}:
        raise UpdateError(
            UpdateErrorCode.UPDATER_LAUNCH_FAILED,
            f"updater exited during startup with exit code {return_code}: {updater_executable}",
        )


def _coerce_positive_int(value: object) -> int | None:
    """解析正整数 PID。

    :param value: 待解析值。
    :return: 正整数；无效时返回 None。
    """
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _coerce_non_empty_string(value: object) -> str | None:
    """解析非空字符串。

    :param value: 待解析值。
    :return: 去空格后的字符串；无效时返回 None。
    """
    if not isinstance(value, str):
        return None
    parsed = value.strip()
    return parsed or None


def _coerce_bool(value: object, *, default: bool) -> bool:
    """解析布尔值。

    :param value: 待解析值。
    :param default: 非布尔值时使用的默认值。
    :return: 解析后的布尔值。
    """
    return value if isinstance(value, bool) else default


def _coerce_float(value: object) -> float | None:
    """解析非负浮点数。

    :param value: 待解析值。
    :return: 非负浮点数；无效时返回 None。
    """
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None
