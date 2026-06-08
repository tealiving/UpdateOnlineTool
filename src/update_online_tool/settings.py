"""在线升级工具设置解析。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from update_online_tool.errors import UpdateError, UpdateErrorCode


@dataclass(frozen=True)
class UpdateToolSettings:
    """在线升级工具设置。

    :param nas_root: NAS 根目录。
    :param default_channel: 默认发布通道。
    :param default_minimum_version: 默认最低支持版本。
    :param package_filename: 发布包文件名。
    :param updater_executable_name: updater 可执行文件名。
    :return: None
    """

    nas_root: Path
    default_channel: str = "stable"
    default_minimum_version: str = "1.0.0"
    package_filename: str = "package.zip"
    updater_executable_name: str = "AutomationManualUpdater.exe"

    @classmethod
    def load(cls, path: Path | None = None) -> "UpdateToolSettings":
        """读取 settings.json。

        :param path: 显式设置文件路径；为空时读取当前目录 config/settings.json。
        :return: 设置模型。
        """
        settings_path = Path(path) if path is not None else Path.cwd() / "config" / "settings.json"
        try:
            payload = json.loads(settings_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"settings file cannot be read: {settings_path}") from exc
        except json.JSONDecodeError as exc:
            raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"settings file is not valid JSON: {settings_path}") from exc
        if not isinstance(payload, dict):
            raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "settings must be a JSON object")
        return cls.from_payload(payload)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "UpdateToolSettings":
        """从字典解析设置。

        :param payload: 设置字典。
        :return: 设置模型。
        """
        nas_payload = payload.get("nas")
        if not isinstance(nas_payload, dict):
            raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "nas must be an object")
        nas_root = _require_text(nas_payload, "root")
        publish_payload = payload.get("publish")
        if publish_payload is None:
            publish_payload = {}
        if not isinstance(publish_payload, dict):
            raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "publish must be an object")
        updater_payload = payload.get("updater")
        if updater_payload is None:
            updater_payload = {}
        if not isinstance(updater_payload, dict):
            raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "updater must be an object")
        return cls(
            nas_root=Path(nas_root),
            default_channel=_optional_text(publish_payload, "default_channel", "stable"),
            default_minimum_version=_optional_text(publish_payload, "default_minimum_version", "1.0.0"),
            package_filename=_optional_text(publish_payload, "package_filename", "package.zip"),
            updater_executable_name=_optional_text(
                updater_payload,
                "executable_name",
                "AutomationManualUpdater.exe",
            ),
        )


def _require_text(payload: dict[str, Any], key: str) -> str:
    """读取必填文本字段。

    :param payload: 来源字典。
    :param key: 字段名。
    :return: 非空文本。
    """
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"{key} must be a non-empty string")
    return value.strip()


def _optional_text(payload: dict[str, Any], key: str, default: str) -> str:
    """读取可选文本字段。

    :param payload: 来源字典。
    :param key: 字段名。
    :param default: 默认值。
    :return: 非空文本或默认值。
    """
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"{key} must be a non-empty string")
    return value.strip()
