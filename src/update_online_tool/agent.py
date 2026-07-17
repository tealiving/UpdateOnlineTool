"""独立 Update Agent 的 durable request 与运行时。"""

from __future__ import annotations

import json
import os
import re
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.release_contract import normalize_release_required_paths
from update_online_tool.runtime import (
    RuntimeResult,
    apply_pending_update,
    rollback_installation,
    switch_installed_release,
)


@dataclass(frozen=True)
class AgentRequest:
    """宿主交给独立 Update Agent 的持久化更新请求。"""

    operation_id: str
    action: str
    install_root: Path
    pending_path: Path | None
    target_version: str
    old_pid: int | None
    wait_timeout: float
    handoff_timeout: float
    bootstrap_command: tuple[str, ...]
    release_required_paths: tuple[str, ...] = ()

    def to_payload(self) -> dict[str, object]:
        """转换为可审计且不含密钥的 JSON 负载。"""
        payload: dict[str, object] = {
            "schema_version": 1,
            "operation_id": self.operation_id,
            "action": self.action,
            "install_root": str(self.install_root),
            "pending_path": str(self.pending_path) if self.pending_path is not None else None,
            "target_version": self.target_version,
            "old_pid": self.old_pid,
            "wait_timeout": self.wait_timeout,
            "handoff_timeout": self.handoff_timeout,
            "bootstrap_command": list(self.bootstrap_command),
        }
        if self.release_required_paths:
            payload["release_required_paths"] = list(self.release_required_paths)
        return payload


@dataclass(frozen=True)
class AgentRunResult:
    """Agent 完成一次更新请求的结果。"""

    operation_id: str
    success: bool
    runtime_result: RuntimeResult
    bootstrap_pid: int


@dataclass(frozen=True)
class AgentLaunchResult:
    """宿主启动独立 Agent 并获得 ready 确认的结果。"""

    operation_id: str
    agent_pid: int
    request_path: Path


ApplyPending = Callable[..., RuntimeResult]
SwitchRelease = Callable[..., RuntimeResult]
RollbackInstallation = Callable[..., RuntimeResult]
BootstrapLauncher = Callable[[tuple[str, ...], Path], int]
HandoffWaiter = Callable[[Path, AgentRequest], None]


class UpdateAgent:
    """在宿主退出后执行 UOT runtime 并重启稳定 Bootstrap。

    这个 Module 的 Interface 仅接受 request 文件并返回事务结果。宿主无需知道
    PID 等待、安装细节或 Bootstrap 启动参数；生产环境可用原生二进制替换本
    Python 实现而不改变 request 契约。
    """

    def __init__(
        self,
        *,
        apply_pending: ApplyPending = apply_pending_update,
        switch_release: SwitchRelease = switch_installed_release,
        rollback: RollbackInstallation = rollback_installation,
        launch_bootstrap: BootstrapLauncher | None = None,
        wait_for_handoff: HandoffWaiter | None = None,
    ) -> None:
        """初始化 Agent 的可替换内部依赖。"""
        self._apply_pending = apply_pending
        self._switch_release = switch_release
        self._rollback = rollback
        self._launch_bootstrap = launch_bootstrap or _launch_bootstrap_process
        self._wait_for_handoff = wait_for_handoff or _wait_for_handoff

    def run_request(self, request_path: Path) -> AgentRunResult:
        """执行请求：ready、安装事务、稳定 Bootstrap 重启。"""
        path = Path(request_path)
        request = read_agent_request(path)
        _ensure_agent_request_path(request.install_root, request.operation_id, path)
        _write_agent_status(path, request, phase="ready", message="agent is ready")
        try:
            self._wait_for_handoff(path, request)
            _write_agent_status(path, request, phase="applying", message=f"waiting for old process and {request.action}")
            runtime_result = self._run_runtime(request)
            if not runtime_result.success:
                raise UpdateError(UpdateErrorCode.UPDATER_LAUNCH_FAILED, "update runtime returned an unsuccessful result")
            _write_agent_status(path, request, phase="restarting_bootstrap", message="starting stable bootstrap")
            bootstrap_pid = self._launch_bootstrap(request.bootstrap_command, request.install_root)
            result = AgentRunResult(
                operation_id=request.operation_id,
                success=True,
                runtime_result=runtime_result,
                bootstrap_pid=bootstrap_pid,
            )
            _write_agent_status(path, request, phase="success", message="update installed and bootstrap started", bootstrap_pid=bootstrap_pid)
            return result
        except Exception as exc:
            message = str(exc)
            _write_agent_status(path, request, phase="failed", message=message, error=message)
            if isinstance(exc, UpdateError):
                raise
            raise UpdateError(UpdateErrorCode.UPDATER_LAUNCH_FAILED, f"agent request failed: {exc}") from exc

    def _run_runtime(self, request: AgentRequest) -> RuntimeResult:
        """按 request action 调用同一个 UOT runtime，禁止其直接重启 release。"""
        if request.action == "apply":
            if request.pending_path is None:
                raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "apply request requires pending_path")
            release_kwargs: dict[str, object] = {}
            if request.release_required_paths:
                release_kwargs["release_required_paths"] = request.release_required_paths
            return self._apply_pending(
                pending_path=request.pending_path,
                wait_pid=request.old_pid,
                wait_timeout=request.wait_timeout,
                restart=False,
                **release_kwargs,
            )
        if request.action == "switch":
            release_kwargs = {}
            if request.release_required_paths:
                release_kwargs["release_required_paths"] = request.release_required_paths
            return self._switch_release(
                install_root=request.install_root,
                version=request.target_version,
                wait_pid=request.old_pid,
                wait_timeout=request.wait_timeout,
                restart=False,
                **release_kwargs,
            )
        if request.action == "rollback":
            release_kwargs = {}
            if request.release_required_paths:
                release_kwargs["release_required_paths"] = request.release_required_paths
            return self._rollback(
                install_root=request.install_root,
                wait_pid=request.old_pid,
                wait_timeout=request.wait_timeout,
                restart=False,
                **release_kwargs,
            )
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"unsupported agent action: {request.action}")


