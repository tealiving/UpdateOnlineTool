"""CLI 测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from update_online_tool.cli import main


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


def test_cli_init_with_nas_root_writes_user_settings(tmp_path: Path, monkeypatch) -> None:
    """验证 init 可同时写入用户级 NAS settings。

    :param tmp_path: pytest 临时目录。
    :param monkeypatch: pytest monkeypatch 工具。
    :return: None
    """
    output = tmp_path / "update-endpoint.json"
    nas_root = tmp_path / "nas"
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

    settings_path = appdata / "my-tool" / "update-online-tool" / "settings.json"
    settings_payload = json.loads(settings_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert output.is_file()
    assert settings_payload["nas"]["root"] == str(nas_root)
    assert settings_payload["updater"]["executable_name"] == "Updater.exe"


def test_cli_init_uses_cwd_name_and_default_output(tmp_path: Path, monkeypatch) -> None:
    """验证 init 最小命令从当前目录推导项目名和默认输出文件。

    :param tmp_path: pytest 临时目录。
    :param monkeypatch: pytest monkeypatch 工具。
    :return: None
    """
    project_root = tmp_path / "my-tool"
    nas_root = tmp_path / "nas"
    appdata = tmp_path / "appdata"
    project_root.mkdir()
    monkeypatch.chdir(project_root)
    monkeypatch.setenv("APPDATA", str(appdata))

    exit_code = main(["init", "--nas-root", str(nas_root)])

    endpoint_payload = json.loads((project_root / "update-endpoint.json").read_text(encoding="utf-8"))
    settings_path = appdata / "my-tool" / "update-online-tool" / "settings.json"
    settings_payload = json.loads(settings_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert endpoint_payload["manifest_sources"][0]["manifest_url"] == "uot-nas://my-tool/stable"
    assert settings_payload["nas"]["root"] == str(nas_root)
