"""桌面应用在线升级高层客户端。"""

from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from update_online_tool.downloader import CancellationToken, PreparedPackage
from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.install_root import missing_current_json_message, missing_updater_message, normalize_install_root
from update_online_tool.installed import InstalledVersion, list_installed_versions
from update_online_tool.launcher import LaunchResult, PopenFactory, StandaloneUpdaterLauncher
from update_online_tool.manifest import UpdateManifest
from update_online_tool.service import CheckUpdateResult, ProgressCallback, RemoteVersion, UpdateService
from update_online_tool.settings import UpdateToolSettings
from update_online_tool.signature import verify_manifest_signature_with_key_file


@dataclass(frozen=True)
class DesktopUpdateConfig:
    """桌面应用在线升级配置。

    :param app_id: 应用标识。
    :param install_root: 标准安装根目录。
    :param settings_path: 可选 settings.json 路径。
    :param platform: 平台标识。
    :param channel: 发布通道；为空时使用 settings 默认通道。
    :param download_dir: 准备包下载目录；为空时使用安装根 updates 目录。
    :param signature_key: 可选 manifest 验签公钥。
    :param wait_timeout: 等待旧 GUI 退出超时时间。
    :return: None
    """

    app_id: str
    install_root: Path
    settings_path: Path | None = None
    platform: str = ""
    channel: str = ""
    download_dir: Path | None = None
    signature_key: Path | None = None
    wait_timeout: float = 60.0