class UpdateAgentLauncher:
    """由宿主启动 Agent 并等待 ready 状态的薄适配器。"""

    def __init__(
        self,
        agent_executable: Path,
        *,
        popen: Callable[..., Any] = subprocess.Popen,
        poll_interval: float = 0.05,
    ) -> None:
        """保存 Agent 可执行文件和可注入进程启动函数。"""
        self.agent_executable = Path(agent_executable)
        self._popen = popen
        self._poll_interval = max(0.001, float(poll_interval))

    def start(self, request: AgentRequest, *, ready_timeout: float = 30.0) -> AgentLaunchResult:
        """写入 request、启动 Agent，并确认其已等待宿主交接。"""
        if not self.agent_executable.is_file():
            raise UpdateError(UpdateErrorCode.UPDATER_NOT_FOUND, f"update agent not found: {self.agent_executable}")
        if ready_timeout < 0:
            raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "ready_timeout must be non-negative")
        request_path = write_agent_request(request)
        command = [str(self.agent_executable), request.action, "--request", str(request_path)]
        try:
            process = self._popen(
                command,
                cwd=str(request.install_root),
                close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except OSError as exc:
            raise UpdateError(UpdateErrorCode.UPDATER_LAUNCH_FAILED, f"update agent launch failed: {exc}") from exc
        try:
            self._wait_until_ready(process, request_path, request.operation_id, timeout=ready_timeout)
        except UpdateError as exc:
            _stop_agent_process(process)
            _write_agent_status(
                request_path,
                request,
                phase="failed",
                message=exc.message,
                error=exc.message,
            )
            raise
        return AgentLaunchResult(
            operation_id=request.operation_id,
            agent_pid=int(process.pid),
            request_path=request_path,
        )

    def confirm_handoff(self, request_path: Path) -> Path:
        """在宿主保存工作后确认交接，使 Agent 开始等待旧 PID。"""
        return write_agent_handoff(request_path)

    def _wait_until_ready(self, process: Any, request_path: Path, operation_id: str, *, timeout: float) -> None:
        """等待 Agent 写入 ready；失败或超时则拒绝宿主退出。"""
        deadline = time.monotonic() + timeout
        while True:
            exit_code = _process_exit_code(process)
            if exit_code is not None:
                raise UpdateError(
                    UpdateErrorCode.UPDATER_LAUNCH_FAILED,
                    f"update agent exited before ready with code {exit_code}",
                )
            status_path = _status_path(request_path)
            if status_path.is_file():
                status = read_agent_status(request_path)
                if status.get("operation_id") != operation_id:
                    raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "agent ready status does not match request operation_id")
                phase = status.get("phase")
                if phase == "ready":
                    return
                if phase == "failed":
                    raise UpdateError(UpdateErrorCode.UPDATER_LAUNCH_FAILED, str(status.get("error") or status.get("message") or "agent failed"))
            if time.monotonic() >= deadline:
                raise UpdateError(UpdateErrorCode.UPDATER_LAUNCH_FAILED, f"update agent did not become ready within {timeout:g}s")
            time.sleep(self._poll_interval)


