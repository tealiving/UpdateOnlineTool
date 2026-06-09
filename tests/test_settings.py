"""设置解析测试。"""

from __future__ import annotations

import json
from pathlib import Path

from update_online_tool.settings import (
    UPDATE_SETTINGS_FILE_ENV,
    UpdateToolSettings,
    resolve_settings_path,
    user_settings_path,
)


def test_settings_loads_nas_root(tmp_path: Path) -> None:
    """验证 settings.json 可解析 NAS 根路径。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "nas": {"root": str(tmp_path / "nas")},
                "publish": {
                    "default_channel": "stable",
                    "default_minimum_version": "1.0.0",
                    "package_filename": "package.zip",
                },
                "updater": {"executable_name": "AutomationManualUpdater.exe"},
            }
        ),
        encoding="utf-8",
    )

    settings = UpdateToolSettings.load(settings_path)

    assert settings.nas_root == tmp_path / "nas"
    assert settings.default_channel == "stable"
    assert settings.package_filename == "package.zip"


def test_resolve_settings_path_prefers_explicit_path(tmp_path: Path) -> None:
    """验证显式 settings 路径优先级最高。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    explicit_path = tmp_path / "explicit.json"

    resolved = resolve_settings_path(app_id="my-tool", explicit_path=explicit_path)

    assert resolved == explicit_path


def test_resolve_settings_path_prefers_environment_path(tmp_path: Path, monkeypatch) -> None:
    """验证通用环境变量优先于用户级配置。

    :param tmp_path: pytest 临时目录。
    :param monkeypatch: pytest monkeypatch 工具。
    :return: None
    """
    env_path = tmp_path / "env-settings.json"
    appdata = tmp_path / "appdata"
    monkeypatch.setenv(UPDATE_SETTINGS_FILE_ENV, str(env_path))
    monkeypatch.setenv("APPDATA", str(appdata))
    user_path = user_settings_path("my-tool")
    user_path.parent.mkdir(parents=True)
    user_path.write_text("{\"nas\":{\"root\":\"D:\\\\Nas\"}}", encoding="utf-8")

    resolved = resolve_settings_path(app_id="my-tool")

    assert resolved == env_path


def test_resolve_settings_path_uses_user_level_settings(tmp_path: Path, monkeypatch) -> None:
    """验证未显式指定时读取用户级 settings。

    :param tmp_path: pytest 临时目录。
    :param monkeypatch: pytest monkeypatch 工具。
    :return: None
    """
    monkeypatch.delenv(UPDATE_SETTINGS_FILE_ENV, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "appdata"))
    settings_path = user_settings_path("my-tool")
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{\"nas\":{\"root\":\"D:\\\\Nas\"}}", encoding="utf-8")

    resolved = resolve_settings_path(app_id="my-tool")

    assert resolved == settings_path


def test_resolve_settings_path_uses_bundled_before_cwd(tmp_path: Path, monkeypatch) -> None:
    """验证打包内置 settings 优先于开发目录兜底。

    :param tmp_path: pytest 临时目录。
    :param monkeypatch: pytest monkeypatch 工具。
    :return: None
    """
    monkeypatch.delenv(UPDATE_SETTINGS_FILE_ENV, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "empty-appdata"))
    monkeypatch.chdir(tmp_path)
    bundled_path = tmp_path / "bundle" / "_internal" / "config" / "settings.json"
    cwd_path = tmp_path / "config" / "settings.json"
    bundled_path.parent.mkdir(parents=True)
    cwd_path.parent.mkdir(parents=True)
    bundled_path.write_text("{\"nas\":{\"root\":\"D:\\\\BundledNas\"}}", encoding="utf-8")
    cwd_path.write_text("{\"nas\":{\"root\":\"D:\\\\CwdNas\"}}", encoding="utf-8")

    resolved = resolve_settings_path(app_id="my-tool", bundled_paths=(bundled_path,))

    assert resolved == bundled_path


def test_resolve_settings_path_falls_back_to_cwd_config(tmp_path: Path, monkeypatch) -> None:
    """验证开发兜底路径仍为当前目录 config/settings.json。

    :param tmp_path: pytest 临时目录。
    :param monkeypatch: pytest monkeypatch 工具。
    :return: None
    """
    monkeypatch.delenv(UPDATE_SETTINGS_FILE_ENV, raising=False)
    monkeypatch.setenv("APPDATA", str(tmp_path / "empty-appdata"))
    monkeypatch.chdir(tmp_path)

    resolved = resolve_settings_path(app_id="my-tool")

    assert resolved == tmp_path / "config" / "settings.json"
