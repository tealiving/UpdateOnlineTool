"""设置解析测试。"""

from __future__ import annotations

import json
from pathlib import Path

from update_online_tool.settings import (
    UPDATE_SETTINGS_FILE_ENV,
    UpdateToolSettings,
    normalize_nas_root,
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


def test_settings_loads_ordered_nas_roots(tmp_path: Path) -> None:
    """验证 settings.json 可解析多个候选 NAS 根路径。"""
    first = tmp_path / "missing"
    second = tmp_path / "nas"
    second.mkdir()
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps({"nas": {"roots": [str(first), str(second)]}}),
        encoding="utf-8",
    )

    settings = UpdateToolSettings.load(settings_path)

    assert settings.nas_root == first
    assert settings.nas_roots == (first, second)
    assert settings.selected_nas_root() == second


def test_settings_normalizes_file_uri_nas_root_with_chinese_text() -> None:
    """验证 file URI NAS 根路径会解码为文件系统路径。"""
    root = normalize_nas_root(
        "file://sjnas01/as/JSGCB/%E6%8A%80%E6%9C%AF%E5%B7%A5%E7%A8%8B%E9%83%A8/"
        "%E6%95%B0%E6%8D%AE%E4%BC%A0%E8%BE%93%E5%85%B1%E4%BA%AB"
    )
    normalized = str(root).replace("\\", "/")

    assert normalized.startswith("//sjnas01/as/JSGCB/技术工程部")
    assert "数据传输共享" in normalized
    assert "file:" not in normalized


def test_settings_preserves_plain_unc_nas_root_with_chinese_text() -> None:
    """验证普通 UNC 中文路径不经过 URL 编码。"""
    raw = r"\\sjnas01\as\JSGCB\技术工程部\数据传输共享"

    root = normalize_nas_root(raw)

    assert "技术工程部" in str(root)
    assert "数据传输共享" in str(root)


def test_settings_keeps_primary_root_when_roots_are_configured(tmp_path: Path) -> None:
    """验证发布主 NAS 不会被 roots[0] 覆盖。"""
    primary = tmp_path / "primary"
    first = tmp_path / "read-only"
    second = tmp_path / "fallback"
    first.mkdir()
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps({"nas": {"root": str(primary), "roots": [str(first), str(second)]}}),
        encoding="utf-8",
    )

    settings = UpdateToolSettings.load(settings_path)

    assert settings.nas_root == primary
    assert settings.nas_roots == (first, second)
    assert settings.selected_nas_root() == first


def test_settings_skips_unreadable_nas_root(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    """验证不可读候选 NAS 会被跳过。"""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    settings = UpdateToolSettings(nas_root=first, nas_roots=(first, second))

    def fake_access(path: Path, mode: int) -> bool:
        return Path(path) != first

    monkeypatch.setattr("update_online_tool.settings.os.access", fake_access)

    assert settings.selected_nas_root() == second


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
    home = tmp_path / "home"
    monkeypatch.setenv(UPDATE_SETTINGS_FILE_ENV, str(env_path))
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("HOME", str(home))
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
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
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
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
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
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    monkeypatch.chdir(tmp_path)

    resolved = resolve_settings_path(app_id="my-tool")

    assert resolved == tmp_path / "config" / "settings.json"