def create_apply_request(
    *,
    install_root: Path,
    pending_path: Path,
    bootstrap_command: tuple[str, ...],
    old_pid: int | None = None,
    wait_timeout: float = 60.0,
    handoff_timeout: float = 30.0,
    operation_id: str = "",
    release_required_paths: tuple[str, ...] | list[str] = (),
) -> AgentRequest:
    """创建由宿主写入并交给独立 Agent 的 apply 请求。"""
    return _create_request(
        action="apply",
        install_root=install_root,
        pending_path=pending_path,
        target_version="",
        old_pid=old_pid,
        wait_timeout=wait_timeout,
        handoff_timeout=handoff_timeout,
        bootstrap_command=bootstrap_command,
        operation_id=operation_id,
        release_required_paths=release_required_paths,
    )


def create_switch_request(
    *,
    install_root: Path,
    version: str,
    bootstrap_command: tuple[str, ...],
    old_pid: int | None = None,
    wait_timeout: float = 60.0,
    handoff_timeout: float = 30.0,
    operation_id: str = "",
    release_required_paths: tuple[str, ...] | list[str] = (),
) -> AgentRequest:
    """创建本地 release 切换的独立 Agent 请求。"""
    target_version = str(version).strip()
    if not target_version:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "version must be non-empty")
    return _create_request(
        action="switch",
        install_root=install_root,
        pending_path=None,
        target_version=target_version,
        bootstrap_command=bootstrap_command,
        old_pid=old_pid,
        wait_timeout=wait_timeout,
        handoff_timeout=handoff_timeout,
        operation_id=operation_id,
        release_required_paths=release_required_paths,
    )


def create_rollback_request(
    *,
    install_root: Path,
    bootstrap_command: tuple[str, ...],
    old_pid: int | None = None,
    wait_timeout: float = 60.0,
    handoff_timeout: float = 30.0,
    operation_id: str = "",
    release_required_paths: tuple[str, ...] | list[str] = (),
) -> AgentRequest:
    """创建回滚到 previous_version 的独立 Agent 请求。"""
    return _create_request(
        action="rollback",
        install_root=install_root,
        pending_path=None,
        target_version="",
        bootstrap_command=bootstrap_command,
        old_pid=old_pid,
        wait_timeout=wait_timeout,
        handoff_timeout=handoff_timeout,
        operation_id=operation_id,
        release_required_paths=release_required_paths,
    )


def _create_request(
    *,
    action: str,
    install_root: Path,
    pending_path: Path | None,
    target_version: str,
    bootstrap_command: tuple[str, ...],
    old_pid: int | None,
    wait_timeout: float,
    handoff_timeout: float,
    operation_id: str,
    release_required_paths: tuple[str, ...] | list[str],
) -> AgentRequest:
    """校验并创建三类 Agent request 的公共字段。"""
    root = Path(install_root)
    normalized_operation_id = str(operation_id or uuid4().hex).strip()
    _validate_operation_id(normalized_operation_id, error_code=UpdateErrorCode.SETTINGS_INVALID)
    if old_pid is not None and old_pid <= 0:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "old_pid must be positive")
    if wait_timeout < 0:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "wait_timeout must be non-negative")
    if handoff_timeout < 0:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "handoff_timeout must be non-negative")
    command = tuple(str(item).strip() for item in bootstrap_command)
    if not command or any(not item for item in command):
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "bootstrap_command must contain non-empty strings")
    return AgentRequest(
        operation_id=normalized_operation_id,
        action=action,
        install_root=root,
        pending_path=Path(pending_path) if pending_path is not None else None,
        target_version=target_version,
        old_pid=old_pid,
        wait_timeout=float(wait_timeout),
        handoff_timeout=float(handoff_timeout),
        bootstrap_command=command,
        release_required_paths=normalize_release_required_paths(release_required_paths),
    )


def write_agent_request(request: AgentRequest, path: Path | None = None) -> Path:
    """原子写入 Agent request，并返回其绝对位置。"""
    target = Path(path) if path is not None else _request_path(request.install_root, request.operation_id)
    _ensure_agent_request_path(request.install_root, request.operation_id, target)
    _write_json_atomic(target, request.to_payload())
    return target


