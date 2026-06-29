"""在线升级工具设置解析。"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from collections.abc import Sequence

from update_online_tool.errors import UpdateError, UpdateErrorCode

UPDATE_SETTINGS_FILE_ENV = "UPDATE_ONLINE_TOOL_SETTINGS_FILE"


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
    updater_executable_name: str = "Updater.exe"
    nas_roots: tuple[Path, ...] = ()

    @classmethod
    def load(
        cls,
        path: Path | None = None,
        *,
        app_id: str = "update-online-tool",
        bundled_paths: Sequence[Path] | None = None,
    ) -> "UpdateToolSettings":
        """读取 settings.json。

        :param path: 显式设置文件路径。
        :param app_id: 接入方应用标识，用于解析用户级配置。
        :param bundled_paths: 打包内置 settings 候选路径。
        :return: 设置模型。
        """
        settings_path = resolve_settings_path(
            app_id=app_id,
            explicit_path=path,
            bundled_paths=bundled_paths,
        )
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
        nas_roots = _nas_roots(nas_payload)
        nas_root_text = nas_payload.get("root")
        if isinstance(nas_root_text, str) and nas_root_text.strip():
            nas_root = Path(nas_root_text.strip())
        else:
            nas_root = nas_roots[0]
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
            nas_root=nas_root,
            nas_roots=nas_roots,
            default_channel=_optional_text(publish_payload, "default_channel", "stable"),
            default_minimum_version=_optional_text(publish_payload, "default_minimum_version", "1.0.0"),
            package_filename=_optional_text(publish_payload, "package_filename", "package.zip"),
            updater_executable_name=_optional_text(
                updater_payload,
                "executable_name",
                "Updater.exe",
            ),
        )

    def selected_nas_root(self) -> Path:
        """返回第一个可访问 NAS 根目录；都不可访问时返回主根目录。"""
        for root in self.nas_roots or (self.nas_root,):
            if _is_readable_directory(root):
                return root
        return self.nas_root


def _is_readable_directory(path: Path) -> bool:
    try:
        return path.is_dir() and os.access(path, os.R_OK)
    except OSError:
        return False


def _nas_roots(payload: dict[str, Any]) -> tuple[Path, ...]:
    roots_value = payload.get("roots")
    if roots_value is not None:
        if not isinstance(roots_value, list):
            raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "roots must be a list")
        roots = tuple(Path(item.strip()) for item in roots_value if isinstance(item, str) and item.strip())
        if roots:
            return roots
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "roots must contain at least one non-empty string")
    return (Path(_require_text(payload, "root")),)


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


def resolve_settings_path(
    *,
    app_id: str,
    explicit_path: Path | None = None,
    bundled_paths: Sequence[Path] | None = None,
) -> Path:
    """解析 settings.json 路径。

    优先级：显式路径 > 通用环境变量 > 用户级配置 > 打包内置配置 > 开发目录兜底。

    :param app_id: 接入方应用标识。
    :param explicit_path: 显式 settings 路径。
    :param bundled_paths: 打包内置 settings 候选路径。
    :return: settings.json 路径。
    """
    if explicit_path is not None:
        return Path(explicit_path)
    env_path = os.getenv(UPDATE_SETTINGS_FILE_ENV, "").strip()
    if env_path:
        return Path(env_path)
    user_path = user_settings_path(app_id)
    if user_path.is_file():
        return user_path
    for bundled_path in bundled_paths or ():
        candidate = Path(bundled_path)
        if candidate.is_file():
            return candidate
    return Path.cwd() / "config" / "settings.json"


def user_settings_path(app_id: str) -> Path:
    """生成用户级 settings.json 路径。

    :param app_id: 接入方应用标识。
    :return: 用户级 settings.json 路径。
    """
    normalized_app_id = _normalize_app_id(app_id)
    if os.name == "nt":
        root = Path(os.getenv("APPDATA", "") or Path.home() / "AppData" / "Roaming")
        return root / normalized_app_id / "update-online-tool" / "settings.json"
    if sys.platform == "darwin":
        return (
            Path.home()
            / "Library"
            / "Application Support"
            / normalized_app_id
            / "update-online-tool"
            / "settings.json"
        )
    root = Path(os.getenv("XDG_CONFIG_HOME", "") or Path.home() / ".config")
    return root / normalized_app_id / "update-online-tool" / "settings.json"


def _normalize_app_id(app_id: str) -> str:
    """规范化应用标识。

    :param app_id: 原始应用标识。
    :return: 非空应用标识。
    """
    normalized = str(app_id or "").strip()
    if not normalized:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "app_id must be a non-empty string")
    return normalized
