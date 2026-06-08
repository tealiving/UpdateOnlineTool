"""在线升级 manifest 契约模型。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from update_online_tool.errors import UpdateError, UpdateErrorCode

SUPPORTED_SCHEMA_VERSION = 2
_MANIFEST_KEYS = {
    "schema_version",
    "app_id",
    "channel",
    "version",
    "mandatory",
    "min_supported_version",
    "published_at",
    "notes",
    "package",
}
_PACKAGE_KEYS = {"url", "size", "sha256"}
_SHA256_RE = re.compile(r"^[a-fA-F0-9]{64}$")


@dataclass(frozen=True)
class UpdatePackageInfo:
    """升级包信息。

    :param url: NAS 根目录下的包相对路径。
    :param size: 包大小，单位字节。
    :param sha256: 包 SHA-256 十六进制摘要。
    :return: None
    """

    url: str
    size: int
    sha256: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "UpdatePackageInfo":
        """从字典解析包信息。

        :param payload: 包信息字典。
        :return: 包信息模型。
        """
        _reject_extra_keys(payload, allowed_keys=_PACKAGE_KEYS, context="package")
        url = _require_text(payload, "url")
        size = payload.get("size")
        if not isinstance(size, int) or size <= 0:
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "package.size must be a positive integer")
        sha256 = _require_text(payload, "sha256").lower()
        if not _SHA256_RE.match(sha256):
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "package.sha256 must be 64 hex characters")
        return cls(url=url, size=size, sha256=sha256)

    def to_payload(self) -> dict[str, object]:
        """转换为可序列化字典。

        :return: 包信息字典。
        """
        return {
            "url": self.url,
            "size": self.size,
            "sha256": self.sha256,
        }


@dataclass(frozen=True)
class UpdateManifest:
    """在线升级 manifest。

    :param schema_version: manifest schema 版本。
    :param app_id: 应用标识。
    :param channel: 发布通道。
    :param version: 目标版本。
    :param mandatory: 是否强制升级。
    :param min_supported_version: 最低支持版本。
    :param published_at: 发布时间。
    :param notes: 发布说明。
    :param package: 升级包信息。
    :return: None
    """

    schema_version: int
    app_id: str
    channel: str
    version: str
    mandatory: bool
    min_supported_version: str
    published_at: str
    notes: str
    package: UpdatePackageInfo

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "UpdateManifest":
        """从字典解析 manifest。

        :param payload: manifest 字典。
        :return: manifest 模型。
        """
        _reject_extra_keys(payload, allowed_keys=_MANIFEST_KEYS, context="manifest")
        schema_version = payload.get("schema_version")
        if schema_version != SUPPORTED_SCHEMA_VERSION:
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "schema_version must be 2")
        mandatory = payload.get("mandatory")
        if not isinstance(mandatory, bool):
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "mandatory must be a boolean")
        package_payload = payload.get("package")
        if not isinstance(package_payload, dict):
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "package must be an object")
        return cls(
            schema_version=schema_version,
            app_id=_require_text(payload, "app_id"),
            channel=_require_text(payload, "channel"),
            version=_require_text(payload, "version"),
            mandatory=mandatory,
            min_supported_version=_require_text(payload, "min_supported_version"),
            published_at=_require_text(payload, "published_at"),
            notes=_require_text(payload, "notes"),
            package=UpdatePackageInfo.from_payload(package_payload),
        )

    def to_payload(self) -> dict[str, object]:
        """转换为可序列化字典。

        :return: manifest 字典。
        """
        return {
            "schema_version": self.schema_version,
            "app_id": self.app_id,
            "channel": self.channel,
            "version": self.version,
            "mandatory": self.mandatory,
            "min_supported_version": self.min_supported_version,
            "published_at": self.published_at,
            "notes": self.notes,
            "package": self.package.to_payload(),
        }


def _reject_extra_keys(payload: dict[str, Any], *, allowed_keys: set[str], context: str) -> None:
    """拒绝契约之外的字段。

    :param payload: 待检查字典。
    :param allowed_keys: 允许字段集合。
    :param context: 错误上下文。
    :return: None
    """
    extra_keys = sorted(set(payload).difference(allowed_keys))
    if extra_keys:
        raise UpdateError(
            UpdateErrorCode.MANIFEST_INVALID,
            f"{context} contains unsupported keys: {', '.join(extra_keys)}",
        )


def _require_text(payload: dict[str, Any], key: str) -> str:
    """读取非空字符串字段。

    :param payload: 字段来源字典。
    :param key: 字段名。
    :return: 非空字符串。
    """
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"{key} must be a non-empty string")
    return value.strip()