def read_agent_request(path: Path) -> AgentRequest:
    """读取并校验 Agent request JSON。"""
    payload = _read_json_object(Path(path), "agent request")
    if payload.get("schema_version") != 1:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "agent request schema_version must be 1")
    action = payload.get("action")
    if action not in {"apply", "switch", "rollback"}:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "agent request action must be apply, switch, or rollback")
    operation_id = _require_text(payload, "operation_id")
    _validate_operation_id(operation_id, error_code=UpdateErrorCode.MANIFEST_INVALID)
    install_root = Path(_require_text(payload, "install_root"))
    pending_path = _optional_path(payload.get("pending_path"), "pending_path")
    target_version = _optional_text(payload.get("target_version"), "target_version")
    if action == "apply" and pending_path is None:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "apply agent request requires pending_path")
    if action == "switch" and not target_version:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "switch agent request requires target_version")
    old_pid = _optional_positive_int(payload.get("old_pid"), "old_pid")
    wait_timeout = _non_negative_number(payload.get("wait_timeout"), "wait_timeout")
    handoff_timeout = _non_negative_number(payload.get("handoff_timeout"), "handoff_timeout")
    bootstrap_command = _command(payload.get("bootstrap_command"))
    release_required_paths = normalize_release_required_paths(payload.get("release_required_paths"))
    return AgentRequest(
        operation_id=operation_id,
        action=action,
        install_root=install_root,
        pending_path=pending_path,
        target_version=target_version,
        old_pid=old_pid,
        wait_timeout=wait_timeout,
        handoff_timeout=handoff_timeout,
        bootstrap_command=bootstrap_command,
        release_required_paths=release_required_paths,
    )


def read_agent_status(request_path: Path) -> dict[str, object]:
    """读取某个 request 的最新 Agent 状态。"""
    return _read_json_object(_status_path(Path(request_path)), "agent status")


def write_agent_handoff(request_path: Path) -> Path:
    """由宿主写入交接确认，允许已就绪 Agent 开始等待旧 PID。"""
    path = Path(request_path)
    request = read_agent_request(path)
    _ensure_agent_request_path(request.install_root, request.operation_id, path)
    handoff_path = _handoff_path(path)
    _write_json_atomic(
        handoff_path,
        {
            "schema_version": 1,
            "operation_id": request.operation_id,
            "confirmed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        },
    )
    return handoff_path


def _write_agent_status(
    request_path: Path,
    request: AgentRequest,
    *,
    phase: str,
    message: str,
    error: str = "",
    bootstrap_pid: int | None = None,
) -> None:
    """原子写入可由宿主轮询的 Agent 状态。"""
    payload: dict[str, object] = {
        "schema_version": 1,
        "operation_id": request.operation_id,
        "phase": phase,
        "message": message,
        "agent_pid": os.getpid(),
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if error:
        payload["error"] = error
    if bootstrap_pid is not None:
        payload["bootstrap_pid"] = bootstrap_pid
    _write_json_atomic(_status_path(request_path), payload)


def _request_path(install_root: Path, operation_id: str) -> Path:
    """返回安装根中 operation 专属 request 路径。"""
    _validate_operation_id(operation_id, error_code=UpdateErrorCode.SETTINGS_INVALID)
    return Path(install_root) / "operations" / f"{operation_id}.request.json"


def _validate_operation_id(operation_id: str, *, error_code: UpdateErrorCode) -> None:
    """验证 operation ID 只能作为 operations 目录中的文件名。

    :param operation_id: 由宿主生成或传入的操作标识。
    :param error_code: 校验失败时使用的 UOT 错误码。
    :return: None
    """
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", operation_id):
        raise UpdateError(error_code, "operation_id must contain only letters, digits, underscores, or hyphens")


def _ensure_agent_request_path(install_root: Path, operation_id: str, path: Path) -> None:
    """确认 Agent request 使用约定的安装根 operations 路径。

    :param install_root: UOT 安装根目录。
    :param operation_id: request 中的安全操作标识。
    :param path: 待写入的 request 文件路径。
    :return: None
    """
    expected = _request_path(install_root, operation_id).resolve()
    if Path(path).resolve() != expected:
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID,
            "agent request path must equal install_root/operations/<operation_id>.request.json",
        )


def _process_exit_code(process: Any) -> int | None:
    """读取 Agent 子进程退出状态，并兼容测试替身。

    :param process: ``subprocess.Popen`` 或兼容测试对象。
    :return: 已退出时的退出码；仍运行或不可查询时返回 ``None``。
    """
    poll = getattr(process, "poll", None)
    if not callable(poll):
        return None
    result = poll()
    return int(result) if isinstance(result, int) else None


def _stop_agent_process(process: Any) -> None:
    """在 ready 失败后终止仍在等待交接的 Agent。

    :param process: ``subprocess.Popen`` 或兼容测试对象。
    :return: None
    """
    if _process_exit_code(process) is not None:
        return
    terminate = getattr(process, "terminate", None)
    if callable(terminate):
        try:
            terminate()
        except OSError:
            return
    wait = getattr(process, "wait", None)
    if not callable(wait):
        return
    try:
        wait(timeout=1.0)
    except (subprocess.TimeoutExpired, TypeError):
        kill = getattr(process, "kill", None)
        if callable(kill):
            try:
                kill()
            except OSError:
                return


def _status_path(request_path: Path) -> Path:
    """由 request 路径派生对应状态路径。"""
    suffix = ".request.json"
    if not request_path.name.endswith(suffix):
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"agent request path must end with {suffix}")
    return request_path.with_name(f"{request_path.name.removesuffix(suffix)}.status.json")


