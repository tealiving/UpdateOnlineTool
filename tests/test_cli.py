"""CLI 测试。"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from update_online_tool.cli import main
from update_online_tool.settings import user_settings_path
from update_online_tool.signature import load_hmac_key, sign_manifest_payload


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
    versions_index = nas_root / "automation-manual-studio" / "stable" / "versions.json"
    copied = nas_root / "automation-manual-studio" / "stable" / "v1.0.6" / "package.zip"
    payload = json.loads(latest.read_text(encoding="utf-8"))
    index_payload = json.loads(versions_index.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert copied.read_bytes() == b"release"
    assert payload["package"]["sha256"] == hashlib.sha256(b"release").hexdigest()
    assert index_payload["versions"][0]["version"] == "1.0.6"
    assert index_payload["versions"][0]["manifest_url"] == "automation-manual-studio/stable/v1.0.6/latest.json"
    assert not list(nas_root.rglob("*.tmp"))
    assert not (nas_root / "automation-manual-studio" / "stable" / "publish.lock").exists()


def test_cli_publish_rejects_release_contract_with_different_version(tmp_path: Path) -> None:
    """验证 NAS manifest 版本不能与已验证构建产物的契约分裂。"""
    settings_path = tmp_path / "settings.json"
    nas_root = tmp_path / "nas"
    package = tmp_path / "app.zip"
    contract = tmp_path / "uot-release.json"
    package.write_bytes(b"release")
    contract.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "app_id": "automation-manual-studio",
                "version": "1.0.5",
                "platform": "windows",
                "entry_path": "AutomationManualStudio.exe",
                "required_paths": [],
            }
        ),
        encoding="utf-8",
    )
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
            "windows",
            "--package",
            str(package),
            "--release-contract",
            str(contract),
        ]
    )

    assert exit_code == 1
    assert not (nas_root / "automation-manual-studio").exists()


def test_cli_publish_requires_contract_to_be_inside_release_package(tmp_path: Path) -> None:
    """验证带契约发布时，契约必须属于实际上传的 zip。"""
    settings_path = tmp_path / "settings.json"
    nas_root = tmp_path / "nas"
    package = tmp_path / "app.zip"
    contract = tmp_path / "uot-release.json"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("Demo.app/Contents/MacOS/Demo", "app")
    contract.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "app_id": "automation-manual-studio",
                "version": "1.0.6",
                "platform": "windows",
                "entry_path": "Demo.exe",
                "required_paths": [],
            }
        ),
        encoding="utf-8",
    )
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
            "windows",
            "--package",
            str(package),
            "--release-contract",
            str(contract),
        ]
    )

    assert exit_code == 1
    assert not (nas_root / "automation-manual-studio").exists()


def test_cli_publish_accepts_matching_contract_from_release_package(tmp_path: Path) -> None:
    """验证带契约的 release 包可作为 NAS 发布的版本身份来源。"""
    settings_path = tmp_path / "settings.json"
    nas_root = tmp_path / "nas"
    package = tmp_path / "app.zip"
    contract = tmp_path / "uot-release.json"
    payload = {
        "schema_version": 1,
        "app_id": "automation-manual-studio",
        "version": "1.0.6",
        "platform": "windows",
        "entry_path": "Demo.exe",
        "required_paths": [],
    }
    contract.write_text(json.dumps(payload), encoding="utf-8")
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("Demo.exe", "app")
        archive.writestr("uot-release.json", json.dumps(payload))
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
            "windows",
            "--package",
            str(package),
            "--release-contract",
            str(contract),
        ]
    )

    assert exit_code == 0
    assert (nas_root / "automation-manual-studio" / "stable" / "v1.0.6" / "windows" / "package.zip").is_file()


def test_cli_writes_and_validates_framework_release_contract(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """验证框架构建产物可在发布前写入并校验 UOT 契约。"""
    release_dir = tmp_path / "release"
    executable = release_dir / "Demo.app/Contents/MacOS/Demo"
    settings = release_dir / "Demo.app/Contents/Resources/uot/settings.json"
    bridge = release_dir / "Demo.app/Contents/Resources/uot/uot-bridge/uot-bridge"
    executable.parent.mkdir(parents=True)
    executable.write_text("app", encoding="utf-8")
    settings.parent.mkdir(parents=True)
    settings.write_text("{}", encoding="utf-8")
    bridge.parent.mkdir(parents=True)
    bridge.write_text("bridge", encoding="utf-8")
    command = [
        "--release-dir",
        str(release_dir),
        "--app",
        "demo",
        "--version",
        "1.2.3",
        "--platform",
        "macos",
        "--entry-path",
        "Demo.app",
        "--required-path",
        "Demo.app/Contents/Resources/uot/settings.json",
        "--required-path",
        "Demo.app/Contents/Resources/uot/uot-bridge/uot-bridge",
    ]

    assert main(["write-release-contract", *command]) == 0
    capsys.readouterr()
    exit_code = main(["validate-release", *command])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["contract"]["version"] == "1.2.3"


def test_cli_publish_rejects_existing_publish_lock(tmp_path: Path) -> None:
    """验证 publish.lock 存在时拒绝并发发布且不复制包。"""
    settings_path = tmp_path / "settings.json"
    nas_root = tmp_path / "nas"
    package = tmp_path / "app.zip"
    lock_path = nas_root / "automation-manual-studio" / "stable" / "publish.lock"
    package.write_bytes(b"release")
    lock_path.parent.mkdir(parents=True)
    lock_path.write_text('{"pid": 1}\n', encoding="utf-8")
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

    assert exit_code == 1
    assert lock_path.is_file()
    assert not (nas_root / "automation-manual-studio" / "stable" / "v1.0.6" / "package.zip").exists()


def test_cli_publish_can_read_notes_from_file(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """验证 publish 可从文件读取版本说明并输出到历史列表。"""
    settings_path = tmp_path / "settings.json"
    nas_root = tmp_path / "nas"
    package = tmp_path / "app.zip"
    notes_file = tmp_path / "release-notes.md"
    package.write_bytes(b"release")
    notes_file.write_text("## 1.0.6\n- fix startup\n- improve update history\n", encoding="utf-8")
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
                "--notes-file",
                str(notes_file),
            ]
        )
        == 0
    )
    capsys.readouterr()

    exit_code = main(["list-remote", "--settings", str(settings_path), "--app", "automation-manual-studio"])
    payload = json.loads(capsys.readouterr().out)
    latest = json.loads((nas_root / "automation-manual-studio" / "stable" / "latest.json").read_text(encoding="utf-8"))
    versions_index = json.loads(
        (nas_root / "automation-manual-studio" / "stable" / "versions.json").read_text(encoding="utf-8")
    )

    assert exit_code == 0
    assert latest["notes"] == "## 1.0.6\n- fix startup\n- improve update history"
    assert versions_index["versions"][0]["notes"] == "## 1.0.6\n- fix startup\n- improve update history"
    assert payload["versions"][0]["notes"] == "## 1.0.6\n- fix startup\n- improve update history"


def test_cli_publish_missing_notes_file_does_not_copy_package(tmp_path: Path) -> None:
    """验证 notes 文件缺失时不会留下半成品发布包。"""
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
            "--notes-file",
            str(tmp_path / "missing.md"),
        ]
    )

    assert exit_code == 1
    assert not (nas_root / "automation-manual-studio" / "stable" / "v1.0.6" / "package.zip").exists()


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
    versions_index = nas_root / "automation-manual-studio" / "stable" / "macos" / "versions.json"
    copied = nas_root / "automation-manual-studio" / "stable" / "v1.0.6" / "macos" / "package.zip"
    payload = json.loads(latest.read_text(encoding="utf-8"))
    index_payload = json.loads(versions_index.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert copied.read_bytes() == b"macos-release"
    assert payload["platform"] == "macos"
    assert payload["package"]["url"] == "automation-manual-studio/stable/v1.0.6/macos/package.zip"
    assert index_payload["platform"] == "macos"
    assert index_payload["versions"][0]["manifest_url"] == "automation-manual-studio/stable/v1.0.6/macos/latest.json"


def test_cli_publish_can_store_same_version_remotely_by_channel(tmp_path: Path) -> None:
    """验证同一版本号可在不同通道远端存放不同包。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    settings_path = tmp_path / "settings.json"
    nas_root = tmp_path / "nas"
    test_package = tmp_path / "test.zip"
    stable_package = tmp_path / "stable.zip"
    test_package.write_bytes(b"test-release")
    stable_package.write_bytes(b"stable-release")
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
                "--channel",
                "test",
                "--package",
                str(test_package),
            ]
        )
        == 0
    )
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
                "--channel",
                "stable",
                "--package",
                str(stable_package),
            ]
        )
        == 0
    )

    test_copied = nas_root / "automation-manual-studio" / "test" / "v1.0.6" / "package.zip"
    stable_copied = nas_root / "automation-manual-studio" / "stable" / "v1.0.6" / "package.zip"
    test_latest = json.loads((nas_root / "automation-manual-studio" / "test" / "latest.json").read_text(encoding="utf-8"))
    stable_latest = json.loads(
        (nas_root / "automation-manual-studio" / "stable" / "latest.json").read_text(encoding="utf-8")
    )
    assert test_copied.read_bytes() == b"test-release"
    assert stable_copied.read_bytes() == b"stable-release"
    assert test_latest["package"]["url"] == "automation-manual-studio/test/v1.0.6/package.zip"
    assert stable_latest["package"]["url"] == "automation-manual-studio/stable/v1.0.6/package.zip"


