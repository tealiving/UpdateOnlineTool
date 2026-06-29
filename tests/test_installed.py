"""安装根版本状态测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.installed import list_installed_versions, migrate_install_root, switch_installed_version


def test_list_installed_versions_marks_current(tmp_path: Path) -> None:
    """验证可列出安装根版本并标记当前版本。"""
    install_root = _write_install_root(tmp_path, current_version="1.0.5", entry_name="MyTool.exe")
    _write_release_entry(install_root, "1.0.4", "MyTool.exe", "old")
    _write_release_entry(install_root, "1.0.6", "MyTool.exe", "new")

    versions = list_installed_versions(install_root=install_root)

    assert [item.version for item in versions] == ["1.0.6", "1.0.5", "1.0.4"]
    assert [item.is_current for item in versions] == [False, True, False]
    assert all(item.entry_exists for item in versions)


def test_switch_installed_version_updates_current_json_atomically(tmp_path: Path) -> None:
    """验证切换已安装版本会更新 current.json。"""
    install_root = _write_install_root(tmp_path, current_version="1.0.5", entry_name="MyTool.exe")
    _write_release_entry(install_root, "1.0.4", "MyTool.exe", "old")

    switched = switch_installed_version(install_root=install_root, version="1.0.4")

    payload = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
    assert switched.version == "1.0.4"
    assert payload["version"] == "1.0.4"
    assert payload["release_dir"] == "releases/1.0.4"
    assert payload["executable"] == "MyTool.exe"
    assert payload["entry"] == {
        "kind": "executable",
        "path": "MyTool.exe",
        "platform": "windows",
    }
    assert not (install_root / "current.json.tmp").exists()
    assert not list(install_root.glob("current.json.*.tmp"))


def test_switch_installed_version_supports_macos_app_bundle(tmp_path: Path) -> None:
    """验证可切换到 macOS .app bundle release。"""
    install_root = _write_install_root(tmp_path, current_version="1.0.5", entry_name="MyTool.app", platform="macos")
    _write_macos_app_bundle(install_root / "releases" / "1.0.4" / "MyTool.app", "old")

    switched = switch_installed_version(install_root=install_root, version="1.0.4")

    payload = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
    assert switched.entry_kind == "app_bundle"
    assert payload["entry"] == {
        "kind": "app_bundle",
        "path": "MyTool.app",
        "platform": "macos",
    }


def test_switch_installed_version_supports_macos_app_with_different_inner_executable(tmp_path: Path) -> None:
    """验证 .app 内部可执行文件名不同于 bundle 名时仍可切换。"""
    install_root = _write_install_root(tmp_path, current_version="1.0.5", entry_name="MyTool.app", platform="macos")
    _write_macos_app_bundle(
        install_root / "releases" / "1.0.4" / "MyTool.app",
        "old",
        executable_name="MyTool-v1.0.4",
    )

    switched = switch_installed_version(install_root=install_root, version="1.0.4")

    assert switched.entry_exists is True
    assert switched.entry_kind == "app_bundle"


def test_switch_installed_version_supports_legacy_macos_executable_after_app_bundle(tmp_path: Path) -> None:
    """验证 .app 新版本可切回旧裸入口版本。"""
    install_root = _write_install_root(tmp_path, current_version="1.1.0", entry_name="MyTool.app", platform="macos")
    _write_release_entry(install_root, "1.0.8", "MyTool", "legacy")

    versions = list_installed_versions(install_root=install_root)
    switched = switch_installed_version(install_root=install_root, version="1.0.8")
    payload = json.loads((install_root / "current.json").read_text(encoding="utf-8"))

    assert {item.version: item.entry_exists for item in versions}["1.0.8"] is True
    assert switched.entry_path == install_root / "releases" / "1.0.8" / "MyTool"
    assert switched.entry_kind == "executable"
    assert payload["executable"] == "MyTool"
    assert payload["entry"] == {
        "kind": "executable",
        "path": "MyTool",
        "platform": "macos",
    }


def test_switch_installed_version_preserves_unknown_current_json_fields(tmp_path: Path) -> None:
    """验证切换版本时保留 current.json 中的扩展字段。"""
    install_root = _write_install_root(tmp_path, current_version="1.0.5", entry_name="MyTool.exe")
    _write_release_entry(install_root, "1.0.4", "MyTool.exe", "old")
    current_path = install_root / "current.json"
    payload = json.loads(current_path.read_text(encoding="utf-8"))
    payload["launcher_schema"] = 3
    payload["entry"]["display_name"] = "My Tool"
    current_path.write_text(json.dumps(payload), encoding="utf-8")

    switch_installed_version(install_root=install_root, version="1.0.4")

    switched_payload = json.loads(current_path.read_text(encoding="utf-8"))
    assert switched_payload["launcher_schema"] == 3
    assert switched_payload["entry"]["display_name"] == "My Tool"
    assert switched_payload["version"] == "1.0.4"


def test_switch_installed_version_rejects_missing_release(tmp_path: Path) -> None:
    """验证目标 release 不存在时拒绝切换。"""
    install_root = _write_install_root(tmp_path, current_version="1.0.5", entry_name="MyTool.exe")

    with pytest.raises(UpdateError) as error:
        switch_installed_version(install_root=install_root, version="1.0.4")

    assert error.value.code is UpdateErrorCode.SETTINGS_INVALID


def test_switch_installed_version_rejects_existing_update_lock(tmp_path: Path) -> None:
    """验证本地版本切换复用安装根 update.lock。"""
    install_root = _write_install_root(tmp_path, current_version="1.0.5", entry_name="MyTool.exe")
    _write_release_entry(install_root, "1.0.4", "MyTool.exe", "old")
    (install_root / "update.lock").write_text('{"pid": 123}\n', encoding="utf-8")

    with pytest.raises(UpdateError) as error:
        switch_installed_version(install_root=install_root, version="1.0.4")

    assert error.value.code is UpdateErrorCode.UPDATE_LOCKED


def test_migrate_install_root_copies_flat_install_into_release(tmp_path: Path) -> None:
    """验证可把旧版平铺安装根迁移为 releases/current.json。"""
    install_root = tmp_path / "legacy"
    install_root.mkdir()
    (install_root / "MyTool.exe").write_text("app", encoding="utf-8")
    resources = install_root / "resources"
    resources.mkdir()
    (resources / "data.txt").write_text("data", encoding="utf-8")
    (install_root / "update-result.json").write_text("{}", encoding="utf-8")

    result = migrate_install_root(
        install_root=install_root,
        version="1.0.0",
        entry_name="MyTool.exe",
        app_id="my-tool",
        platform="windows",
    )

    current_payload = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
    assert result.version == "1.0.0"
    assert (install_root / "releases" / "1.0.0" / "MyTool.exe").read_text(encoding="utf-8") == "app"
    assert (install_root / "releases" / "1.0.0" / "resources" / "data.txt").read_text(encoding="utf-8") == "data"
    assert not (install_root / "releases" / "1.0.0" / "update-result.json").exists()
    assert current_payload["app_id"] == "my-tool"
    assert current_payload["version"] == "1.0.0"
    assert current_payload["entry"]["platform"] == "windows"


def test_migrate_install_root_dry_run_does_not_write(tmp_path: Path) -> None:
    """验证 dry-run 只输出迁移计划。"""
    install_root = tmp_path / "legacy"
    install_root.mkdir()
    (install_root / "MyTool.exe").write_text("app", encoding="utf-8")

    result = migrate_install_root(
        install_root=install_root,
        version="1.0.0",
        entry_name="MyTool.exe",
        app_id="my-tool",
        dry_run=True,
    )

    assert result.dry_run is True
    assert result.copied_items == ["MyTool.exe"]
    assert not (install_root / "current.json").exists()
    assert not (install_root / "releases").exists()


def test_migrate_install_root_rejects_existing_release_without_force(tmp_path: Path) -> None:
    """验证目标 release 已存在时默认拒绝覆盖。"""
    install_root = tmp_path / "legacy"
    install_root.mkdir()
    (install_root / "MyTool.exe").write_text("app", encoding="utf-8")
    (install_root / "releases" / "1.0.0").mkdir(parents=True)

    with pytest.raises(UpdateError) as error:
        migrate_install_root(
            install_root=install_root,
            version="1.0.0",
            entry_name="MyTool.exe",
            app_id="my-tool",
        )

    assert error.value.code is UpdateErrorCode.SETTINGS_INVALID


def _write_install_root(
    tmp_path: Path,
    *,
    current_version: str,
    entry_name: str,
    platform: str = "windows",
) -> Path:
    """写入测试安装根。"""
    install_root = tmp_path / "install"
    _write_release_entry(install_root, current_version, entry_name, "current")
    current_payload = {
        "app_id": "my-tool",
        "version": current_version,
        "release_dir": f"releases/{current_version}",
        "executable": entry_name,
        "entry": {
            "kind": "app_bundle" if entry_name.endswith(".app") else "executable",
            "path": entry_name,
            "platform": platform,
        },
    }
    (install_root / "current.json").write_text(json.dumps(current_payload), encoding="utf-8")
    return install_root


def _write_release_entry(install_root: Path, version: str, entry_name: str, content: str) -> None:
    """写入 release 入口。"""
    release_dir = install_root / "releases" / version
    release_dir.mkdir(parents=True, exist_ok=True)
    if entry_name.endswith(".app"):
        _write_macos_app_bundle(release_dir / entry_name, content)
        return
    (release_dir / entry_name).write_text(content, encoding="utf-8")


def _write_macos_app_bundle(path: Path, executable_text: str, *, executable_name: str = "") -> None:
    """写入最小 .app bundle。"""
    executable_path = path / "Contents" / "MacOS" / (executable_name or path.stem)
    executable_path.parent.mkdir(parents=True, exist_ok=True)
    executable_path.write_text(executable_text, encoding="utf-8")