def _handoff_path(request_path: Path) -> Path:
    """由 request 路径派生宿主确认路径。"""
    suffix = ".request.json"
    if not request_path.name.endswith(suffix):
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"agent request path must end with {suffix}")
    return request_path.with_name(f"{request_path.name.removesuffix(suffix)}.handoff.json")


def _wait_for_handoff(request_path: Path, request: AgentRequest) -> None:
    """等待宿主确认已完成保存并即将退出。"""
    handoff_path = _handoff_path(request_path)
    deadline = time.monotonic() + request.handoff_timeout
    while not handoff_path.is_file():
        if time.monotonic() >= deadline:
            raise UpdateError(
                UpdateErrorCode.PROCESS_TIMEOUT,
                f"host did not confirm update handoff within {request.handoff_timeout:g}s",
            )
        time.sleep(0.05)
    payload = _read_json_object(handoff_path, "agent handoff")
    if payload.get("schema_version") != 1 or payload.get("operation_id") != request.operation_id:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "agent handoff does not match request operation_id")


def _launch_bootstrap_process(command: tuple[str, ...], cwd: Path) -> int:
    """启动稳定 Bootstrap，而不是直接启动版本化 release。"""
    try:
        process = subprocess.Popen(list(command), cwd=str(cwd), close_fds=True)
    except OSError as exc:
        raise UpdateError(UpdateErrorCode.UPDATER_LAUNCH_FAILED, f"bootstrap launch failed: {exc}") from exc
    return process.pid


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    """以同目录临时文件原子替换 JSON 状态。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        temporary.replace(path)
    except OSError as exc:
        if temporary.exists():
            temporary.unlink()
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"cannot write {path}: {exc}") from exc


def _read_json_object(path: Path, context: str) -> dict[str, Any]:
    """读取一个 JSON object。"""
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise UpdateError(UpdateErrorCode.MANIFEST_NOT_FOUND, f"{context} not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"{context} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"{context} must be a JSON object: {path}")
    return payload


def _require_text(payload: dict[str, Any], key: str) -> str:
    """读取必填非空字符串。"""
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"agent request {key} must be a non-empty string")
    return value.strip()


def _optional_text(value: object, key: str) -> str:
    """读取可选字符串。"""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"agent request {key} must be a string")
    return value.strip()


def _optional_path(value: object, key: str) -> Path | None:
    """读取可选路径字符串。"""
    text = _optional_text(value, key)
    return Path(text) if text else None


def _optional_positive_int(value: object, key: str) -> int | None:
    """读取可选正整数。"""
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"agent request {key} must be a positive integer or null")
    return value


def _non_negative_number(value: object, key: str) -> float:
    """读取非负数值。"""
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"agent request {key} must be a non-negative number")
    return float(value)


def _command(value: object) -> tuple[str, ...]:
    """读取不可为空的 Bootstrap 命令列表。"""
    if not isinstance(value, list) or not value:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "agent request bootstrap_command must be a non-empty array")
    command = tuple(item.strip() for item in value if isinstance(item, str))
    if len(command) != len(value) or any(not item for item in command):
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "agent request bootstrap_command must contain non-empty strings")
    return command
