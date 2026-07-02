"""标准 updater runtime。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.install_root import missing_current_json_message, normalize_install_root
from update_online_tool.installed import switch_installed_version as _switch_installed_version
from update_online_tool.locks import runtime_lock
from update_online_tool.manifest import UpdateManifest
from update_online_tool.signature import verify_manifest_signature_with_key_file


@dataclass(frozen=True)
class RuntimeResult:
    """updater runtime 执行结果。"""

    success: bool
    action: str
    version: str
    previous_version: str
    release_dir: Path
    message: str
    restarted_pid: int | None = None
    elapsed_ms: int = 0
    phase_durations_ms: dict[str, int] = field(default_factory=dict)

    def to_payload(self) -> dict[str, object]:
        """转换为 update-result.json 负载。"""
        payload: dict[str, object] = {
            "success": self.success,
            "action": self.action,
            "version": self.version,
            "previous_version": self.previous_version,
            "release_dir": str(self.release_dir),
            "message": self.message,
        }
        if self.restarted_pid is not None:
            payload["restarted_pid"] = self.restarted_pid
        payload["elapsed_ms"] = self.elapsed_ms
        if self.phase_durations_ms:
            payload["phase_durations_ms"] = dict(self.phase_durations_ms)
        return payload


@dataclass(frozen=True)
class RuntimeStatus:
    """updater runtime 阶段状态。"""

    phase: str
    percent: int
    message: str
    version: str
    previous_version: str
    action: str = "install_prepared"
    error: str = ""
    started_at: str = ""
    phase_started_at: str = ""
    phase_elapsed_ms: int = 0
    total_elapsed_ms: int = 0

    def to_payload(self) -> dict[str, object]:
        """转换为 update-status.json 负载。"""
        payload: dict[str, object] = {
            "schema_version": 1,
            "phase": self.phase,
            "percent": max(0, min(100, int(self.percent))),
            "message": self.message,
            "version": self.version,
            "previous_version": self.previous_version,
            "action": self.action,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        if self.started_at:
            payload["started_at"] = self.started_at
        if self.phase_started_at:
            payload["phase_started_at"] = self.phase_started_at
        if self.phase_elapsed_ms:
            payload["phase_elapsed_ms"] = self.phase_elapsed_ms
        if self.total_elapsed_ms:
            payload["total_elapsed_ms"] = self.total_elapsed_ms
        if self.error:
            payload["error"] = self.error
        return payload


class RuntimeStatusTracker:
    """记录 runtime 阶段状态和耗时。"""

    def __init__(self, *, action: str, version: str, previous_version: str) -> None:
        """初始化阶段计时器。"""
        self.action = action
        self.version = version
        self.previous_version = previous_version
        self.started_monotonic = time.monotonic()
        self.phase_monotonic = self.started_monotonic
        self.started_at = _utc_now_iso()
        self.phase_started_at = self.started_at
        self.current_phase = ""
        self.phase_durations_ms: dict[str, int] = {}

    def status(self, *, phase: str, percent: int, message: str, error: str = "") -> RuntimeStatus:
        """创建带耗时信息的阶段状态。"""
        now = time.monotonic()
        if phase != self.current_phase:
            if self.current_phase:
                self.phase_durations_ms[self.current_phase] = int((now - self.phase_monotonic) * 1000)
            self.current_phase = phase
            self.phase_monotonic = now
            self.phase_started_at = _utc_now_iso()
        return RuntimeStatus(
            phase=phase,
            percent=percent,
            message=message,
            version=self.version,
            previous_version=self.previous_version,
            action=self.action,
            error=error,
            started_at=self.started_at,
            phase_started_at=self.phase_started_at,
            phase_elapsed_ms=int((now - self.phase_monotonic) * 1000),
            total_elapsed_ms=int((now - self.started_monotonic) * 1000),
        )

    def finish(self) -> tuple[int, dict[str, int]]:
        """结束计时并返回总耗时和阶段耗时。"""
        now = time.monotonic()
        if self.current_phase:
            self.phase_durations_ms[self.current_phase] = int((now - self.phase_monotonic) * 1000)
        return int((now - self.started_monotonic) * 1000), dict(self.phase_durations_ms)


@dataclass(frozen=True)
class SidecarPromotion:
    """sidecar 提升事务状态。

    :param backup_root: sidecar 备份目录。
    :param backups: 原始目标与备份路径。
    :param touched_targets: 已写入的新 sidecar 目标。
    :return: None
    """

    backup_root: Path
    backups: list[tuple[Path, Path]]
    touched_targets: list[Path]


def install_prepared_package(
    *,
    install_root: Path,
    package_path: Path,
    manifest: UpdateManifest,
    entry_name: str = "",
    switch_current: bool = True,
    force: bool = False,
    dry_run: bool = False,
    wait_pid: int | None = None,
    wait_timeout: float = 60.0,
    restart: bool = False,
) -> RuntimeResult:
    """安装一个已准备好的升级包到 releases/<version>。

    :param install_root: 安装根目录。
    :param package_path: 本地升级包。
    :param manifest: 对应 manifest。
    :param entry_name: release 入口名；为空时从 current.json 推断。
    :param switch_current: 安装后是否切换 current.json。
    :param force: 目标 release 已存在时是否覆盖。
    :param dry_run: 只校验安装计划，不写入 release、current.json 或 update-result.json。
    :param wait_pid: 安装前等待退出的旧进程 PID。
    :param wait_timeout: 等待旧进程退出的超时时间，单位秒。
    :param restart: 安装成功后启动 current.json 指向的入口。
    :return: runtime 结果。
    """
    root = normalize_install_root(Path(install_root))
    package = Path(package_path)
    previous_version = _current_version(root)
    releases_root = root / "releases"
    target_release_dir = releases_root / manifest.version
    temp_release_dir = root / f".update-{manifest.version}.{os.getpid()}.{uuid4().hex}.tmp"
    release_backup_dir: Path | None = None
    target_release_replaced = False
    can_rollback_release = True
    sidecar_promotion: SidecarPromotion | None = None
    tracker = RuntimeStatusTracker(
        action="install_prepared",
        version=manifest.version,
        previous_version=previous_version,
    )
    try:
        if restart and not switch_current:
            raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "restart requires switching current.json")
        if wait_pid is not None and not dry_run:
            write_update_status(
                root,
                tracker.status(
                    phase="waiting_old_process",
                    percent=10,
                    message=f"waiting for old process {wait_pid} to exit",
                ),
            )
            wait_for_process_exit(pid=wait_pid, timeout_seconds=wait_timeout)
        with runtime_lock(root, action="install_prepared", dry_run=dry_run):
            if not dry_run:
                write_update_status(
                    root,
                    tracker.status(
                        phase="verifying",
                        percent=20,
                        message="verifying package",
                    ),
                )
            _verify_package(package, manifest)
            resolved_entry_name = _resolve_entry_name(root, entry_name)
            if target_release_dir.exists() and not force:
                raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"release already exists: {target_release_dir}")
            if dry_run:
                _verify_zip_plan(package, resolved_entry_name)
                return RuntimeResult(
                    success=True,
                    action="install_prepared",
                    version=manifest.version,
                    previous_version=previous_version,
                    release_dir=target_release_dir,
                    message="dry-run ok",
                )
            write_update_status(
                root,
                tracker.status(
                    phase="extracting",
                    percent=45,
                    message="extracting package",
                ),
            )
            _extract_zip_safe(package, temp_release_dir)
            _ensure_release_entry(temp_release_dir, resolved_entry_name)
            if target_release_dir.exists():
                release_backup_dir = root / f".release-backup.{manifest.version}.{os.getpid()}.{uuid4().hex}.tmp"
                shutil.move(str(target_release_dir), str(release_backup_dir))
            releases_root.mkdir(parents=True, exist_ok=True)
            temp_release_dir.replace(target_release_dir)
            target_release_replaced = True
            sidecar_promotion = _promote_update_sidecars(extracted_root=target_release_dir, install_root=root)
            if switch_current:
                write_update_status(
                    root,
                    tracker.status(
                        phase="switching",
                        percent=75,
                        message="switching current release",
                    ),
                )
                _switch_installed_version(
                    install_root=root,
                    version=manifest.version,
                    entry_name=resolved_entry_name,
                    app_id=manifest.app_id,
                    platform=manifest.platform,
                    use_lock=False,
                )
                can_rollback_release = False
            _commit_sidecar_promotion(sidecar_promotion)
            sidecar_promotion = None
            can_rollback_release = False
            if release_backup_dir is not None and release_backup_dir.exists():
                shutil.rmtree(release_backup_dir)
                release_backup_dir = None
            result = RuntimeResult(
                success=True,
                action="install_prepared",
                version=manifest.version,
                previous_version=previous_version,
                release_dir=target_release_dir,
                message="installed",
            )
            if restart:
                write_update_status(
                    root,
                    tracker.status(
                        phase="restarting",
                        percent=90,
                        message="restarting current release",
                    ),
                )
                try:
                    process = launch_current(install_root=root)
                except OSError as exc:
                    raise UpdateError(UpdateErrorCode.UPDATER_LAUNCH_FAILED, f"restart failed: {exc}") from exc
                elapsed_ms, phase_durations_ms = tracker.finish()
                result = RuntimeResult(
                    success=True,
                    action="install_prepared",
                    version=manifest.version,
                    previous_version=previous_version,
                    release_dir=target_release_dir,
                    message="installed and restarted",
                    restarted_pid=process.pid,
                    elapsed_ms=elapsed_ms,
                    phase_durations_ms=phase_durations_ms,
                )
            else:
                elapsed_ms, phase_durations_ms = tracker.finish()
                result = RuntimeResult(
                    success=result.success,
                    action=result.action,
                    version=result.version,
                    previous_version=result.previous_version,
                    release_dir=result.release_dir,
                    message=result.message,
                    elapsed_ms=elapsed_ms,
                    phase_durations_ms=phase_durations_ms,
                )
            write_update_result(root, result)
            write_update_status(
                root,
                tracker.status(
                    phase="success",
                    percent=100,
                    message=result.message,
                ),
            )
            return result
    except Exception as exc:
        _rollback_sidecar_promotion(sidecar_promotion)
        if temp_release_dir.exists():
            shutil.rmtree(temp_release_dir)
        if can_rollback_release:
            if target_release_replaced and target_release_dir.exists():
                shutil.rmtree(target_release_dir)
            if release_backup_dir is not None and release_backup_dir.exists():
                target_release_dir.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(release_backup_dir), str(target_release_dir))
                release_backup_dir = None
        elif release_backup_dir is not None and release_backup_dir.exists():
            shutil.rmtree(release_backup_dir)
            release_backup_dir = None
        if isinstance(exc, UpdateError):
            if not dry_run and exc.code is not UpdateErrorCode.UPDATE_LOCKED:
                write_update_result(
                    root,
                    _failure_result("install_prepared", manifest.version, previous_version, target_release_dir, exc),
                )
                write_update_status(
                    root,
                    tracker.status(
                        phase="failed",
                        percent=100,
                        message=str(exc),
                        error=str(exc),
                    ),
                )
            raise
        wrapped = UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"install failed: {exc}")
        if not dry_run:
            write_update_result(
                root,
                _failure_result("install_prepared", manifest.version, previous_version, target_release_dir, wrapped),
            )
            write_update_status(
                root,
                tracker.status(
                    phase="failed",
                    percent=100,
                    message=str(wrapped),
                    error=str(wrapped),
                ),
            )
        raise wrapped from exc


def apply_pending_update(
    *,
    pending_path: Path,
    signature_key: Path | None = None,
    entry_name: str = "",
    force: bool = False,
    dry_run: bool = False,
    wait_pid: int | None = None,
    wait_timeout: float = 60.0,
    restart: bool = False,
) -> RuntimeResult:
    """读取 pending-update.json 并安装其中声明的包。

    :param pending_path: pending-update.json 路径。
    :param signature_key: 可选 manifest 验签公钥。
    :param entry_name: 显式 release 入口名。
    :param force: 目标 release 已存在时是否覆盖。
    :param dry_run: 是否只校验计划。
    :param wait_pid: 等待退出的旧进程 PID。
    :param wait_timeout: 等待旧进程退出超时时间。
    :param restart: 安装成功后是否重启当前入口。
    :return: runtime 结果。
    """
    payload = _read_json_object(Path(pending_path), "pending update")
    return apply_pending_payload(
        pending_payload=payload,
        signature_key=signature_key,
        entry_name=entry_name,
        force=force,
        dry_run=dry_run,
        wait_pid=wait_pid,
        wait_timeout=wait_timeout,
        restart=restart,
    )


def apply_pending_payload(
    *,
    pending_payload: dict[str, Any],
    signature_key: Path | None = None,
    entry_name: str = "",
    force: bool = False,
    dry_run: bool = False,
    wait_pid: int | None = None,
    wait_timeout: float = 60.0,
    restart: bool = False,
) -> RuntimeResult:
    """安装已读取并可验签的 pending payload。

    :param pending_payload: pending-update.json 已读取负载。
    :param signature_key: 可选 manifest 验签公钥。
    :param entry_name: 显式 release 入口名。
    :param force: 目标 release 已存在时是否覆盖。
    :param dry_run: 是否只校验计划。
    :param wait_pid: 等待退出的旧进程 PID。
    :param wait_timeout: 等待旧进程退出超时时间。
    :param restart: 安装成功后是否重启当前入口。
    :return: runtime 结果。
    """
    payload = pending_payload
    package_path = _require_path(payload, "package_path")
    install_root = _require_path(payload, "install_root")
    manifest_payload = payload.get("manifest")
    if not isinstance(manifest_payload, dict):
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "pending manifest must be an object")
    if signature_key is not None:
        verify_manifest_signature_with_key_file(manifest_payload, key_path=Path(signature_key))
    manifest = UpdateManifest.from_payload(manifest_payload)
    resolved_entry_name = _pending_entry_name(payload, entry_name)
    resolved_wait_pid = wait_pid
    if resolved_wait_pid is None:
        raw_old_pid = payload.get("old_pid")
        if raw_old_pid is not None:
            try:
                parsed_old_pid = int(raw_old_pid)
            except (TypeError, ValueError):
                parsed_old_pid = 0
            if parsed_old_pid > 0:
                resolved_wait_pid = parsed_old_pid
    return install_prepared_package(
        install_root=install_root,
        package_path=package_path,
        manifest=manifest,
        entry_name=resolved_entry_name,
        switch_current=True,
        force=force,
        dry_run=dry_run,
        wait_pid=resolved_wait_pid,
        wait_timeout=wait_timeout,
        restart=restart,
    )


def switch_installed_release(
    *,
    install_root: Path,
    version: str,
    entry_name: str = "",
    app_id: str = "",
    platform: str = "",
    wait_pid: int | None = None,
    wait_timeout: float = 60.0,
    restart: bool = False,
) -> RuntimeResult:
    """切换已安装版本并可等待旧进程退出后重启。

    :param install_root: 安装根目录。
    :param version: 目标版本。
    :param entry_name: release 入口名；为空时从 current.json 推断。
    :param app_id: 应用标识；为空时沿用 current.json。
    :param platform: 平台；为空时沿用 current.json。
    :param wait_pid: 切换前等待退出的旧进程 PID。
    :param wait_timeout: 等待旧进程退出超时时间。
    :param restart: 切换成功后启动当前版本。
    :return: runtime 结果。
    """
    root = normalize_install_root(Path(install_root))
    target_version = str(version or "").strip()
    previous_version = _current_version(root)
    target_release_dir = root / "releases" / target_version
    tracker = RuntimeStatusTracker(
        action="switch_installed",
        version=target_version,
        previous_version=previous_version,
    )
    try:
        if wait_pid is not None:
            write_update_status(
                root,
                tracker.status(
                    phase="waiting_old_process",
                    percent=10,
                    message=f"waiting for old process {wait_pid} to exit",
                ),
            )
            wait_for_process_exit(pid=wait_pid, timeout_seconds=wait_timeout)
        with runtime_lock(root, action="switch_installed", dry_run=False):
            write_update_status(
                root,
                tracker.status(
                    phase="switching",
                    percent=75,
                    message="switching current release",
                ),
            )
            switched = _switch_installed_version(
                install_root=root,
                version=target_version,
                entry_name=entry_name,
                app_id=app_id,
                platform=platform,
                use_lock=False,
            )
            restarted_pid = None
            message = "switched"
            if restart:
                write_update_status(
                    root,
                    tracker.status(
                        phase="restarting",
                        percent=90,
                        message="restarting current release",
                    ),
                )
                try:
                    process = launch_current(install_root=root)
                except OSError as exc:
                    raise UpdateError(UpdateErrorCode.UPDATER_LAUNCH_FAILED, f"restart failed: {exc}") from exc
                restarted_pid = process.pid
                message = "switched and restarted"
            elapsed_ms, phase_durations_ms = tracker.finish()
            result = RuntimeResult(
                success=True,
                action="switch_installed",
                version=switched.version,
                previous_version=previous_version,
                release_dir=switched.release_dir,
                message=message,
                restarted_pid=restarted_pid,
                elapsed_ms=elapsed_ms,
                phase_durations_ms=phase_durations_ms,
            )
            write_update_result(root, result)
            write_update_status(
                root,
                tracker.status(
                    phase="success",
                    percent=100,
                    message=result.message,
                ),
            )
            return result
    except Exception as exc:
        if isinstance(exc, UpdateError):
            if exc.code is not UpdateErrorCode.UPDATE_LOCKED:
                write_update_result(
                    root,
                    _failure_result("switch_installed", target_version, previous_version, target_release_dir, exc),
                )
                write_update_status(
                    root,
                    tracker.status(
                        phase="failed",
                        percent=100,
                        message=str(exc),
                        error=str(exc),
                    ),
                )
            raise
        wrapped = UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"switch failed: {exc}")
        write_update_result(
            root,
            _failure_result("switch_installed", target_version, previous_version, target_release_dir, wrapped),
        )
        write_update_status(
            root,
            tracker.status(
                phase="failed",
                percent=100,
                message=str(wrapped),
                error=str(wrapped),
            ),
        )
        raise wrapped from exc


def rollback_installation(
    *,
    install_root: Path,
    entry_name: str = "",
    wait_pid: int | None = None,
    wait_timeout: float = 60.0,
    restart: bool = False,
) -> RuntimeResult:
    """回滚到 current.json 中记录的 previous_version。

    :param install_root: 安装根目录。
    :param entry_name: release 入口名；为空时从 current.json 推断。
    :param wait_pid: 回滚前等待退出的旧进程 PID。
    :param wait_timeout: 等待旧进程退出超时时间。
    :param restart: 回滚成功后启动当前版本。
    :return: runtime 结果。
    """
    root = normalize_install_root(Path(install_root))
    current_version = ""
    previous_version = ""
    release_dir = root / "releases"
    tracker = RuntimeStatusTracker(action="rollback", version="", previous_version="")
    try:
        current_payload = _read_json_object(root / "current.json", "current.json")
        current_version = str(current_payload.get("version", "")).strip()
        previous_version = str(current_payload.get("previous_version", "")).strip()
        tracker = RuntimeStatusTracker(
            action="rollback",
            version=previous_version,
            previous_version=current_version,
        )
        if wait_pid is not None:
            write_update_status(
                root,
                tracker.status(
                    phase="waiting_old_process",
                    percent=10,
                    message=f"waiting for old process {wait_pid} to exit",
                ),
            )
            wait_for_process_exit(pid=wait_pid, timeout_seconds=wait_timeout)
        with runtime_lock(root, action="rollback", dry_run=False):
            if not previous_version:
                raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "current.json has no previous_version")
            write_update_status(
                root,
                tracker.status(
                    phase="switching",
                    percent=75,
                    message="rolling back current release",
                ),
            )
            switched = _switch_installed_version(
                install_root=root,
                version=previous_version,
                entry_name=entry_name,
                use_lock=False,
            )
            restarted_pid = None
            message = "rolled back"
            if restart:
                write_update_status(
                    root,
                    tracker.status(
                        phase="restarting",
                        percent=90,
                        message="restarting current release",
                    ),
                )
                try:
                    process = launch_current(install_root=root)
                except OSError as exc:
                    raise UpdateError(UpdateErrorCode.UPDATER_LAUNCH_FAILED, f"restart failed: {exc}") from exc
                restarted_pid = process.pid
                message = "rolled back and restarted"
            elapsed_ms, phase_durations_ms = tracker.finish()
            result = RuntimeResult(
                success=True,
                action="rollback",
                version=switched.version,
                previous_version=current_version,
                release_dir=switched.release_dir,
                message=message,
                restarted_pid=restarted_pid,
                elapsed_ms=elapsed_ms,
                phase_durations_ms=phase_durations_ms,
            )
            write_update_result(root, result)
            write_update_status(
                root,
                tracker.status(
                    phase="success",
                    percent=100,
                    message=result.message,
                ),
            )
            return result
    except UpdateError as exc:
        if previous_version:
            release_dir = root / "releases" / previous_version
        if exc.code is not UpdateErrorCode.UPDATE_LOCKED:
            write_update_result(root, _failure_result("rollback", previous_version, current_version, release_dir, exc))
            write_update_status(
                root,
                tracker.status(
                    phase="failed",
                    percent=100,
                    message=str(exc),
                    error=str(exc),
                ),
            )
        raise


def launch_current(*, install_root: Path) -> subprocess.Popen[bytes]:
    """启动 current.json 指向的当前版本入口。"""
    root = normalize_install_root(Path(install_root))
    current_payload = _read_json_object(root / "current.json", "current.json")
    release_dir = str(current_payload.get("release_dir", "")).strip()
    executable = str(current_payload.get("executable", "")).strip()
    if not executable:
        entry_payload = current_payload.get("entry")
        if isinstance(entry_payload, dict):
            path_payload = entry_payload.get("path")
            if isinstance(path_payload, str):
                executable = path_payload.strip()
    if not release_dir or not executable:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "current.json must contain release_dir and executable")
    entry_path = root / release_dir / executable
    if not entry_path.exists():
        raise UpdateError(UpdateErrorCode.UPDATER_NOT_FOUND, f"current entry not found: {entry_path}")
    if _is_macos_app_bundle(entry_path) and sys.platform == "darwin":
        return subprocess.Popen([_macos_open_executable(), "-n", str(entry_path)], cwd=str(entry_path.parent), close_fds=True)
    return subprocess.Popen([str(entry_path)], cwd=str(entry_path.parent))


def wait_for_process_exit(*, pid: int, timeout_seconds: float = 60.0, poll_interval: float = 0.25) -> None:
    """等待指定进程退出；超时则抛出结构化错误。"""
    if pid <= 0:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"pid must be positive: {pid}")
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    while _is_process_alive(pid):
        if time.monotonic() >= deadline:
            raise UpdateError(
                UpdateErrorCode.PROCESS_TIMEOUT,
                f"process {pid} did not exit within {timeout_seconds:g}s; please close the application and retry",
            )
        time.sleep(max(0.01, float(poll_interval)))


def _macos_open_executable() -> str:
    """返回 macOS open 命令路径。"""
    path = Path("/usr/bin/open")
    return str(path) if path.is_file() else "open"


def _is_process_alive(pid: int) -> bool:
    """跨平台检查进程是否仍存在。"""
    if sys.platform == "win32":
        return _is_windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _is_windows_process_alive(pid: int) -> bool:
    """用 Win32 API 检查进程是否仍处于 STILL_ACTIVE。"""
    import ctypes
    from ctypes import wintypes

    process_query_limited_information = 0x1000
    still_active = 259
    error_invalid_parameter = 87
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    handle = kernel32.OpenProcess(process_query_limited_information, False, int(pid))
    if not handle:
        error_code = ctypes.get_last_error()
        if error_code == error_invalid_parameter:
            return False
        return True
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return True
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def write_update_result(install_root: Path, result: RuntimeResult) -> None:
    """写入 update-result.json。"""
    path = Path(install_root) / "update-result.json"
    _write_json_atomic(path, result.to_payload())


def write_update_status(install_root: Path, status: RuntimeStatus) -> None:
    """写入 update-status.json。"""
    path = Path(install_root) / "update-status.json"
    _write_json_atomic(path, status.to_payload())


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    """原子写入 JSON，避免 GUI 轮询时读到半截状态文件。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.parent / f".{path.name}.{os.getpid()}.{uuid4().hex}.tmp"
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def _utc_now_iso() -> str:
    """返回 UTC ISO 时间戳。"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _failure_result(
    action: str,
    version: str,
    previous_version: str,
    release_dir: Path,
    exc: UpdateError,
) -> RuntimeResult:
    """构造失败 update-result.json 结果。"""
    return RuntimeResult(
        success=False,
        action=action,
        version=version,
        previous_version=previous_version,
        release_dir=release_dir,
        message=str(exc),
    )


def _pending_entry_name(payload: dict[str, object], explicit_entry_name: str) -> str:
    """解析 pending manifest 中的 release 入口名。"""
    explicit = str(explicit_entry_name or "").strip()
    if explicit:
        return explicit
    for key in ("entry_name", "restart_executable"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _verify_package(package_path: Path, manifest: UpdateManifest) -> None:
    """按 manifest 校验本地包大小和 SHA-256。"""
    if not package_path.is_file():
        raise UpdateError(UpdateErrorCode.PACKAGE_NOT_FOUND, f"package not found: {package_path}")
    actual_size = package_path.stat().st_size
    if actual_size != manifest.package.size:
        raise UpdateError(
            UpdateErrorCode.PACKAGE_SIZE_MISMATCH,
            f"package.size {manifest.package.size} != actual {actual_size}",
        )
    digest = hashlib.sha256()
    with package_path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    actual_sha256 = digest.hexdigest()
    if actual_sha256 != manifest.package.sha256.lower():
        raise UpdateError(
            UpdateErrorCode.PACKAGE_HASH_MISMATCH,
            f"package.sha256 {manifest.package.sha256.lower()} != actual {actual_sha256}",
        )


def _extract_zip_safe(package_path: Path, target_dir: Path) -> None:
    """安全解压 zip，拒绝绝对路径和目录穿越。"""
    target_dir.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(package_path) as archive:
            for member in archive.infolist():
                parts = _safe_zip_member_parts(member.filename)
                extracted_path = target_dir.joinpath(*parts)
                mode = member.external_attr >> 16
                if member.is_dir():
                    extracted_path.mkdir(parents=True, exist_ok=True)
                    if mode:
                        extracted_path.chmod(mode)
                    continue
                if stat.S_ISLNK(mode):
                    _extract_zip_symlink(archive, member, extracted_path, target_dir)
                    continue
                extracted_path.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as source_file, extracted_path.open("wb") as target_file:
                    shutil.copyfileobj(source_file, target_file)
                if mode:
                    extracted_path.chmod(mode)
    except zipfile.BadZipFile as exc:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"package is not a valid zip: {package_path}") from exc


def _extract_zip_symlink(archive: zipfile.ZipFile, member: zipfile.ZipInfo, extracted_path: Path, target_dir: Path) -> None:
    """恢复 zip 中的 POSIX symlink，并拒绝指向解压根外部的链接。

    :param archive: 待解压的 zip 包。
    :param member: symlink 成员信息。
    :param extracted_path: symlink 目标写入路径。
    :param target_dir: 当前解压根目录。
    :return: None
    """
    link_target = archive.read(member).decode("utf-8")
    if not link_target:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"empty symlink target: {member.filename}")
    link_target_path = Path(link_target)
    if link_target_path.is_absolute():
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"unsafe symlink target: {member.filename}")
    resolved_target = (extracted_path.parent / link_target_path).resolve()
    if not resolved_target.is_relative_to(target_dir.resolve()):
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"unsafe symlink target: {member.filename}")
    extracted_path.parent.mkdir(parents=True, exist_ok=True)
    extracted_path.symlink_to(link_target)


def _promote_update_sidecars(*, extracted_root: Path, install_root: Path) -> SidecarPromotion | None:
    """把 update 包中的 launcher/updater sidecar 提升到安装根。

    :param extracted_root: 已安装 release 目录。
    :param install_root: 安装根目录。
    :return: sidecar 提升事务；无 sidecar 时返回 None。
    """
    backup_root = install_root / f".sidecar-backup.{os.getpid()}.{uuid4().hex}.tmp"
    promotion = SidecarPromotion(backup_root=backup_root, backups=[], touched_targets=[])
    try:
        changed = False
        launcher_sidecar = extracted_root / "_launcher"
        if launcher_sidecar.is_dir():
            for item in launcher_sidecar.iterdir():
                _copy_sidecar_item(item, install_root / item.name, promotion, backup_name=Path("launcher") / item.name)
                changed = True
            shutil.rmtree(launcher_sidecar)

        updater_sidecar = extracted_root / "updater"
        if updater_sidecar.is_dir():
            _copy_sidecar_item(updater_sidecar, install_root / "updater", promotion, backup_name=Path("updater"))
            changed = True
            shutil.rmtree(updater_sidecar)

        if not changed:
            _remove_empty_backup_root(backup_root)
            return None
        return promotion
    except Exception:
        _rollback_sidecar_promotion(promotion)
        raise


def _copy_sidecar_item(source: Path, target: Path, promotion: SidecarPromotion, *, backup_name: Path) -> None:
    """复制一个 sidecar 项并备份被覆盖目标。

    :param source: sidecar 来源路径。
    :param target: 安装根目标路径。
    :param promotion: sidecar 提升事务。
    :param backup_name: 备份相对路径。
    :return: None
    """
    if target.exists():
        backup_path = promotion.backup_root / backup_name
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(target), str(backup_path))
        promotion.backups.append((target, backup_path))
    if source.is_dir() and not source.is_symlink():
        shutil.copytree(source, target, symlinks=True)
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target, follow_symlinks=False)
    promotion.touched_targets.append(target)


def _commit_sidecar_promotion(promotion: SidecarPromotion | None) -> None:
    """提交 sidecar 提升并删除备份。

    :param promotion: sidecar 提升事务。
    :return: None
    """
    if promotion is None:
        return
    if promotion.backup_root.exists():
        shutil.rmtree(promotion.backup_root)


def _rollback_sidecar_promotion(promotion: SidecarPromotion | None) -> None:
    """回滚 sidecar 提升。

    :param promotion: sidecar 提升事务。
    :return: None
    """
    if promotion is None:
        return
    for target in reversed(promotion.touched_targets):
        if target.exists():
            if target.is_dir() and not target.is_symlink():
                shutil.rmtree(target)
            else:
                target.unlink()
    for target, backup in reversed(promotion.backups):
        if backup.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(backup), str(target))
    _remove_empty_backup_root(promotion.backup_root)


def _remove_empty_backup_root(backup_root: Path) -> None:
    """删除 sidecar 备份根目录。

    :param backup_root: 备份根目录。
    :return: None
    """
    if backup_root.exists():
        shutil.rmtree(backup_root)


def _verify_zip_plan(package_path: Path, entry_name: str) -> None:
    """校验 zip 可安全解压且包含 release 入口。"""
    try:
        with zipfile.ZipFile(package_path) as archive:
            member_names = {_normalized_zip_member_name(member.filename) for member in archive.infolist()}
            for member in archive.infolist():
                _safe_zip_member_parts(member.filename)
    except zipfile.BadZipFile as exc:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"package is not a valid zip: {package_path}") from exc
    normalized_entry = _normalized_zip_member_name(entry_name)
    if normalized_entry in member_names:
        return
    if normalized_entry.endswith(".app"):
        macos_prefix = f"{normalized_entry}/Contents/MacOS/"
        if any(name.startswith(macos_prefix) and name != macos_prefix for name in member_names):
            return
    raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"release entry not found in package: {entry_name}")


def _safe_zip_member_parts(member_name: str) -> tuple[str, ...]:
    """返回安全 zip 成员路径，拒绝绝对路径、盘符和目录穿越。"""
    normalized_name = str(member_name or "").replace("\\", "/")
    pure_path = PurePosixPath(normalized_name)
    if pure_path.is_absolute():
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"unsafe zip member: {member_name}")
    parts = tuple(part for part in pure_path.parts if part)
    if not parts:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"unsafe zip member: {member_name}")
    if ".." in parts or any(part == "" for part in parts):
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"unsafe zip member: {member_name}")
    if ":" in parts[0]:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"unsafe zip member: {member_name}")
    return parts


def _normalized_zip_member_name(member_name: str) -> str:
    """把 zip 成员名规范为 POSIX 路径。"""
    return str(member_name or "").replace("\\", "/").strip("/")


def _ensure_release_entry(release_dir: Path, entry_name: str) -> None:
    """校验 release 入口存在。"""
    entry_path = release_dir / entry_name
    if entry_path.is_file():
        return
    if entry_path.is_dir() and entry_path.suffix == ".app":
        macos_dir = entry_path / "Contents" / "MacOS"
        if macos_dir.is_dir() and any(candidate.is_file() for candidate in macos_dir.iterdir()):
            return
    raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"release entry not found in package: {entry_path}")


def _resolve_entry_name(install_root: Path, entry_name: str) -> str:
    """解析 release 入口名。"""
    explicit = str(entry_name or "").strip()
    if explicit:
        return explicit
    root = normalize_install_root(Path(install_root))
    payload = _read_json_object(root / "current.json", "current.json")
    entry_payload = payload.get("entry")
    if isinstance(entry_payload, dict):
        entry_path = entry_payload.get("path")
        if isinstance(entry_path, str) and entry_path.strip():
            return entry_path.strip()
    executable = payload.get("executable")
    if isinstance(executable, str) and executable.strip():
        return executable.strip()
    raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "entry_name is required when current.json has no entry")


def _current_version(install_root: Path) -> str:
    """读取当前版本；不存在时返回空字符串。"""
    root = normalize_install_root(Path(install_root))
    current_path = root / "current.json"
    if not current_path.is_file():
        return ""
    payload = _read_json_object(current_path, "current.json")
    return str(payload.get("version", "")).strip()


def _read_json_object(path: Path, context: str) -> dict[str, Any]:
    """读取 JSON 对象。"""
    if not path.is_file():
        if context == "current.json":
            raise UpdateError(UpdateErrorCode.MANIFEST_NOT_FOUND, missing_current_json_message(path.parent))
        raise UpdateError(UpdateErrorCode.MANIFEST_NOT_FOUND, f"{context} not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"{context} is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"{context} must be a JSON object")
    return payload


def _is_macos_app_bundle(path: Path) -> bool:
    """判断路径是否为 macOS 应用包。"""
    return (
        path.is_dir()
        and path.suffix == ".app"
        and (path / "Contents" / "MacOS").is_dir()
        and any(candidate.is_file() for candidate in (path / "Contents" / "MacOS").iterdir())
    )


def _require_path(payload: dict[str, Any], key: str) -> Path:
    """从 JSON 对象读取非空路径。"""
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"{key} must be a non-empty string")
    return Path(value)