def test_cli_publish_writes_version_policy_and_list_remote_filters_hidden(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """验证 publish 可写入版本策略且 list-remote 默认过滤 hidden 版本。"""
    settings_path = tmp_path / "settings.json"
    nas_root = tmp_path / "nas"
    visible_package = tmp_path / "visible.zip"
    hidden_package = tmp_path / "hidden.zip"
    visible_package.write_bytes(b"visible")
    hidden_package.write_bytes(b"hidden")
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
                "1.0.5",
                "--package",
                str(visible_package),
            ]
        )
        == 0
    )
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
                str(hidden_package),
                "--hidden",
                "--allow-downgrade",
                "--requires-confirmation",
                "--rollout-percent",
                "25",
                "--data-schema-version",
                "3",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert main(["list-remote", "--settings", str(settings_path), "--app", "automation-manual-studio"]) == 0
    visible_payload = json.loads(capsys.readouterr().out)
    assert main(
        [
            "list-remote",
            "--settings",
            str(settings_path),
            "--app",
            "automation-manual-studio",
            "--include-hidden",
        ]
    ) == 0
    all_payload = json.loads(capsys.readouterr().out)

    latest_payload = json.loads(
        (nas_root / "automation-manual-studio" / "stable" / "latest.json").read_text(encoding="utf-8")
    )
    index_payload = json.loads(
        (nas_root / "automation-manual-studio" / "stable" / "versions.json").read_text(encoding="utf-8")
    )
    assert [item["version"] for item in visible_payload["versions"]] == ["1.0.5"]
    assert [item["version"] for item in all_payload["versions"]] == ["1.0.6", "1.0.5"]
    assert all_payload["versions"][0]["hidden"] is True
    assert all_payload["versions"][0]["allow_downgrade"] is True
    assert all_payload["versions"][0]["requires_confirmation"] is True
    assert all_payload["versions"][0]["rollout_percent"] == 25
    assert all_payload["versions"][0]["data_schema_version"] == 3
    assert latest_payload["hidden"] is True
    assert latest_payload["rollout_percent"] == 25
    assert index_payload["versions"][0]["hidden"] is True


