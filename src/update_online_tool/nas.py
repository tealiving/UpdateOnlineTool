"""NAS 发布源路径解析。"""

from __future__ import annotations

from pathlib import Path, PurePosixPath

from update_online_tool.errors import UpdateError, UpdateErrorCode


class NasReleaseSource:
    """NAS 发布源。

    :param root: NAS 根目录。
    :return: None
    """

    def __init__(self, root: Path) -> None:
        """保存 NAS 根目录。

        :param root: NAS 根目录。
        :return: None
        """
        self.root = Path(root)

    def ensure_available(self) -> None:
        """确认 NAS 根目录可访问。

        :return: None
        """
        if not self.root.exists() or not self.root.is_dir():
            raise UpdateError(UpdateErrorCode.NAS_SOURCE_UNAVAILABLE, f"NAS root is not available: {self.root}")

    def manifest_path(self, app_id: str, channel: str, platform: str = "") -> Path:
        """解析通道 manifest 路径。

        :param app_id: 应用标识。
        :param channel: 发布通道。
        :param platform: 可选平台；为空时使用旧版通道路径。
        :return: manifest 路径。
        """
        if platform:
            return self.root / app_id / channel / platform / "latest.json"
        return self.root / app_id / channel / "latest.json"

    def versions_index_path(self, app_id: str, channel: str, platform: str = "") -> Path:
        """解析通道版本索引路径。

        :param app_id: 应用标识。
        :param channel: 发布通道。
        :param platform: 可选平台；为空时使用旧版通道路径。
        :return: versions.json 路径。
        """
        if platform:
            return self.root / app_id / channel / platform / "versions.json"
        return self.root / app_id / channel / "versions.json"

    def version_dir(self, app_id: str, version: str, platform: str = "", channel: str = "") -> Path:
        """解析版本目录。

        :param app_id: 应用标识。
        :param version: 版本号。
        :param platform: 可选平台；为空时使用旧版版本路径。
        :param channel: 可选发布通道；为空时使用旧版全局版本路径。
        :return: 版本目录。
        """
        if channel:
            if platform:
                return self.root / app_id / channel / f"v{version}" / platform
            return self.root / app_id / channel / f"v{version}"
        if platform:
            return self.root / app_id / f"v{version}" / platform
        return self.root / app_id / f"v{version}"

    def version_manifest_path(self, app_id: str, version: str, platform: str = "", channel: str = "") -> Path:
        """解析指定版本 manifest 路径。

        :param app_id: 应用标识。
        :param version: 版本号。
        :param platform: 可选平台；为空时使用旧版版本路径。
        :param channel: 可选发布通道；为空时使用旧版全局版本路径。
        :return: 版本 manifest 路径。
        """
        return self.version_dir(app_id, version, platform, channel) / "latest.json"

    def iter_version_manifest_paths(self, app_id: str, platform: str = "", channel: str = "") -> list[Path]:
        """列出应用历史版本 manifest。

        :param app_id: 应用标识。
        :param platform: 可选平台；为空时使用旧版版本路径。
        :param channel: 可选发布通道；为空时优先扫描旧版全局版本目录。
        :return: 按路径排序的 manifest 列表。
        """
        app_root = self.root / app_id
        if not app_root.is_dir():
            return []
        manifest_paths: list[Path] = []
        if channel:
            channel_paths = self._iter_version_manifests_under(app_root / channel, platform)
            manifest_paths.extend(channel_paths)
            channel_versions = self._manifest_version_keys(channel_paths, platform)
            legacy_paths = [
                path
                for path in self._iter_version_manifests_under(app_root, platform)
                if self._manifest_version_key(path, platform) not in channel_versions
            ]
            manifest_paths.extend(legacy_paths)
            return sorted(manifest_paths)
        manifest_paths.extend(self._iter_version_manifests_under(app_root, platform))
        return sorted(manifest_paths)

    def package_path(self, app_id: str, version: str, package_filename: str, platform: str = "", channel: str = "") -> Path:
        """解析发布包路径。

        :param app_id: 应用标识。
        :param version: 版本号。
        :param package_filename: 包文件名。
        :param platform: 可选平台；为空时使用旧版版本路径。
        :param channel: 可选发布通道；为空时使用旧版全局版本路径。
        :return: 发布包路径。
        """
        return self.version_dir(app_id, version, platform, channel) / package_filename

    def _iter_version_manifests_under(self, root: Path, platform: str = "") -> list[Path]:
        """扫描指定根目录下的版本 manifest。

        :param root: 应用根或通道根目录。
        :param platform: 可选平台。
        :return: 按路径排序的 manifest 列表。
        """
        if not root.is_dir():
            return []
        manifest_paths: list[Path] = []
        for version_dir in root.glob("v*"):
            if not version_dir.is_dir():
                continue
            candidate = version_dir / platform / "latest.json" if platform else version_dir / "latest.json"
            if candidate.is_file():
                manifest_paths.append(candidate)
        return sorted(manifest_paths)

    def _manifest_version_keys(self, manifest_paths: list[Path], platform: str = "") -> set[str]:
        """提取 manifest 路径集合中的版本目录名。

        :param manifest_paths: manifest 路径列表。
        :param platform: 可选平台。
        :return: 版本目录名集合。
        """
        return {self._manifest_version_key(path, platform) for path in manifest_paths}

    def _manifest_version_key(self, manifest_path: Path, platform: str = "") -> str:
        """从 manifest 路径推断版本目录名。

        :param manifest_path: manifest 路径。
        :param platform: 可选平台。
        :return: 版本目录名，例如 v1.0.6。
        """
        if platform and manifest_path.parent.name == platform:
            return manifest_path.parent.parent.name
        return manifest_path.parent.name

    def resolve_package_path(self, package_url: str) -> Path:
        """解析 manifest 中的相对包路径。

        :param package_url: package.url 字段。
        :return: 本地/NAS 文件路径。
        """
        url = str(package_url or "").strip()
        if not url or url == ".":
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "package.url must be a non-empty relative path")
        if "\\" in url:
            raise UpdateError(
                UpdateErrorCode.MANIFEST_INVALID,
                f"unsafe package url: {package_url}; use a forward-slash relative path, not UNC or Windows path syntax",
            )
        relative_path = PurePosixPath(url)
        if relative_path.is_absolute() or ".." in relative_path.parts or any(":" in part for part in relative_path.parts):
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"unsafe package url: {package_url}")
        return self.root.joinpath(*relative_path.parts)