class DesktopUpdateClient:
    """桌面应用在线升级高层客户端。

    :param config: 桌面升级配置。
    :param settings: 在线升级设置。
    :param popen: 可注入进程启动函数。
    :return: None
    """

    def __init__(
        self,
        config: DesktopUpdateConfig,
        *,
        settings: UpdateToolSettings | None = None,
        popen: PopenFactory | None = None,
    ) -> None:
        """保存客户端配置。

        :param config: 桌面升级配置。
        :param settings: 可注入 settings；为空时按 config.settings_path 加载。
        :param popen: 可注入进程启动函数。
        :return: None
        """
        self.config = config
        self.settings = settings or UpdateToolSettings.load(config.settings_path, app_id=config.app_id)
        self.service = UpdateService(self.settings)
        self._popen = popen or subprocess.Popen

    @classmethod
    def from_config(
        cls,
        config: DesktopUpdateConfig,
        *,
        popen: PopenFactory | None = None,
    ) -> "DesktopUpdateClient":
        """从配置构建客户端。

        :param config: 桌面升级配置。
        :param popen: 可注入进程启动函数。
        :return: 桌面升级客户端。
        """
        return cls(config, popen=popen)

    def check(self, *, skipped_version: str | None = None) -> CheckUpdateResult:
        """检查当前安装根是否有可用更新。

        :param skipped_version: 用户已跳过版本。
        :return: 检查结果。
        """
        result = self.service.check(
            app_id=self.config.app_id,
            current_version=self.current_version(),
            channel=self._channel(),
            platform=self.config.platform,
            skipped_version=skipped_version,
        )
        self._verify_manifest_if_needed(result.manifest)
        return result

    def list_remote_versions(self, *, include_hidden: bool = False) -> list[RemoteVersion]:
        """列出远端历史版本。

        :param include_hidden: 是否包含隐藏版本。
        :return: 远端版本列表。
        """
        versions = self.service.list_remote_versions(
            app_id=self.config.app_id,
            channel=self._channel(),
            platform=self.config.platform,
            include_hidden=include_hidden,
        )
        for version in versions:
            self._verify_manifest_if_needed(version.manifest)
        return versions

    def install_remote_version(
        self,
        version: str,
        *,
        old_pid: int | None = None,
        restart: bool = True,
        force: bool = False,
        progress: ProgressCallback | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> LaunchResult:
        """准备并启动标准 updater 安装远端版本。

        :param version: 目标远端版本。
        :param old_pid: 旧 GUI 进程号。
        :param restart: 安装后是否重启。
        :param force: 目标 release 已存在时是否覆盖。
        :param progress: 下载进度回调。
        :param cancellation_token: 下载取消令牌。
        :return: updater 启动结果。
        """
        manifest, _ = self.service.get_remote_manifest_with_path(
            app_id=self.config.app_id,
            version=version,
            channel=self._channel(),
            platform=self.config.platform,
        )
        self._verify_manifest_if_needed(manifest)
        prepared = self.service.prepare(
            manifest,
            self._download_dir(),
            progress=progress,
            cancellation_token=cancellation_token,
        )
        pending_payload = self._pending_payload(
            prepared=prepared,
            manifest=manifest,
            old_pid=old_pid,
            restart=restart,
            force=force,
        )
        return StandaloneUpdaterLauncher(self._updater_executable(), popen=self._popen).launch(
            pending_payload=pending_payload,
            pending_manifest_path=self.pending_path(),
        )

    def switch_installed_version(
        self,
        version: str,
        *,
        old_pid: int | None = None,
        restart: bool = True,
    ) -> LaunchResult:
        """启动标准 updater 切换本地已安装版本。

        :param version: 目标本地版本。
        :param old_pid: 旧 GUI 进程号。
        :param restart: 切换后是否重启。
        :return: updater 启动结果。
        """
        command = [
            str(self._updater_executable()),
            "switch-installed",
            "--install-root",
            str(self.install_root()),
            "--version",
            str(version),
        ]
        if old_pid is not None:
            command.extend(["--wait-pid", str(int(old_pid)), "--wait-timeout", str(float(self.config.wait_timeout))])
        if restart:
            command.append("--restart")
        try:
            process = self._popen(command, cwd=str(self._updater_executable().parent), close_fds=True)
        except OSError as exc:
            raise UpdateError(UpdateErrorCode.UPDATER_LAUNCH_FAILED, f"updater launch failed: {self._updater_executable()}") from exc
        return LaunchResult(started=True, updater_pid=getattr(process, "pid", None), pending_manifest_path=None)

    def rollback(self, *, old_pid: int | None = None, restart: bool = True) -> LaunchResult:
        """启动标准 updater 回滚到 previous_version。

        :param old_pid: 旧 GUI 进程号。
        :param restart: 回滚后是否重启。
        :return: updater 启动结果。
        """
        command = [
            str(self._updater_executable()),
            "rollback",
            "--install-root",
            str(self.install_root()),
        ]
        if old_pid is not None:
            command.extend(["--wait-pid", str(int(old_pid)), "--wait-timeout", str(float(self.config.wait_timeout))])
        if restart:
            command.append("--restart")
        try:
            process = self._popen(command, cwd=str(self._updater_executable().parent), close_fds=True)
        except OSError as exc:
            raise UpdateError(UpdateErrorCode.UPDATER_LAUNCH_FAILED, f"updater launch failed: {self._updater_executable()}") from exc
        return LaunchResult(started=True, updater_pid=getattr(process, "pid", None), pending_manifest_path=None)

    def prewarm_updater(self, *, timeout: float = 20.0) -> float:
        """后台预热标准 updater，降低首次版本切换的可见等待。

        :param timeout: 预热命令最大等待秒数。
        :return: 预热耗时秒数。
        """
        updater = self._updater_executable()
        if not updater.is_file():
            raise UpdateError(
                UpdateErrorCode.UPDATER_NOT_FOUND,
                missing_updater_message(Path(self.config.install_root), updater),
            )
        command = [str(updater), "--help"]
        kwargs: dict[str, object] = {
            "cwd": str(updater.parent),
            "close_fds": True,
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        started_at = time.perf_counter()
        try:
            process = self._popen(command, **kwargs)
        except TypeError:
            process = self._popen(command, cwd=str(updater.parent), close_fds=True)
        except OSError as exc:
            raise UpdateError(UpdateErrorCode.UPDATER_LAUNCH_FAILED, f"updater launch failed: {updater}") from exc
        try:
            if hasattr(process, "communicate"):
                process.communicate(timeout=float(timeout))
            elif hasattr(process, "wait"):
                process.wait(timeout=float(timeout))
        except subprocess.TimeoutExpired as exc:
            if hasattr(process, "kill"):
                process.kill()
            raise UpdateError(UpdateErrorCode.UPDATER_LAUNCH_FAILED, f"updater prewarm timeout: {updater}") from exc
        return time.perf_counter() - started_at

    def read_status(self) -> dict[str, Any]:
        """读取 update-status.json。

        :return: 状态字典；不存在时返回 exists=False。
        """
        return self._read_json_state("update-status.json")

    def read_result(self) -> dict[str, Any]:
        """读取 update-result.json。

        :return: 结果字典；不存在时返回 exists=False。
        """
        return self._read_json_state("update-result.json")

    def list_installed_versions(self) -> list[InstalledVersion]:
        """列出本地已安装版本。

        :return: 已安装版本列表。
        """
        return list_installed_versions(install_root=self.install_root())

    def current_version(self) -> str:
        """读取 current.json 当前版本。

        :return: 当前版本号。
        """
        return str(self._current_payload().get("version", "")).strip()

    def install_root(self) -> Path:
        """返回安装根目录。

        :return: 安装根路径。
        """
        return normalize_install_root(Path(self.config.install_root))

    def pending_path(self) -> Path:
        """返回标准 pending-update.json 路径。

        :return: pending 文件路径。
        """
        return self.install_root() / "pending-update.json"

    def _channel(self) -> str:
        """解析发布通道。

        :return: 发布通道名称。
        """
        return self.config.channel or self.settings.default_channel

    def _download_dir(self) -> Path:
        """解析准备包下载目录。

        :return: 准备包下载目录。
        """
        return Path(self.config.download_dir) if self.config.download_dir is not None else self.install_root() / "updates"

    def _updater_executable(self) -> Path:
        """解析标准 updater 可执行文件路径。

        :return: updater 可执行文件路径。
        """
        configured = Path(self.settings.updater_executable_name)
        candidates = [
            self.install_root() / "updater" / configured.name,
            self.install_root() / "updater" / configured.stem / configured.name,
        ]
        if configured.suffix:
            candidates.append(self.install_root() / "updater" / configured.stem / configured.stem)
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        return candidates[0]

    def _current_payload(self) -> dict[str, Any]:
        """读取 current.json。

        :return: current.json 负载。
        """
        path = self.install_root() / "current.json"
        if not path.is_file():
            raise UpdateError(UpdateErrorCode.MANIFEST_NOT_FOUND, missing_current_json_message(self.install_root()))
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"current.json is not valid JSON: {path}") from exc
        if not isinstance(payload, dict):
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "current.json must be a JSON object")
        return payload

    def _current_entry_name(self) -> str:
        """读取 current.json 中的当前入口名。

        :return: 当前 release 入口名。
        """
        payload = self._current_payload()
        entry = payload.get("entry")
        if isinstance(entry, dict):
            path = entry.get("path")
            if isinstance(path, str) and path.strip():
                return path.strip()
        executable = payload.get("executable")
        if isinstance(executable, str) and executable.strip():
            return executable.strip()
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "current.json has no entry path")

    def _pending_payload(
        self,
        *,
        prepared: PreparedPackage,
        manifest: UpdateManifest,
        old_pid: int | None,
        restart: bool,
        force: bool,
    ) -> dict[str, object]:
        """构建标准 pending-update.json 负载。

        :param prepared: 已准备升级包。
        :param manifest: 目标版本 manifest。
        :param old_pid: 旧 GUI 进程号。
        :param restart: 安装后是否重启。
        :param force: 目标 release 已存在时是否覆盖。
        :return: pending 负载。
        """
        payload: dict[str, object] = {
            "install_root": str(self.install_root()),
            "package_path": str(prepared.package_path),
            "manifest": manifest.to_payload(),
            "restart_executable": self._current_entry_name(),
            "restart": bool(restart),
            "force": bool(force),
        }
        if old_pid is not None:
            payload["old_pid"] = int(old_pid)
            payload["wait_timeout"] = float(self.config.wait_timeout)
        if self.config.signature_key is not None:
            payload["signature_key"] = str(Path(self.config.signature_key))
        return payload

    def _verify_manifest_if_needed(self, manifest: UpdateManifest) -> None:
        """按配置校验 manifest 签名。

        :param manifest: 待校验 manifest。
        :return: None。
        """
        if self.config.signature_key is None:
            return
        verify_manifest_signature_with_key_file(manifest.to_payload(), key_path=Path(self.config.signature_key))

    def _read_json_state(self, filename: str) -> dict[str, Any]:
        """读取安装根状态 JSON。

        :param filename: 安装根下的状态文件名。
        :return: 状态读取结果。
        """
        path = self.install_root() / filename
        if not path.is_file():
            return {"exists": False}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return {"exists": True, "error": str(exc)}
        if not isinstance(payload, dict):
            return {"exists": True, "error": "JSON root is not an object"}
        return {"exists": True, "payload": payload}