def test_cli_keygen_publish_and_verify_signed_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """验证 CLI 可生成密钥、签名发布并校验 manifest。"""
    settings_path = tmp_path / "settings.json"
    nas_root = tmp_path / "nas"
    package = tmp_path / "app.zip"
    key_path = tmp_path / "signing.key"
    public_key_path = tmp_path / "signing.pub"
    package.write_bytes(b"release")
    _settings(settings_path, nas_root)
    assert main(["keygen", "--output", str(key_path), "--public-output", str(public_key_path)]) == 0
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
                "--sign-key",
                str(key_path),
                "--key-id",
                "release",
            ]
        )
        == 0
    )
    capsys.readouterr()

    exit_code = main(
        [
            "verify",
            "--settings",
            str(settings_path),
            "--app",
            "automation-manual-studio",
            "--signature-key",
            str(public_key_path),
        ]
    )

    latest_payload = json.loads((nas_root / "automation-manual-studio" / "stable" / "latest.json").read_text())
    assert exit_code == 0
    assert latest_payload["signature"]["algorithm"] == "ed25519"
    assert latest_payload["signature"]["key_id"] == "release"


def test_cli_verify_rejects_tampered_signed_manifest(tmp_path: Path) -> None:
    """验证签名 manifest 被篡改后 verify 失败。"""
    settings_path = tmp_path / "settings.json"
    nas_root = tmp_path / "nas"
    package = tmp_path / "app.zip"
    key_path = tmp_path / "signing.key"
    package.write_bytes(b"release")
    _settings(settings_path, nas_root)
    assert main(["keygen", "--output", str(key_path)]) == 0
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
                "--sign-key",
                str(key_path),
            ]
        )
        == 0
    )
    latest_path = nas_root / "automation-manual-studio" / "stable" / "latest.json"
    payload = json.loads(latest_path.read_text(encoding="utf-8"))
    payload["version"] = "9.9.9"
    latest_path.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = main(
        [
            "verify",
            "--settings",
            str(settings_path),
            "--app",
            "automation-manual-studio",
            "--signature-key",
            str(key_path),
        ]
    )

    assert exit_code == 1


def test_cli_check_rejects_tampered_signed_manifest(tmp_path: Path) -> None:
    """验证 check --signature-key 会拒绝被篡改的 latest manifest。"""
    settings_path = tmp_path / "settings.json"
    nas_root = tmp_path / "nas"
    package = tmp_path / "app.zip"
    key_path = tmp_path / "signing.key"
    package.write_bytes(b"release")
    _settings(settings_path, nas_root)
    assert main(["keygen", "--output", str(key_path)]) == 0
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
                "--sign-key",
                str(key_path),
            ]
        )
        == 0
    )
    latest_path = nas_root / "automation-manual-studio" / "stable" / "latest.json"
    payload = json.loads(latest_path.read_text(encoding="utf-8"))
    payload["notes"] = "tampered"
    latest_path.write_text(json.dumps(payload), encoding="utf-8")

    exit_code = main(
        [
            "check",
            "--settings",
            str(settings_path),
            "--app",
            "automation-manual-studio",
            "--current-version",
            "1.0.0",
            "--signature-key",
            str(key_path),
        ]
    )

    assert exit_code == 1


