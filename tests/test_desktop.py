"""桌面升级客户端测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.downloader import CancellationToken
from update_online_tool.desktop import DesktopUpdateClient, DesktopUpdateConfig
from update_online_tool.settings import UpdateToolSettings
from update_online_tool.signature import generate_hmac_key, load_hmac_key, sign_manifest_payload
from update_online_tool.versioning import UpdateDecision


def test_desktop_client_checks_with_current_json_version(tmp_path: Path) -> None:
    """验证桌面客户端从 current.json 读取当前版本并检查更新。"""
    install_root = _write_install_root(tmp_path, version="1.0.0")
    nas_root = tmp_path / "nas"
    _write_manifest(nas_root, version="1.1.0")
    client = _client(install_root=install_root, nas_root=nas_root)

    result = client.check()

    assert result.decision is UpdateDecision.OPTIONAL_UPDATE
    assert result.manifest.version == "1.1.0"


def test_desktop_client_check_rejects_tampered_signed_manifest(tmp_path: Path) -> None:
    """验证桌面检查更新阶段也校验 manifest 签名。"""
    install_root = _write_install_root(tmp_path, version="1.0.0")
    nas_root = tmp_path / "nas"
    key_path = tmp_path / "signature.key"
    key_path.write_text(generate_hmac_key(), encoding="utf-8")
    _write_manifest(nas_root, version="1.1.0", sign_key=key_path)
    latest_path = nas_root / "my-tool" / "stable" / "latest.json"
    payload = json.loads(latest_path.read_text(encoding="utf-8"))
    payload["notes"] = "tampered"
    latest_path.write_text(json.dumps(payload), encoding="utf-8")
    client = _client(install_root=install_root, nas_root=nas_root, signature_key=key_path)

    with pytest.raises(UpdateError) as error:
        client.check()

    assert error.value.code is UpdateErrorCode.MANIFEST_INVALID


def test_desktop_client_lists_remote_versions(tmp_path: Path) -> None:
    """验证桌面客户端列出远端版本。"""
    install_root = _write_install_root(tmp_path, version="1.0.0")
    nas_root = tmp_path / "nas"
    _write_manifest(nas_root, version="1.0.9")
    _write_manifest(nas_root, version="1.1.0")
    client = _client(install_root=install_root, nas_root=nas_root)

    versions = client.list_remote_versions()

    assert [item.version for item in versions] == ["1.1.0", "1.0.9"]


def test_desktop_client_list_remote_versions_rejects_tampered_signed_manifest(tmp_path: Path) -> None:
    """验证桌面历史版本列表阶段也校验 manifest 签名。"""
    install_root = _write_install_root(tmp_path, version="1.0.0")
    nas_root = tmp_path / "nas"
    key_path = tmp_path / "signature.key"
    key_path.write_text(generate_hmac_key(), encoding="utf-8")
    _write_manifest(nas_root, version="1.0.9", sign_key=key_path)
    version_manifest_path = nas_root / "my-tool" / "stable" / "v1.0.9" / "latest.json"
    payload = json.loads(version_manifest_path.read_text(encoding="utf-8"))
    payload["notes"] = "tampered"
    version_manifest_path.write_text(json.dumps(payload), encoding="utf-8")
    client = _client(install_root=install_root, nas_root=nas_root, signature_key=key_path)

    with pytest.raises(UpdateError) as error:
        client.list_remote_versions()

    assert error.value.code is UpdateErrorCode.MANIFEST_INVALID


def test_desktop_client_installs_remote_version_through_updater(tmp_path: Path) -> None:
    """验证桌面客户端准备远端包并启动标准 updater。"""
    install_root = _write_install_root(tmp_path, version="1.0.0", entry_name="MyTool.exe")
    updater = install_root / "updater" / "MyToolUpdater.exe"
    updater.parent.mkdir()
    updater.write_text("updater", encoding="utf-8")
    nas_root = tmp_path / "nas"
    key_path = tmp_path / "signature.key"
    key_path.write_text(generate_hmac_key(), encoding="utf-8")
    _write_manifest(nas_root, version="1.1.0", content=b"package", sign_key=key_path)
    calls: list[list[str]] = []
    progress_calls: list[tuple[int, int]] = []
    token = CancellationToken()
    client = _client(
        install_root=install_root,
        nas_root=nas_root,
        popen=_popen_recorder(calls),
        signature_key=key_path,
        wait_timeout=12.5,
    )

    result = client.install_remote_version(
        "1.1.0",
        old_pid=123,
        restart=True,
        force=True,
        progress=lambda copied, total: progress_calls.append((copied, total)),
        cancellation_token=token,
    )

    pending_payload = json.loads(client.pending_path().read_text(encoding="utf-8"))
    assert result.started is True
    assert result.updater_pid == 456
    assert result.pending_manifest_path == client.pending_path()
    assert pending_payload["install_root"] == str(install_root)
    assert pending_payload["restart_executable"] == "MyTool.exe"
    assert pending_payload["signature_key"] == str(key_path)
    assert pending_payload["wait_timeout"] == 12.5
    assert Path(pending_payload["package_path"]).read_bytes() == b"package"
    assert progress_calls[-1] == (len(b"package"), len(b"package"))
    assert calls == [
        [
            str(updater),
            "apply",
            "--pending",
            str(client.pending_path()),
            "--restart",
            "--force",
            "--signature-key",
            str(key_path),
            "--entry-name",
            "MyTool.exe",
            "--wait-pid",
            "123",
            "--wait-timeout",
            "12.5",
        ]
    ]


def test_desktop_client_switches_installed_version_through_updater(tmp_path: Path) -> None:
    """验证桌面客户端通过标准 updater 切换本地版本。"""
    install_root = _write_install_root(tmp_path, version="1.0.0")
    updater = install_root / "updater" / "MyToolUpdater.exe"
    updater.parent.mkdir()
    updater.write_text("updater", encoding="utf-8")
    calls: list[list[str]] = []
    client = _client(install_root=install_root, nas_root=tmp_path / "nas", popen=_popen_recorder(calls))

    result = client.switch_installed_version("1.0.9", old_pid=123, restart=True)

    assert result.started is True
    assert result.pending_manifest_path is None
    assert calls == [
        [
            str(updater),
            "switch-installed",
            "--install-root",
            str(install_root),
            "--version",
            "1.0.9",
            "--wait-pid",
            "123",
            "--wait-timeout",
            "60.0",
            "--restart",
        ]
    ]


def test_desktop_client_rolls_back_through_updater(tmp_path: Path) -> None:
    """验证桌面客户端通过标准 updater 回滚本地版本。"""
    install_root = _write_install_root(tmp_path, version="1.0.9")
    updater = install_root / "updater" / "MyToolUpdater.exe"
    updater.parent.mkdir()
    updater.write_text("updater", encoding="utf-8")
    calls: list[list[str]] = []
    client = _client(install_root=install_root, nas_root=tmp_path / "nas", popen=_popen_recorder(calls))

    result = client.rollback(old_pid=123, restart=True)

    assert result.started is True
    assert result.pending_manifest_path is None
    assert calls == [
        [
            str(updater),
            "rollback",
            "--install-root",
            str(install_root),
            "--wait-pid",
            "123",
            "--wait-timeout",
            "60.0",
            "--restart",
        ]
    ]


def test_desktop_client_reads_status_and_result(tmp_path: Path) -> None:
    """验证桌面客户端读取运行态状态和结果。"""
    install_root = _write_install_root(tmp_path, version="1.0.0")
    (install_root / "update-status.json").write_text(json.dumps({"phase": "success"}), encoding="utf-8")
    (install_root / "update-result.json").write_text(json.dumps({"success": True}), encoding="utf-8")
    client = _client(install_root=install_root, nas_root=tmp_path / "nas")

    assert client.read_status()["payload"]["phase"] == "success"
    assert client.read_result()["payload"]["success"] is True


def _client(
    *,
    install_root: Path,
    nas_root: Path,
    popen=None,  # noqa: ANN001
    signature_key: Path | None = None,
    wait_timeout: float = 60.0,
) -> DesktopUpdateClient:
    """创建测试桌面客户端。

    :param install_root: 测试安装根。
    :param nas_root: 测试 NAS 根目录。
    :param popen: 可注入进程启动假函数。
    :param signature_key: 可选签名公钥。
    :param wait_timeout: 等待旧进程退出超时。
    :return: 桌面升级客户端。
    """
    return DesktopUpdateClient(
        DesktopUpdateConfig(
            app_id="my-tool",
            install_root=install_root,
            signature_key=signature_key,
            wait_timeout=wait_timeout,
        ),
        settings=UpdateToolSettings(nas_root=nas_root, updater_executable_name="MyToolUpdater.exe"),
        popen=popen,
    )


def _write_install_root(tmp_path: Path, *, version: str, entry_name: str = "MyTool.exe") -> Path:
    """写入测试安装根。

    :param tmp_path: pytest 临时目录。
    :param version: 当前版本号。
    :param entry_name: release 入口名。
    :return: 安装根路径。
    """
    install_root = tmp_path / "install"
    release_dir = install_root / "releases" / version
    release_dir.mkdir(parents=True)
    (release_dir / entry_name).write_text("app", encoding="utf-8")
    (install_root / "current.json").write_text(
        json.dumps(
            {
                "app_id": "my-tool",
                "version": version,
                "release_dir": f"releases/{version}",
                "executable": entry_name,
                "entry": {"kind": "executable", "path": entry_name, "platform": "windows"},
            }
        ),
        encoding="utf-8",
    )
    return install_root


def _write_manifest(root: Path, *, version: str, content: bytes = b"release", sign_key: Path | None = None) -> None:
    """写入测试 NAS manifest 和包。

    :param root: 测试 NAS 根目录。
    :param version: 版本号。
    :param content: 包体内容。
    :param sign_key: 可选签名密钥。
    :return: None。
    """
    version_dir = root / "my-tool" / "stable" / f"v{version}"
    channel_dir = root / "my-tool" / "stable"
    package = version_dir / "package.zip"
    package.parent.mkdir(parents=True)
    channel_dir.mkdir(parents=True, exist_ok=True)
    package.write_bytes(content)
    payload = {
        "schema_version": 2,
        "app_id": "my-tool",
        "channel": "stable",
        "version": version,
        "mandatory": False,
        "min_supported_version": "1.0.0",
        "published_at": "2026-06-08T00:00:00+00:00",
        "notes": "release",
        "package": {
            "url": f"my-tool/stable/v{version}/package.zip",
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
    }
    if sign_key is not None:
        payload = sign_manifest_payload(payload, key=load_hmac_key(sign_key), key_id="release")
    (channel_dir / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    (version_dir / "latest.json").write_text(json.dumps(payload), encoding="utf-8")


def _popen_recorder(calls: list[list[str]]):
    """创建记录 Popen 参数的假函数。

    :param calls: 用于记录命令参数的列表。
    :return: 假 Popen 函数。
    """

    def popen(args: list[str], cwd: str, close_fds: bool):  # noqa: ANN001
        """记录进程启动参数。

        :param args: 进程命令参数。
        :param cwd: 工作目录。
        :param close_fds: 是否关闭文件描述符。
        :return: 假进程对象。
        """
        calls.append(args)

        class Process:
            """假进程。"""

            pid = 456

        return Process()

    return popen
