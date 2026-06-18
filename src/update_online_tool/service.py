"""在线升级服务门面。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from update_online_tool.downloader import CancellationToken, PreparedPackage, copy_package_with_verification
from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.manifest import UpdateManifest
from update_online_tool.nas import NasReleaseSource
from update_online_tool.settings import UpdateToolSettings
from update_online_tool.versioning import UpdateDecision, decide_update

ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True)
class CheckUpdateResult:
    """检查更新结果。

    :param decision: 升级决策。
    :param manifest: 远端 manifest。
    :param package_size: 包大小。
    :param notes: 发布说明。
    :return: None
    """

    decision: UpdateDecision
    manifest: UpdateManifest
    package_size: int
    notes: str


class UpdateService:
    """在线升级服务门面。

    :param settings: 在线升级工具设置。
    :return: None
    """

    def __init__(self, settings: UpdateToolSettings) -> None:
        """保存设置。

        :param settings: 在线升级工具设置。
        :return: None
        """
        self.settings = settings
        self.source = NasReleaseSource(settings.nas_root)

    @classmethod
    def from_settings(cls, path: Path | None = None) -> "UpdateService":
        """从 settings.json 构建服务。

        :param path: 显式设置文件路径。
        :return: 在线升级服务。
        """
        return cls(UpdateToolSettings.load(path))

    def check(
        self,
        *,
        app_id: str,
        current_version: str,
        channel: str = "",
        platform: str = "",
        skipped_version: str | None = None,
    ) -> CheckUpdateResult:
        """检查是否存在可用升级。

        :param app_id: 应用标识。
        :param current_version: 当前版本。
        :param channel: 发布通道。
        :param platform: 可选平台；为空时使用旧版通道路径。
        :param skipped_version: 用户已跳过版本。
        :return: 检查更新结果。
        """
        self.source.ensure_available()
        resolved_channel = channel or self.settings.default_channel
        manifest_path = self.source.manifest_path(app_id, resolved_channel, platform)
        if not manifest_path.is_file():
            raise UpdateError(UpdateErrorCode.MANIFEST_NOT_FOUND, f"manifest not found: {manifest_path}")
        manifest = self._load_manifest(manifest_path)
        if manifest.app_id != app_id:
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"manifest app_id mismatch: {manifest.app_id}")
        if manifest.channel != resolved_channel:
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"manifest channel mismatch: {manifest.channel}")
        if platform and manifest.platform and manifest.platform != platform:
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"manifest platform mismatch: {manifest.platform}")
        decision = decide_update(
            current_version=current_version,
            latest_version=manifest.version,
            mandatory=manifest.mandatory,
            min_supported_version=manifest.min_supported_version,
            skipped_version=skipped_version,
        )
        return CheckUpdateResult(
            decision=decision,
            manifest=manifest,
            package_size=manifest.package.size,
            notes=manifest.notes,
        )

    def prepare(
        self,
        manifest: UpdateManifest,
        download_dir: Path,
        *,
        progress: ProgressCallback | None = None,
        cancellation_token: CancellationToken | None = None,
    ) -> PreparedPackage:
        """复制并校验升级包。

        :param manifest: 远端 manifest。
        :param download_dir: 本地下载目录。
        :param progress: 进度回调。
        :param cancellation_token: 取消令牌。
        :return: 已准备升级包。
        """
        self.source.ensure_available()
        source_package_path = self.source.resolve_package_path(manifest.package.url)
        target_package_path = Path(download_dir) / Path(manifest.package.url).name
        return copy_package_with_verification(
            source_path=source_package_path,
            target_path=target_package_path,
            expected_size=manifest.package.size,
            expected_sha256=manifest.package.sha256,
            progress=progress,
            cancellation_token=cancellation_token,
        )

    def launch(
        self,
        *,
        package_path: Path,
        manifest: UpdateManifest,
        install_root: Path,
        old_pid: int,
        restart_executable: str,
    ) -> object:
        """启动独立 updater。

        :param package_path: 已准备升级包。
        :param manifest: 远端 manifest。
        :param install_root: 安装根目录。
        :param old_pid: 旧 GUI 进程号。
        :param restart_executable: 重启入口。
        :return: 启动结果。
        """
        from update_online_tool.launcher import StandaloneUpdaterLauncher

        pending_payload: dict[str, object] = {
            "package_path": str(package_path),
            "manifest": manifest.to_payload(),
            "install_root": str(install_root),
            "old_pid": old_pid,
            "restart_executable": restart_executable,
        }
        updater_executable = Path(install_root) / self.settings.updater_executable_name
        return StandaloneUpdaterLauncher(updater_executable).launch(
            pending_payload=pending_payload,
            pending_manifest_path=Path(install_root) / "pending-update.json",
        )

    def _load_manifest(self, path: Path) -> UpdateManifest:
        """读取 manifest 文件。

        :param path: manifest 路径。
        :return: manifest 模型。
        """
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"manifest is not valid JSON: {path}") from exc
        if not isinstance(payload, dict):
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "manifest must be a JSON object")
        return UpdateManifest.from_payload(payload)