def test_cli_remote_version_commands_reject_tampered_signed_manifest(tmp_path: Path) -> None:
    """验证 list/show/prepare 在指定签名公钥时都会拒绝被篡改的历史 manifest。"""
    settings_path = tmp_path / "settings.json"
    nas_root = tmp_path / "nas"
    package = tmp_path / "app.zip"
    key_path = tmp_path / "signing.key"
    package.write_bytes(b"release")
    _settings(settings_path, nas_root)
    assert main(["keygen", "--output", str(key_path)]) == 0
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
                "--sign-key",
                str(key_path),
            ]
        )
        == 0
    )
    version_manifest_path = nas_root / "automation-manual-studio" / "stable" / "v1.0.6" / "latest.json"
    payload = json.loads(version_manifest_path.read_text(encoding="utf-8"))
    payload["notes"] = "tampered"
    version_manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    assert main(["list-remote", "--settings", str(settings_path), "--app", "automation-manual-studio", "--signature-key", str(key_path)]) == 1
    assert (
        main(
            [
                "show-version",
                "--settings",
                str(settings_path),
                "--app",
                "automation-manual-studio",
                "--version",
                "1.0.6",
                "--signature-key",
                str(key_path),
            ]
        )
        == 1
    )
    assert (
        main(
            [
                "prepare-version",
                "--settings",
                str(settings_path),
                "--app",
                "automation-manual-studio",
                "--version",
                "1.0.6",
                "--download-dir",
                str(tmp_path / "downloads"),
                "--signature-key",
                str(key_path),
            ]
        )
        == 1
    )


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


