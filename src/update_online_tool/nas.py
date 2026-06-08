"""NAS 发布源路径解析。"""

from __future__ import annotations

from pathlib import Path

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

    def manifest_path(self, app_id: str, channel: str) -> Path:
        """解析通道 manifest 路径。

        :param app_id: 应用标识。
        :param channel: 发布通道。
        :return: manifest 路径。
        """
        return self.root / app_id / channel / "latest.json"

    def version_dir(self, app_id: str, version: str) -> Path:
        """解析版本目录。

        :param app_id: 应用标识。
        :param version: 版本号。
        :return: 版本目录。
        """
        return self.root / app_id / f"v{version}"

    def package_path(self, app_id: str, version: str, package_filename: str) -> Path:
        """解析发布包路径。

        :param app_id: 应用标识。
        :param version: 版本号。
        :param package_filename: 包文件名。
        :return: 发布包路径。
        """
        return self.version_dir(app_id, version) / package_filename

    def resolve_package_path(self, package_url: str) -> Path:
        """解析 manifest 中的相对包路径。

        :param package_url: package.url 字段。
        :return: 本地/NAS 文件路径。
        """
        relative_path = Path(package_url)
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"unsafe package url: {package_url}")
        return self.root / relative_path
