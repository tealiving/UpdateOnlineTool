"""CLI 测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from update_online_tool.cli import main
from update_online_tool.settings import user_settings_path


def _settings(path: Path, nas_root: Path) -> None:
    """写入测试 settings。

    :param path: settings 路径。
    :param nas_root: NAS 根目录。
    :return: None
    """
    path.write_text(
        json.dumps(
            {
                "nas": {"root": str(nas_root)},
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


def test_cli_publish_writes_package_and_latest_json(tmp_path: Path) -> None:
    """验证 publish 写入 NAS 包和 manifest。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    settings_path = tmp_path / "settings.json"
    nas_root = tmp_path / "nas"
    package = tmp_path / "app.zip"
    package.write_bytes(b"release")
    _settings(settings_path, nas_root)

    exit_code = main(
        [
            "publish",
            "--settings",
            str(settings_path),
            "--app",
            "automation-manual-studio",
            "--version",
            "1.0.6",
            "--package",
            str(package),
        ]
    )

    latest = nas_root / "automation-manual-studio" / "stable" / "latest.json"
    copied = nas_root / "automation-manual-studio" / "v1.0.6" / "package.zip"
    payload = json.loads(latest.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert copied.read_bytes() == b"release"
    assert payload["package"]["sha256"] == hashlib.sha256(b"release").hexdigest()


def test_cli_publish_can_isolate_package_by_platform(tmp_path: Path) -> None:
    """验证 publish 可按平台隔离同版本包路径。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    settings_path = tmp_path / "settings.json"
    nas_root = tmp_path / "nas"
    package = tmp_path / "app.zip"
    package.write_bytes(b"macos-release")
    _settings(settings_path, nas_root)

    exit_code = main(
        [
            "publish",
            "--settings",
            str(settings_path),
            "--app",
            "automation-manual-studio",
            "--version",
            "1.0.6",
            "--platform",
            "macos",
            "--package",
            str(package),
        ]
    )

    latest = nas_root / "automation-manual-studio" / "stable" / "macos" / "latest.json"
    copied = nas_root / "automation-manual-studio" / "v1.0.6" / "macos" / "package.zip"
    payload = json.loads(latest.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert copied.read_bytes() == b"macos-release"
    assert payload["platform"] == "macos"
    assert payload["package"]["url"] == "automation-manual-studio/v1.0.6/macos/package.zip"


def test_cli_verify_accepts_published_release(tmp_path: Path) -> None:
    """验证 verify 接受 publish 生成的发布内容。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    settings_path = tmp_path / "settings.json"
    nas_root = tmp_path / "nas"
    package = tmp_path / "app.zip"
    package.write_bytes(b"release")
    _settings(settings_path, nas_root)
    assert (
        main(
            [
                "publish",
                "--settings",
                str(settings_path),
                "--app",
                "automation-manual-studio",
                "--version",
                "1.0.6",
                "--package",
                str(package),
            ]
        )
        == 0
    )

    assert main(["verify", "--settings", str(settings_path), "--app", "automation-manual-studio"]) == 0


def test_cli_verify_and_check_accept_platform_release(tmp_path: Path) -> None:
    """验证 verify/check 可读取平台隔离 manifest。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    settings_path = tmp_path / "settings.json"
    nas_root = tmp_path / "nas"
    package = tmp_path / "app.zip"
    package.write_bytes(b"macos-release")
    _settings(settings_path, nas_root)
    assert (
        main(
            [
                "publish",
                "--settings",
                str(settings_path),
                "--app",
                "automation-manual-studio",
                "--version",
                "1.0.6",
                "--platform",
                "darwin",
                "--package",
                str(package),
            ]
        )
        == 0
    )

    assert (
        main(
            [
                "verify",
                "--settings",
                str(settings_path),
                "--app",
                "automation-manual-studio",
                "--platform",
                "macos",
            ]
        )
        == 0
    )
    assert (
        main(
            [
                "check",
                "--settings",
                str(settings_path),
                "--app",
                "automation-manual-studio",
                "--current-version",
                "1.0.5",
                "--platform",
                "macos",
            ]
        )
        == 0
    )


def test_cli_init_writes_project_update_endpoint(tmp_path: Path) -> None:
    """验证 init 生成接入方项目配置文件。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    output = tmp_path / "update-endpoint.json"

    exit_code = main(["init", "--app", "my-tool", "--output", str(output)])

    payload = json.loads(output.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload == {
        "channel": "stable",
        "installer_mode": "custom_updater",
        "manifest_sources": [
            {
                "name": "local-nas",
                "manifest_url": "uot-nas://my-tool/stable",
                "package_url_prefix": "uot-nas://nas",
                "auth_provider": "update_online_tool",
                "priority": 10,
            }
        ],
    }


def test_cli_init_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    """验证 init 默认不覆盖已有文件。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    output = tmp_path / "update-endpoint.json"
    output.write_text("{}", encoding="utf-8")

    exit_code = main(["init", "--app", "my-tool", "--output", str(output)])

    assert exit_code == 1
    assert output.read_text(encoding="utf-8") == "{}"


def test_cli_init_with_nas_root_writes_project_settings(tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture[str]) -> None:
    """验证 init 默认同时写入项目内 NAS settings。

    :param tmp_path: pytest 临时目录。
    :param monkeypatch: pytest monkeypatch 工具。
    :param capsys: pytest 输出捕获工具。
    :return: None
    """
    output = tmp_path / "project" / "update-endpoint.json"
    nas_root = tmp_path / "nas"
    project_root = output.parent
    project_root.mkdir()
    nas_root.mkdir()
    monkeypatch.chdir(project_root)

    exit_code = main(
        [
            "init",
            "--app",
            "my-tool",
            "--output",
            str(output),
            "--nas-root",
            str(nas_root),
        ]
    )

    settings_path = project_root / "config" / "settings.json"
    settings_payload = json.loads(settings_path.read_text(encoding="utf-8"))
    captured = capsys.readouterr()
    assert exit_code == 0
    assert output.is_file()
    assert settings_payload["nas"]["root"] == str(nas_root)
    assert settings_payload["updater"]["executable_name"] == "Updater.exe"
    assert "NAS check ok" in captured.out


def test_cli_init_can_write_user_settings_when_requested(tmp_path: Path, monkeypatch) -> None:
    """验证 init 可按需写入用户级 NAS settings。

    :param tmp_path: pytest 临时目录。
    :param monkeypatch: pytest monkeypatch 工具。
    :return: None
    """
    output = tmp_path / "update-endpoint.json"
    nas_root = tmp_path / "nas"
    appdata = tmp_path / "appdata"
    home = tmp_path / "home"
    nas_root.mkdir()
    monkeypatch.setenv("APPDATA", str(appdata))
    monkeypatch.setenv("HOME", str(home))

    exit_code = main(
        [
            "init",
            "--app",
            "my-tool",
            "--output",
            str(output),
            "--nas-root",
            str(nas_root),
            "--user-settings",
        ]
    )

    settings_path = user_settings_path("my-tool")
    settings_payload = json.loads(settings_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert settings_payload["nas"]["root"] == str(nas_root)


def test_cli_init_uses_cwd_name_and_default_output(tmp_path: Path, monkeypatch) -> None:
    """验证 init 最小命令从当前目录推导项目名和默认输出文件。

    :param tmp_path: pytest 临时目录。
    :param monkeypatch: pytest monkeypatch 工具。
    :return: None
    """
    project_root = tmp_path / "my-tool"
    nas_root = tmp_path / "nas"
    project_root.mkdir()
    nas_root.mkdir()
    monkeypatch.chdir(project_root)

    exit_code = main(["init", "--nas-root", str(nas_root)])

    endpoint_payload = json.loads((project_root / "update-endpoint.json").read_text(encoding="utf-8"))
    settings_path = project_root / "config" / "settings.json"
    settings_payload = json.loads(settings_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert endpoint_payload["manifest_sources"][0]["manifest_url"] == "uot-nas://my-tool/stable"
    assert settings_payload["nas"]["root"] == str(nas_root)


def test_cli_init_rejects_unavailable_nas_root_without_writing(
    tmp_path: Path,
    monkeypatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """验证 init 在 NAS 路径不可用时不写入配置。

    :param tmp_path: pytest 临时目录。
    :param monkeypatch: pytest monkeypatch 工具。
    :param capsys: pytest 输出捕获工具。
    :return: None
    """
    output = tmp_path / "update-endpoint.json"
    nas_root = tmp_path / "missing-nas"
    appdata = tmp_path / "appdata"
    monkeypatch.setenv("APPDATA", str(appdata))

    exit_code = main(
        [
            "init",
            "--app",
            "my-tool",
            "--output",
            str(output),
            "--nas-root",
            str(nas_root),
        ]
    )

    captured = capsys.readouterr()
    settings_path = tmp_path / "config" / "settings.json"
    assert exit_code == 1
    assert "NAS root is not available" in captured.err
    assert not output.exists()
    assert not settings_path.exists()


def test_cli_assemble_pyinstaller_normalizes_launcher_and_release(tmp_path: Path) -> None:
    """验证 PyInstaller 装配命令生成标准安装目录和升级目录。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    dist = tmp_path / "dist"
    release_dir = dist / "AutomationManualStudio_release_v1.0.5"
    launcher_dir = dist / "AutomationManualStudio_launcher"
    settings_path = tmp_path / "config" / "settings.json"
    release_internal = release_dir / "_internal"
    launcher_internal = launcher_dir / "_internal"
    release_internal.mkdir(parents=True)
    launcher_internal.mkdir(parents=True)
    settings_path.parent.mkdir()
    (release_dir / "AutomationManualStudio.exe").write_text("gui", encoding="utf-8")
    (launcher_dir / "AutomationManualLauncher.exe").write_text("launcher", encoding="utf-8")
    (release_internal / "python311.dll").write_text("runtime", encoding="utf-8")
    (launcher_internal / "python311.dll").write_text("runtime", encoding="utf-8")
    settings_path.write_text('{"nas":{"root":"D:\\\\Nas"}}', encoding="utf-8")

    exit_code = main(
        [
            "assemble-pyinstaller",
            "--version",
            "1.0.5",
            "--dist-dir",
            str(dist),
            "--app",
            "automation-manual-studio",
            "--product-name",
            "AutomationManualStudio",
            "--settings",
            str(settings_path),
        ]
    )

    install_root = dist / "AutomationManualStudio_install_v1.0.5"
    update_root = dist / "AutomationManualStudio_update_v1.0.5"
    current_payload = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert (install_root / "AutomationManualStudio.exe").read_text(encoding="utf-8") == "launcher"
    assert (install_root / "releases" / "1.0.5" / "AutomationManualStudio.exe").read_text(encoding="utf-8") == "gui"
    assert current_payload["app_id"] == "automation-manual-studio"
    assert current_payload["version"] == "1.0.5"
    assert current_payload["release_dir"] == "releases/1.0.5"
    assert current_payload["executable"] == "AutomationManualStudio.exe"
    assert current_payload["entry"] == {
        "kind": "executable",
        "path": "AutomationManualStudio.exe",
        "platform": "windows",
    }
    assert (install_root / "releases" / "1.0.5" / "_internal" / "config" / "settings.json").is_file()
    assert (update_root / "AutomationManualStudio.exe").read_text(encoding="utf-8") == "gui"
    assert (update_root / "_launcher" / "AutomationManualStudio.exe").read_text(encoding="utf-8") == "launcher"


def test_cli_assemble_pyinstaller_supports_macos_onedir(tmp_path: Path) -> None:
    """验证 macOS PyInstaller onedir 可使用无 .exe 入口装配。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    dist = tmp_path / "dist"
    release_dir = dist / "MyTool_release_v1.2.3"
    launcher_dir = dist / "MyTool_launcher"
    settings_path = tmp_path / "config" / "settings.json"
    release_internal = release_dir / "_internal"
    launcher_internal = launcher_dir / "_internal"
    release_internal.mkdir(parents=True)
    launcher_internal.mkdir(parents=True)
    settings_path.parent.mkdir()
    (release_dir / "MyToolGui").write_text("gui", encoding="utf-8")
    (launcher_dir / "MyToolLauncher").write_text("launcher", encoding="utf-8")
    (release_internal / "libpython3.11.dylib").write_text("runtime", encoding="utf-8")
    (launcher_internal / "libpython3.11.dylib").write_text("runtime", encoding="utf-8")
    settings_path.write_text('{"nas":{"root":"/Volumes/release-share/UpdateOnlineTool"}}', encoding="utf-8")

    exit_code = main(
        [
            "assemble-pyinstaller",
            "--version",
            "1.2.3",
            "--dist-dir",
            str(dist),
            "--app",
            "my-tool",
            "--product-name",
            "MyTool",
            "--platform",
            "macos",
            "--settings",
            str(settings_path),
        ]
    )

    install_root = dist / "MyTool_install_v1.2.3"
    update_root = dist / "MyTool_update_v1.2.3"
    current_payload = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert (install_root / "MyTool").read_text(encoding="utf-8") == "launcher"
    assert (install_root / "releases" / "1.2.3" / "MyTool").read_text(encoding="utf-8") == "gui"
    assert current_payload["executable"] == "MyTool"
    assert current_payload["entry"] == {
        "kind": "executable",
        "path": "MyTool",
        "platform": "macos",
    }
    assert (install_root / "releases" / "1.2.3" / "_internal" / "config" / "settings.json").is_file()
    assert (update_root / "MyTool").read_text(encoding="utf-8") == "gui"
    assert (update_root / "_launcher" / "MyTool").read_text(encoding="utf-8") == "launcher"


def test_cli_assemble_pyinstaller_supports_macos_app_bundle_entries(tmp_path: Path) -> None:
    """验证 macOS .app bundle 可作为 release 与 launcher 入口装配。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    dist = tmp_path / "dist"
    release_dir = dist / "MyTool_release_v1.2.3"
    launcher_dir = dist / "MyTool_launcher"
    settings_path = tmp_path / "config" / "settings.json"
    _write_macos_app_bundle(release_dir / "MyToolGui.app", "gui")
    _write_macos_app_bundle(launcher_dir / "MyToolLauncher.app", "launcher")
    settings_path.parent.mkdir()
    settings_path.write_text('{"nas":{"root":"/Volumes/release-share/UpdateOnlineTool"}}', encoding="utf-8")

    exit_code = main(
        [
            "assemble-pyinstaller",
            "--version",
            "1.2.3",
            "--dist-dir",
            str(dist),
            "--app",
            "my-tool",
            "--product-name",
            "MyTool",
            "--platform",
            "macos",
            "--entry-name",
            "MyTool.app",
            "--release-entry-name",
            "MyToolGui.app",
            "--launcher-entry-name",
            "MyToolLauncher.app",
            "--settings",
            str(settings_path),
        ]
    )

    install_root = dist / "MyTool_install_v1.2.3"
    update_root = dist / "MyTool_update_v1.2.3"
    current_payload = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert (install_root / "MyTool.app" / "Contents" / "MacOS" / "MyToolLauncher").read_text(encoding="utf-8") == "launcher"
    assert (
        install_root / "releases" / "1.2.3" / "MyTool.app" / "Contents" / "MacOS" / "MyToolGui"
    ).read_text(encoding="utf-8") == "gui"
    assert current_payload["executable"] == "MyTool.app"
    assert current_payload["entry"] == {
        "kind": "app_bundle",
        "path": "MyTool.app",
        "platform": "macos",
    }
    assert (
        install_root / "releases" / "1.2.3" / "MyTool.app" / "Contents" / "Resources" / "config" / "settings.json"
    ).is_file()
    assert (update_root / "MyTool.app" / "Contents" / "MacOS" / "MyToolGui").read_text(encoding="utf-8") == "gui"
    assert (update_root / "_launcher" / "MyTool.app" / "Contents" / "MacOS" / "MyToolLauncher").read_text(
        encoding="utf-8"
    ) == "launcher"


def _write_macos_app_bundle(path: Path, executable_text: str) -> None:
    """写入最小 macOS .app 测试 bundle。

    :param path: .app 路径。
    :param executable_text: 入口文件内容。
    :return: None
    """
    executable_name = path.stem
    executable_path = path / "Contents" / "MacOS" / executable_name
    executable_path.parent.mkdir(parents=True)
    (path / "Contents" / "Resources").mkdir(parents=True)
    executable_path.write_text(executable_text, encoding="utf-8")
