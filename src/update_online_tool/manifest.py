"""在线升级 manifest 契约模型。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.release_identity import (
    normalize_release_version,
    validate_release_component,
    validate_release_platform,
)

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
    "platform",
    "package",
    "allow_downgrade",
    "hidden",
    "requires_confirmation",
    "rollout_percent",
    "data_schema_version",
    "signature",
}
_PACKAGE_KEYS = {"url", "size", "sha256"}
_SIGNATURE_KEYS = {"algorithm", "key_id", "value"}
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
            raise UpdateError(
                UpdateErrorCode.MANIFEST_INVALID,
                "package.size must be a positive integer",
            )
        sha256 = _require_text(payload, "sha256").lower()
        if not _SHA256_RE.match(sha256):
            raise UpdateError(
                UpdateErrorCode.MANIFEST_INVALID,
                "package.sha256 must be 64 hex characters",
            )
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
class ManifestSignature:
    """manifest 签名信息。"""

    algorithm: str
    key_id: str
    value: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ManifestSignature":
        """从字典解析签名信息。"""
        _reject_extra_keys(payload, allowed_keys=_SIGNATURE_KEYS, context="signature")
        return cls(
            algorithm=_require_text(payload, "algorithm"),
            key_id=_require_text(payload, "key_id"),
            value=_require_text(payload, "value"),
        )

    def to_payload(self) -> dict[str, object]:
        """转换为可序列化字典。"""
        return {
            "algorithm": self.algorithm,
            "key_id": self.key_id,
            "value": self.value,
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
    :param platform: 可选平台标识。
    :param package: 升级包信息。
    :param allow_downgrade: 是否允许从更高版本切换回该版本。
    :param hidden: 是否从普通版本列表中隐藏。
    :param requires_confirmation: 安装或切换前是否需要用户确认。
    :param rollout_percent: 灰度比例，0-100。
    :param data_schema_version: 应用数据 schema 版本，0 表示未声明。
    :param signature: 可选 manifest 签名。
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
    platform: str = ""
    allow_downgrade: bool = False
    hidden: bool = False
    requires_confirmation: bool = False
    rollout_percent: int = 100
    data_schema_version: int = 0
    signature: ManifestSignature | None = None

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "UpdateManifest":
        """从字典解析 manifest。

        :param payload: manifest 字典。
        :return: manifest 模型。
        """
        _reject_extra_keys(payload, allowed_keys=_MANIFEST_KEYS, context="manifest")
        schema_version = payload.get("schema_version")
        if schema_version != SUPPORTED_SCHEMA_VERSION:
            raise UpdateError(
                UpdateErrorCode.MANIFEST_INVALID, "schema_version must be 2"
            )
        mandatory = payload.get("mandatory")
        if not isinstance(mandatory, bool):
            raise UpdateError(
                UpdateErrorCode.MANIFEST_INVALID, "mandatory must be a boolean"
            )
        package_payload = payload.get("package")
        if not isinstance(package_payload, dict):
            raise UpdateError(
                UpdateErrorCode.MANIFEST_INVALID, "package must be an object"
            )
        signature_payload = payload.get("signature")
        if signature_payload is not None and not isinstance(signature_payload, dict):
            raise UpdateError(
                UpdateErrorCode.MANIFEST_INVALID, "signature must be an object"
            )
        return cls(
            schema_version=schema_version,
            app_id=validate_release_component(
                _require_text(payload, "app_id"),
                "app_id",
                error_code=UpdateErrorCode.MANIFEST_INVALID,
            ),
            channel=validate_release_component(
                _require_text(payload, "channel"),
                "channel",
                error_code=UpdateErrorCode.MANIFEST_INVALID,
            ),
            version=normalize_release_version(
                _require_text(payload, "version"),
                error_code=UpdateErrorCode.MANIFEST_INVALID,
            ),
            mandatory=mandatory,
            min_supported_version=_require_text(payload, "min_supported_version"),
            published_at=_require_text(payload, "published_at"),
            notes=_require_text(payload, "notes"),
            package=UpdatePackageInfo.from_payload(package_payload),
            platform=validate_release_platform(
                _optional_text(payload, "platform"),
                error_code=UpdateErrorCode.MANIFEST_INVALID,
                allow_empty=True,
            ),
            allow_downgrade=_optional_bool(payload, "allow_downgrade", default=False),
            hidden=_optional_bool(payload, "hidden", default=False),
            requires_confirmation=_optional_bool(
                payload, "requires_confirmation", default=False
            ),
            rollout_percent=_optional_int_range(
                payload, "rollout_percent", default=100, minimum=0, maximum=100
            ),
            data_schema_version=_optional_int_range(
                payload,
                "data_schema_version",
                default=0,
                minimum=0,
                maximum=1_000_000,
            ),
            signature=ManifestSignature.from_payload(signature_payload)
            if signature_payload
            else None,
        )

    def to_payload(self) -> dict[str, object]:
        """转换为可序列化字典。

        :return: manifest 字典。
        """
        payload: dict[str, object] = {
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
        if self.platform:
            payload["platform"] = self.platform
        if self.allow_downgrade:
            payload["allow_downgrade"] = self.allow_downgrade
        if self.hidden:
            payload["hidden"] = self.hidden
        if self.requires_confirmation:
            payload["requires_confirmation"] = self.requires_confirmation
        if self.rollout_percent != 100:
            payload["rollout_percent"] = self.rollout_percent
        if self.data_schema_version:
            payload["data_schema_version"] = self.data_schema_version
        if self.signature is not None:
            payload["signature"] = self.signature.to_payload()
        return payload


def _reject_extra_keys(
    payload: dict[str, Any], *, allowed_keys: set[str], context: str
) -> None:
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
        raise UpdateError(
            UpdateErrorCode.MANIFEST_INVALID, f"{key} must be a non-empty string"
        )
    return value.strip()


def _validate_optional_component(payload: dict[str, Any], key: str) -> str:
    """读取可选的跨平台安全路径段。"""
    value = _optional_text(payload, key)
    if not value:
        return ""
    return validate_release_component(
        value, key, error_code=UpdateErrorCode.MANIFEST_INVALID
    )


def _optional_text(payload: dict[str, Any], key: str) -> str:
    """读取可选字符串字段。

    :param payload: 字段来源字典。
    :param key: 字段名。
    :return: 非空字符串或空字符串。
    """
    value = payload.get(key)
    if value is None:
        return ""
    if not isinstance(value, str) or not value.strip():
        raise UpdateError(
            UpdateErrorCode.MANIFEST_INVALID, f"{key} must be a non-empty string"
        )
    return value.strip()


def _optional_bool(payload: dict[str, Any], key: str, *, default: bool) -> bool:
    """读取可选布尔字段。"""
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, bool):
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"{key} must be a boolean")
    return value


def _optional_int_range(
    payload: dict[str, Any],
    key: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """读取可选整数范围字段。"""
    value = payload.get(key)
    if value is None:
        return default
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < minimum
        or value > maximum
    ):
        raise UpdateError(
            UpdateErrorCode.MANIFEST_INVALID,
            f"{key} must be an integer between {minimum} and {maximum}",
        )
    return value
