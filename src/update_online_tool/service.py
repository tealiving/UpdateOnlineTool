"""在线升级服务门面。"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from update_online_tool.downloader import CancellationToken, PreparedPackage, copy_package_with_verification
from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.manifest import UpdateManifest
from update_online_tool.nas import NasReleaseSource
from update_online_tool.settings import UpdateToolSettings
from update_online_tool.versioning import UpdateDecision, decide_update, parse_version_tuple

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


@dataclass(frozen=True)
class RemoteVersion:
    """远端可用版本。

    :param version: 版本号。
    :param channel: 发布通道。
    :param platform: 平台标识。
    :param notes: 发布说明。
    :param manifest: 版本 manifest。
    :param manifest_path: manifest 文件路径。
    :param package_exists: manifest 指向的包是否存在。
    :return: None
    """

    version: str
    channel: str
    platform: str
    notes: str
    manifest: UpdateManifest
    manifest_path: Path
    package_exists: bool


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
        self.source = NasReleaseSource(settings.selected_nas_root())

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
        if manifest.hidden:
            decision = UpdateDecision.NOT_AVAILABLE
        else:
            decision = decide_update(
                current_version=current_version,
                latest_version=manifest.version,
                mandatory=manifest.mandatory,
                min_supported_version=manifest.min_supported_version,
                skipped_version=skipped_version,
            )
            if (
                decision is UpdateDecision.NOT_AVAILABLE
                and manifest.allow_downgrade
                and parse_version_tuple(current_version) > parse_version_tuple(manifest.version)
            ):
                decision = UpdateDecision.OPTIONAL_UPDATE
        return CheckUpdateResult(
            decision=decision,
            manifest=manifest,
            package_size=manifest.package.size,
            notes=manifest.notes,
        )

    def list_remote_versions(
        self,
        *,
        app_id: str,
        channel: str = "",
        platform: str = "",
        include_hidden: bool = False,
    ) -> list[RemoteVersion]:
        """列出 NAS 上已发布的历史版本。

        :param app_id: 应用标识。
        :param channel: 发布通道；为空时使用 settings 默认值。
        :param platform: 可选平台；为空时使用旧版版本路径。
        :param include_hidden: 是否包含 hidden 版本。
        :return: 按版本倒序排列的远端版本。
        """
        self.source.ensure_available()
        resolved_channel = channel or self.settings.default_channel
        versions: list[RemoteVersion] = []
        for manifest_path in self._remote_version_manifest_paths(app_id, resolved_channel, platform):
            manifest = self._load_manifest(manifest_path)
            if manifest.app_id != app_id:
                raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"manifest app_id mismatch: {manifest.app_id}")
            if manifest.channel != resolved_channel:
                continue
            if platform and manifest.platform and manifest.platform != platform:
                continue
            if manifest.hidden and not include_hidden:
                continue
            package_path = self.source.resolve_package_path(manifest.package.url)
            versions.append(
                RemoteVersion(
                    version=manifest.version,
                    channel=manifest.channel,
                    platform=manifest.platform,
                    notes=manifest.notes,
                    manifest=manifest,
                    manifest_path=manifest_path,
                    package_exists=package_path.is_file(),
                )
            )
        return sorted(versions, key=lambda item: parse_version_tuple(item.version), reverse=True)

    def get_remote_manifest(self, *, app_id: str, version: str, channel: str = "", platform: str = "") -> UpdateManifest:
        """读取指定版本 manifest。

        :param app_id: 应用标识。
        :param version: 目标版本。
        :param channel: 发布通道；为空时使用 settings 默认值。
        :param platform: 可选平台；为空时使用旧版版本路径。
        :return: 版本 manifest。
        """
        self.source.ensure_available()
        resolved_channel = channel or self.settings.default_channel
        manifest_path = self.source.version_manifest_path(app_id, version, platform, resolved_channel)
        if not manifest_path.is_file():
            legacy_manifest_path = self.source.version_manifest_path(app_id, version, platform)
            if legacy_manifest_path.is_file():
                manifest_path = legacy_manifest_path
        if not manifest_path.is_file():
            raise UpdateError(UpdateErrorCode.MANIFEST_NOT_FOUND, f"manifest not found: {manifest_path}")
        manifest = self._load_manifest(manifest_path)
        self._validate_manifest_identity(
            manifest,
            app_id=app_id,
            channel=resolved_channel,
            platform=platform,
        )
        if manifest.version != version:
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"manifest version mismatch: {manifest.version}")
        return manifest

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
        target_package_path = Path(download_dir) / _prepared_package_relative_path(manifest)
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

    def _remote_version_manifest_paths(self, app_id: str, channel: str, platform: str) -> list[Path]:
        """读取索引中的版本 manifest；索引不可用时回退目录扫描。"""
        index_path = self.source.versions_index_path(app_id, channel, platform)
        scanned_paths = self.source.iter_version_manifest_paths(app_id, platform, channel)
        if index_path.is_file():
            indexed_paths = self._manifest_paths_from_index(index_path)
            if indexed_paths:
                return self._merge_manifest_paths(indexed_paths, scanned_paths)
        return scanned_paths

    def _merge_manifest_paths(self, first_paths: list[Path], second_paths: list[Path]) -> list[Path]:
        """合并 manifest 路径并保持首次出现优先级。

        :param first_paths: 优先路径列表。
        :param second_paths: 补充路径列表。
        :return: 去重后的路径列表。
        """
        merged: list[Path] = []
        seen: set[Path] = set()
        for path in [*first_paths, *second_paths]:
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            merged.append(path)
        return merged

    def _manifest_paths_from_index(self, index_path: Path) -> list[Path]:
        """从 versions.json 索引解析 manifest 路径。"""
        try:
            payload = json.loads(index_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"versions index is not valid JSON: {index_path}") from exc
        if not isinstance(payload, dict):
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "versions index must be a JSON object")
        versions_payload = payload.get("versions")
        if not isinstance(versions_payload, list):
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "versions index versions must be a list")
        manifest_paths: list[Path] = []
        for item in versions_payload:
            if not isinstance(item, dict):
                raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "versions index item must be an object")
            manifest_url = item.get("manifest_url")
            if not isinstance(manifest_url, str) or not manifest_url.strip():
                raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "versions index manifest_url must be a non-empty string")
            manifest_path = self.source.resolve_package_path(manifest_url.strip())
            if manifest_path.is_file():
                manifest_paths.append(manifest_path)
        return manifest_paths

    def _validate_manifest_identity(
        self,
        manifest: UpdateManifest,
        *,
        app_id: str,
        channel: str,
        platform: str,
    ) -> None:
        """校验 manifest 与请求上下文一致。"""
        if manifest.app_id != app_id:
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"manifest app_id mismatch: {manifest.app_id}")
        if manifest.channel != channel:
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"manifest channel mismatch: {manifest.channel}")
        if platform and manifest.platform and manifest.platform != platform:
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"manifest platform mismatch: {manifest.platform}")


def _prepared_package_relative_path(manifest: UpdateManifest) -> Path:
    """生成按版本隔离的本地准备包路径。"""
    filename = PurePosixPath(manifest.package.url.replace("\\", "/")).name
    return (
        Path(_safe_path_component(manifest.app_id, "app"))
        / _safe_path_component(manifest.channel, "channel")
        / _safe_path_component(manifest.platform, "any")
        / _safe_path_component(manifest.version, "version")
        / _safe_path_component(filename, "package.zip")
    )


def _safe_path_component(value: str, fallback: str) -> str:
    """把 manifest 字段压成单个本地路径段。"""
    text = str(value or "").strip()
    if not text:
        return fallback
    return text.replace("/", "_").replace("\\", "_").replace("..", "_")