def test_cli_list_remote_outputs_published_versions(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """验证 list-remote 输出已发布版本列表。

    :param tmp_path: pytest 临时目录。
    :param capsys: pytest 输出捕获工具。
    :return: None
    """
    settings_path = tmp_path / "settings.json"
    nas_root = tmp_path / "nas"
    package_old = tmp_path / "old.zip"
    package_new = tmp_path / "new.zip"
    package_old.write_bytes(b"old")
    package_new.write_bytes(b"new")
    _settings(settings_path, nas_root)
    assert main(["publish", "--settings", str(settings_path), "--app", "automation-manual-studio", "--version", "1.0.5", "--package", str(package_old)]) == 0
    assert main(["publish", "--settings", str(settings_path), "--app", "automation-manual-studio", "--version", "1.0.6", "--package", str(package_new)]) == 0
    capsys.readouterr()

    exit_code = main(["list-remote", "--settings", str(settings_path), "--app", "automation-manual-studio"])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert [item["version"] for item in payload["versions"]] == ["1.0.6", "1.0.5"]
    assert all(item["package_exists"] for item in payload["versions"])


def test_cli_show_version_outputs_one_manifest(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """验证 show-version 输出指定版本 manifest。

    :param tmp_path: pytest 临时目录。
    :param capsys: pytest 输出捕获工具。
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
                "macos",
                "--package",
                str(package),
            ]
        )
        == 0
    )
    capsys.readouterr()

    exit_code = main(
        [
            "show-version",
            "--settings",
            str(settings_path),
            "--app",
            "automation-manual-studio",
            "--version",
            "1.0.6",
            "--platform",
            "macos",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["version"] == "1.0.6"
    assert payload["platform"] == "macos"
    assert payload["package"]["url"] == "automation-manual-studio/stable/v1.0.6/macos/package.zip"


def test_cli_prepare_version_copies_specific_package(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """验证 prepare-version 可复制并校验指定版本包。

    :param tmp_path: pytest 临时目录。
    :param capsys: pytest 输出捕获工具。
    :return: None
    """
    settings_path = tmp_path / "settings.json"
    nas_root = tmp_path / "nas"
    package = tmp_path / "app.zip"
    download_dir = tmp_path / "downloads"
    package.write_bytes(b"rollback")
    _settings(settings_path, nas_root)
    assert main(["publish", "--settings", str(settings_path), "--app", "automation-manual-studio", "--version", "1.0.4", "--package", str(package)]) == 0
    capsys.readouterr()

    exit_code = main(
        [
            "prepare-version",
            "--settings",
            str(settings_path),
            "--app",
            "automation-manual-studio",
            "--version",
            "1.0.4",
            "--download-dir",
            str(download_dir),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["version"] == "1.0.4"
    assert payload["verified"] is True
    assert Path(payload["manifest_path"]).is_file()
    assert Path(payload["package_path"]).read_bytes() == b"rollback"


def test_cli_prepare_version_outputs_indexed_manifest_path(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """验证 prepare-version 输出 versions.json 实际命中的 manifest 路径。"""
    settings_path = tmp_path / "settings.json"
    nas_root = tmp_path / "nas"
    payload_bytes = b"indexed"
    package_path = nas_root / "automation-manual-studio" / "stable" / "custom" / "1.0.8" / "package.zip"
    manifest_path = package_path.parent / "latest.json"
    package_path.parent.mkdir(parents=True)
    package_path.write_bytes(payload_bytes)
    _settings(settings_path, nas_root)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "app_id": "automation-manual-studio",
                "channel": "stable",
                "version": "1.0.8",
                "mandatory": False,
                "min_supported_version": "1.0.0",
                "published_at": "2026-06-08T00:00:00+00:00",
                "notes": "indexed",
                "package": {
                    "url": "automation-manual-studio/stable/custom/1.0.8/package.zip",
                    "size": len(payload_bytes),
                    "sha256": hashlib.sha256(payload_bytes).hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )
    (nas_root / "automation-manual-studio" / "stable" / "versions.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "app_id": "automation-manual-studio",
                "channel": "stable",
                "versions": [
                    {
                        "version": "1.0.8",
                        "manifest_url": "automation-manual-studio/stable/custom/1.0.8/latest.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    exit_code = main(
        [
            "prepare-version",
            "--settings",
            str(settings_path),
            "--app",
            "automation-manual-studio",
            "--version",
            "1.0.8",
            "--download-dir",
            str(tmp_path / "downloads"),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert Path(payload["manifest_path"]) == manifest_path
    assert Path(payload["package_path"]).read_bytes() == payload_bytes


def test_cli_list_installed_outputs_install_root_versions(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """验证 list-installed 输出安装根版本列表。

    :param tmp_path: pytest 临时目录。
    :param capsys: pytest 输出捕获工具。
    :return: None
    """
    install_root = _write_install_root(tmp_path, current_version="1.0.5", entry_name="MyTool.exe")
    _write_install_release_entry(install_root, "1.0.4", "MyTool.exe", "old")

    exit_code = main(["list-installed", "--install-root", str(install_root)])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert [item["version"] for item in payload["versions"]] == ["1.0.5", "1.0.4"]
    assert payload["versions"][0]["is_current"] is True
    assert payload["versions"][1]["entry_exists"] is True


def test_cli_list_installed_normalizes_release_dir_install_root(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """验证 list-installed 误传 release 目录时输出归一化安装根。

    :param tmp_path: pytest 临时目录
    :param capsys: pytest 输出捕获工具
    :return: None
    """
    install_root = _write_install_root(tmp_path, current_version="1.0.5", entry_name="MyTool.exe")

    exit_code = main(["list-installed", "--install-root", str(install_root / "releases" / "1.0.5")])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload["install_root"] == str(install_root)
    assert [item["version"] for item in payload["versions"]] == ["1.0.5"]


def test_cli_switch_installed_updates_current_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """验证 switch-installed 可切换 current.json。

    :param tmp_path: pytest 临时目录。
    :param capsys: pytest 输出捕获工具。
    :return: None
    """
    install_root = _write_install_root(tmp_path, current_version="1.0.5", entry_name="MyTool.exe")
    _write_install_release_entry(install_root, "1.0.4", "MyTool.exe", "old")

    exit_code = main(["switch-installed", "--install-root", str(install_root), "--version", "1.0.4"])

    payload = json.loads(capsys.readouterr().out)
    current_payload = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["version"] == "1.0.4"
    assert current_payload["version"] == "1.0.4"
    assert current_payload["entry"]["path"] == "MyTool.exe"


def test_cli_migrate_install_root_creates_release_layout(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """验证 migrate-install-root 可迁移旧安装根。"""
    install_root = tmp_path / "legacy"
    install_root.mkdir()
    (install_root / "MyTool.exe").write_text("app", encoding="utf-8")

    exit_code = main(
        [
            "migrate-install-root",
            "--install-root",
            str(install_root),
            "--version",
            "1.0.0",
            "--entry-name",
            "MyTool.exe",
            "--app",
            "my-tool",
            "--platform",
            "windows",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    current_payload = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["version"] == "1.0.0"
    assert payload["copied_items"] == ["MyTool.exe"]
    assert (install_root / "releases" / "1.0.0" / "MyTool.exe").is_file()
    assert current_payload["entry"]["path"] == "MyTool.exe"


def test_cli_write_and_verify_migration_package(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """验证 CLI 可生成并校验旧客户端迁移包模板。"""
    package_dir = tmp_path / "migration"

    write_exit = main(
        [
            "write-migration-package",
            "--output-dir",
            str(package_dir),
            "--app",
            "my-tool",
            "--version",
            "1.0.0",
            "--entry-name",
            "MyTool.exe",
            "--platform",
            "windows",
        ]
    )
    write_output = json.loads(capsys.readouterr().out)
    verify_exit = main(["verify-migration-package", "--package-dir", str(package_dir)])
    verify_output = json.loads(capsys.readouterr().out)

    assert write_exit == 0
    assert verify_exit == 0
    assert write_output["plan_path"].endswith("migration.json")
    assert verify_output["valid"] is True


def test_cli_install_prepared_updates_current_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """验证 install-prepared 可安装并切换版本。"""
    install_root = _write_install_root(tmp_path, current_version="1.0.5", entry_name="MyTool.exe")
    package = _write_runtime_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    manifest = tmp_path / "latest.json"
    _write_runtime_manifest(manifest, package, version="1.0.6")

    exit_code = main(
        [
            "install-prepared",
            "--install-root",
            str(install_root),
            "--package",
            str(package),
            "--manifest",
            str(manifest),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    current_payload = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["version"] == "1.0.6"
    assert current_payload["version"] == "1.0.6"
    assert current_payload["previous_version"] == "1.0.5"


def test_cli_install_prepared_dry_run_does_not_switch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """验证 install-prepared --dry-run 不切换安装根状态。"""
    install_root = _write_install_root(tmp_path, current_version="1.0.5", entry_name="MyTool.exe")
    package = _write_runtime_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    manifest = tmp_path / "latest.json"
    _write_runtime_manifest(manifest, package, version="1.0.6")

    exit_code = main(
        [
            "install-prepared",
            "--install-root",
            str(install_root),
            "--package",
            str(package),
            "--manifest",
            str(manifest),
            "--dry-run",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    current_payload = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["message"] == "dry-run ok"
    assert current_payload["version"] == "1.0.5"
    assert not (install_root / "releases" / "1.0.6").exists()


def test_cli_install_prepared_verifies_manifest_signature(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """验证 install-prepared 可在安装前校验 manifest 签名。"""
    install_root = _write_install_root(tmp_path, current_version="1.0.5", entry_name="MyTool.exe")
    package = _write_runtime_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    manifest = tmp_path / "latest.json"
    key_path = tmp_path / "signing.key"
    key_path.write_text("release-secret\n", encoding="utf-8")
    _write_runtime_manifest(manifest, package, version="1.0.6")
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    signed_payload = sign_manifest_payload(payload, key=load_hmac_key(key_path), key_id="release")
    manifest.write_text(json.dumps(signed_payload), encoding="utf-8")

    exit_code = main(
        [
            "install-prepared",
            "--install-root",
            str(install_root),
            "--package",
            str(package),
            "--manifest",
            str(manifest),
            "--signature-key",
            str(key_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    current_payload = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["version"] == "1.0.6"
    assert current_payload["version"] == "1.0.6"


def test_cli_rollback_switches_to_previous_version(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """验证 rollback 可回滚到 previous_version。"""
    install_root = _write_install_root(tmp_path, current_version="1.0.5", entry_name="MyTool.exe")
    _write_install_release_entry(install_root, "1.0.6", "MyTool.exe", "new")
    assert main(["switch-installed", "--install-root", str(install_root), "--version", "1.0.6"]) == 0
    capsys.readouterr()

    exit_code = main(["rollback", "--install-root", str(install_root)])

    payload = json.loads(capsys.readouterr().out)
    current_payload = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["version"] == "1.0.5"
    assert current_payload["version"] == "1.0.5"


def test_cli_doctor_outputs_report_and_archive(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """验证 doctor 输出诊断报告并可写入 zip 包。"""
    install_root = _write_install_root(tmp_path, current_version="1.0.5", entry_name="MyTool.exe")
    output_path = tmp_path / "doctor.json"
    archive_path = tmp_path / "doctor.zip"

    exit_code = main(
        [
            "doctor",
            "--install-root",
            str(install_root),
            "--output",
            str(output_path),
            "--archive",
            str(archive_path),
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    saved_payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert exit_code == 0
    assert payload["install_root"] == str(install_root)
    assert payload["files"]["current_json"] is True
    assert payload["archive"] == str(archive_path)
    assert saved_payload["install_root"] == str(install_root)
    assert archive_path.is_file()


def test_cli_write_updater_spec_outputs_generation_plan(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """验证 write-updater-spec 输出 PyInstaller spec 生成结果。"""
    output_dir = tmp_path / "updater-spec"

    exit_code = main(
        [
            "write-updater-spec",
            "--output-dir",
            str(output_dir),
            "--name",
            "MyToolUpdater",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert Path(payload["spec_path"]).is_file()
    assert Path(payload["entry_script"]).is_file()
    assert payload["pyinstaller_command"][-1] == payload["spec_path"]
    assert "update_online_tool.updater_cli" in Path(payload["entry_script"]).read_text(encoding="utf-8")


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
        "installer_mode": "uot_updater",
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


def test_cli_init_normalizes_file_uri_nas_root(tmp_path: Path, monkeypatch) -> None:
    """验证 init 写入 settings 前会规范化 file URI NAS 路径。"""
    output = tmp_path / "project" / "update-endpoint.json"
    project_root = output.parent
    project_root.mkdir()
    monkeypatch.chdir(project_root)

    exit_code = main(
        [
            "init",
            "--app",
            "my-tool",
            "--output",
            str(output),
            "--nas-root",
            "file://sjnas01/as/JSGCB/%E6%8A%80%E6%9C%AF%E5%B7%A5%E7%A8%8B%E9%83%A8",
            "--skip-nas-check",
        ]
    )

    settings_payload = json.loads((project_root / "config" / "settings.json").read_text(encoding="utf-8"))
    normalized = settings_payload["nas"]["root"].replace("\\", "/")
    assert exit_code == 0
    assert normalized.startswith("//sjnas01/as/JSGCB/技术工程部")
    assert "file:" not in normalized


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


def test_cli_assemble_pyinstaller_copies_updater_bundle(tmp_path: Path) -> None:
    """验证 PyInstaller 装配可把 updater 产物放入安装根 updater/。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    dist = tmp_path / "dist"
    release_dir = dist / "MyTool_release_v1.0.5"
    launcher_dir = dist / "MyTool_launcher"
    updater_bundle = dist / "MyToolUpdater"
    (release_dir / "_internal").mkdir(parents=True)
    (launcher_dir / "_internal").mkdir(parents=True)
    updater_bundle.mkdir(parents=True)
    (release_dir / "MyTool.exe").write_text("gui", encoding="utf-8")
    (launcher_dir / "MyToolLauncher.exe").write_text("launcher", encoding="utf-8")
    (release_dir / "_internal" / "python311.dll").write_text("runtime", encoding="utf-8")
    (launcher_dir / "_internal" / "python311.dll").write_text("runtime", encoding="utf-8")
    (updater_bundle / "MyToolUpdater.exe").write_text("updater", encoding="utf-8")

    exit_code = main(
        [
            "assemble-pyinstaller",
            "--version",
            "1.0.5",
            "--dist-dir",
            str(dist),
            "--product-name",
            "MyTool",
            "--updater-bundle",
            str(updater_bundle),
        ]
    )

    install_root = dist / "MyTool_install_v1.0.5"
    update_root = dist / "MyTool_update_v1.0.5"
    assert exit_code == 0
    assert (install_root / "updater" / "MyToolUpdater" / "MyToolUpdater.exe").read_text(encoding="utf-8") == "updater"
    assert (update_root / "updater" / "MyToolUpdater" / "MyToolUpdater.exe").read_text(encoding="utf-8") == "updater"


def test_cli_assemble_pyinstaller_supports_stable_bootstrap_agent_mode(tmp_path: Path) -> None:
    """新模式将 Agent 固定在安装根，升级包不再携带 launcher/updater sidecar。"""
    dist = tmp_path / "dist"
    release_dir = dist / "MyTool_release_v1.0.5"
    launcher_dir = dist / "MyTool_launcher"
    agent_bundle = dist / "MyToolAgent"
    (release_dir / "_internal").mkdir(parents=True)
    (launcher_dir / "_internal").mkdir(parents=True)
    agent_bundle.mkdir(parents=True)
    (release_dir / "MyTool.exe").write_text("gui", encoding="utf-8")
    (launcher_dir / "MyToolBootstrap.exe").write_text("bootstrap", encoding="utf-8")
    (release_dir / "_internal" / "python311.dll").write_text("runtime", encoding="utf-8")
    (launcher_dir / "_internal" / "python311.dll").write_text("runtime", encoding="utf-8")
    (agent_bundle / "uot-agent.exe").write_text("agent", encoding="utf-8")

    exit_code = main(
        [
            "assemble-pyinstaller",
            "--version",
            "1.0.5",
            "--dist-dir",
            str(dist),
            "--product-name",
            "MyTool",
            "--launcher-entry-name",
            "MyToolBootstrap.exe",
            "--bootstrap-agent-mode",
            "--agent-bundle",
            str(agent_bundle),
        ]
    )

    install_root = dist / "MyTool_install_v1.0.5"
    update_root = dist / "MyTool_update_v1.0.5"
    assert exit_code == 0
    assert (install_root / "MyToolBootstrap.exe").read_text(encoding="utf-8") == "bootstrap"
    assert (install_root / "agent" / "MyToolAgent" / "uot-agent.exe").read_text(encoding="utf-8") == "agent"
    assert (update_root / "MyTool.exe").read_text(encoding="utf-8") == "gui"
    assert not (update_root / "_launcher").exists()
    assert not (update_root / "updater").exists()


def test_cli_writes_agent_and_bootstrap_specs(tmp_path: Path) -> None:
    """CLI 可生成稳定运行时两个独立可执行文件的 PyInstaller spec。"""
    agent_output = tmp_path / "agent"
    bootstrap_output = tmp_path / "bootstrap"

    agent_exit = main(["write-agent-spec", "--output-dir", str(agent_output), "--name", "MyToolAgent"])
    bootstrap_exit = main(
        ["write-bootstrap-spec", "--output-dir", str(bootstrap_output), "--name", "MyToolBootstrap"]
    )

    assert agent_exit == 0
    assert bootstrap_exit == 0
    assert "update_online_tool.agent_cli" in (agent_output / "uot_agent_entry.py").read_text(encoding="utf-8")
    assert "update_online_tool.bootstrap_cli" in (bootstrap_output / "uot_bootstrap_entry.py").read_text(encoding="utf-8")


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
    _write_app_bundle_symlink(release_dir / "MyToolGui.app")
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
    release_link = install_root / "releases" / "1.2.3" / "MyTool.app" / "Contents" / "Resources" / "runtime.dylib"
    update_link = update_root / "MyTool.app" / "Contents" / "Resources" / "runtime.dylib"
    assert release_link.is_symlink()
    assert update_link.is_symlink()
    assert release_link.readlink() == Path("../Frameworks/runtime.dylib")
    assert update_link.readlink() == Path("../Frameworks/runtime.dylib")
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


def _write_app_bundle_symlink(path: Path) -> None:
    """写入 macOS .app 内部资源 symlink，用于验证装配不会解引用。

    :param path: .app bundle 根路径。
    :return: None
    """
    target = path / "Contents" / "Frameworks" / "runtime.dylib"
    link = path / "Contents" / "Resources" / "runtime.dylib"
    target.parent.mkdir(parents=True)
    target.write_text("runtime", encoding="utf-8")
    try:
        link.symlink_to(Path("../Frameworks/runtime.dylib"))
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink is not available on this filesystem: {exc}")


def _write_install_root(tmp_path: Path, *, current_version: str, entry_name: str) -> Path:
    """写入测试安装根。

    :param tmp_path: pytest 临时目录。
    :param current_version: 当前版本。
    :param entry_name: release 入口名。
    :return: 安装根路径。
    """
    install_root = tmp_path / "install"
    _write_install_release_entry(install_root, current_version, entry_name, "current")
    current_payload = {
        "app_id": "my-tool",
        "version": current_version,
        "release_dir": f"releases/{current_version}",
        "executable": entry_name,
        "entry": {
            "kind": "executable",
            "path": entry_name,
            "platform": "windows",
        },
    }
    (install_root / "current.json").write_text(json.dumps(current_payload), encoding="utf-8")
    return install_root


def _write_install_release_entry(install_root: Path, version: str, entry_name: str, content: str) -> None:
    """写入测试 release 入口。

    :param install_root: 安装根路径。
    :param version: release 版本。
    :param entry_name: release 入口名。
    :param content: 文件内容。
    :return: None
    """
    release_dir = install_root / "releases" / version
    release_dir.mkdir(parents=True, exist_ok=True)
    (release_dir / entry_name).write_text(content, encoding="utf-8")


def _write_runtime_package(path: Path, files: dict[str, str]) -> Path:
    """写入 runtime 测试包。"""
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def _write_runtime_manifest(path: Path, package: Path, *, version: str) -> None:
    """写入 runtime 测试 manifest。"""
    path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "app_id": "automation-manual-studio",
                "channel": "stable",
                "version": version,
                "mandatory": False,
                "min_supported_version": "1.0.0",
                "published_at": "2026-06-18T00:00:00+00:00",
                "notes": f"v{version}",
                "platform": "windows",
                "package": {
                    "url": "automation-manual-studio/package.zip",
                    "size": package.stat().st_size,
                    "sha256": hashlib.sha256(package.read_bytes()).hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )


def test_cli_assemble_release_supports_tauri_app_with_stable_runtime(tmp_path: Path) -> None:
    """通用 CLI 可装配 Tauri `.app`、Agent 与原生 Bootstrap。"""
    release_dir = tmp_path / "release"
    bootstrap_dir = tmp_path / "bootstrap"
    agent_bundle = tmp_path / "uot-agent"
    app_bundle = release_dir / "Timer.app"
    (app_bundle / "Contents" / "MacOS").mkdir(parents=True)
    bootstrap_dir.mkdir()
    agent_bundle.mkdir()
    (app_bundle / "Contents" / "MacOS" / "Timer").write_text("tauri", encoding="utf-8")
    (bootstrap_dir / "uot-bootstrap").write_text("bootstrap", encoding="utf-8")
    (agent_bundle / "uot-agent").write_text("agent", encoding="utf-8")

    exit_code = main(
        [
            "assemble-release",
            "--version",
            "1.0.0",
            "--app",
            "desktop-floating-timer",
            "--release-dir",
            str(release_dir),
            "--release-entry",
            "Timer.app",
            "--bootstrap-dir",
            str(bootstrap_dir),
            "--bootstrap-entry",
            "uot-bootstrap",
            "--agent-bundle",
            str(agent_bundle),
            "--install-output",
            str(tmp_path / "install"),
            "--update-output",
            str(tmp_path / "update"),
            "--platform",
            "macos",
            "--entry-path",
            "Timer.app",
        ]
    )

    assert exit_code == 0
    assert (tmp_path / "install" / "uot-bootstrap").read_text(encoding="utf-8") == "bootstrap"
    assert (tmp_path / "install" / "agent" / "uot-agent" / "uot-agent").read_text(encoding="utf-8") == "agent"
    assert (tmp_path / "install" / "releases" / "1.0.0" / "Timer.app" / "Contents" / "MacOS" / "Timer").is_file()
    assert not (tmp_path / "update" / "agent").exists()
