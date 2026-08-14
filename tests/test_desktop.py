"""桌面升级客户端测试。"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from update_online_tool import desktop as desktop_module
from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.downloader import CancellationToken
from update_online_tool.desktop import DesktopUpdateClient, DesktopUpdateConfig
from update_online_tool.settings import UpdateToolSettings
from update_online_tool.signature import (
    generate_hmac_key,
    load_hmac_key,
    sign_manifest_payload,
)
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


def test_desktop_client_normalizes_release_dir_install_root(tmp_path: Path) -> None:
    """验证误传 release 目录时桌面客户端可回到安装根。

    :param tmp_path: pytest 临时目录
    :return: None
    """
    install_root = _write_install_root(tmp_path, version="1.0.0")
    client = _client(
        install_root=install_root / "releases" / "1.0.0", nas_root=tmp_path / "nas"
    )

    assert client.install_root() == install_root
    assert client.current_version() == "1.0.0"


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
    client = _client(
        install_root=install_root, nas_root=nas_root, signature_key=key_path
    )

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


def test_desktop_client_list_remote_versions_rejects_tampered_signed_manifest(
    tmp_path: Path,
) -> None:
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
    client = _client(
        install_root=install_root, nas_root=nas_root, signature_key=key_path
    )

    with pytest.raises(UpdateError) as error:
        client.list_remote_versions()

    assert error.value.code is UpdateErrorCode.MANIFEST_INVALID


def test_desktop_client_installs_remote_version_through_updater(tmp_path: Path) -> None:
    """验证桌面客户端准备远端包并启动标准 updater。"""
    install_root = _write_install_root(
        tmp_path, version="1.0.0", entry_name="MyTool.exe"
    )
    updater = install_root / "updater" / "MyToolUpdater.exe"
    updater.parent.mkdir()
    updater.write_text("updater", encoding="utf-8")
    nas_root = tmp_path / "nas"
    key_path = tmp_path / "signature.key"
    key_path.write_text(generate_hmac_key(), encoding="utf-8")
    source_package = _write_manifest(
        nas_root,
        version="1.1.0",
        content=b"package",
        sign_key=key_path,
    )
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
    assert (
        Path(pending_payload["package_path"]).read_bytes()
        == source_package.read_bytes()
    )
    assert progress_calls[-1] == (
        source_package.stat().st_size,
        source_package.stat().st_size,
    )
    assert Path(calls[0][0]).name == updater.name
    assert calls[0][1:] == [
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


def test_desktop_client_can_prepare_before_starting_updater(tmp_path: Path) -> None:
    """宿主可先准备 pending，再在退出协调完成后启动 updater。"""
    install_root = _write_install_root(
        tmp_path, version="1.0.0", entry_name="MyTool.exe"
    )
    updater = install_root / "updater" / "MyToolUpdater.exe"
    updater.parent.mkdir()
    updater.write_text("updater", encoding="utf-8")
    nas_root = tmp_path / "nas"
    _write_manifest(nas_root, version="1.1.0", content=b"package")
    calls: list[list[str]] = []
    client = _client(
        install_root=install_root, nas_root=nas_root, popen=_popen_recorder(calls)
    )

    prepared = client.prepare_remote_version("1.1.0", old_pid=123, restart=True)

    assert prepared.version == "1.1.0"
    with zipfile.ZipFile(prepared.package_path) as archive:
        assert archive.read("payload.bin") == b"package"
    assert prepared.pending_manifest_path == client.pending_path()
    assert calls == []

    result = client.launch_pending_update()

    assert result.started is True
    assert Path(calls[0][0]).name == updater.name
    assert calls[0][1:] == [
        "apply",
        "--pending",
        str(client.pending_path()),
        "--restart",
        "--entry-name",
        "MyTool.exe",
        "--wait-pid",
        "123",
        "--wait-timeout",
        "60.0",
    ]


def test_desktop_client_prepares_for_agent_handoff_without_updater(
    tmp_path: Path,
) -> None:
    """Agent 模式准备 pending 时不应依赖旧 updater sidecar。"""
    install_root = _write_install_root(
        tmp_path, version="1.0.0", entry_name="MyTool.exe"
    )
    nas_root = tmp_path / "nas"
    _write_manifest(nas_root, version="1.1.0", content=b"package")
    client = _client(install_root=install_root, nas_root=nas_root)

    prepared = client.prepare_remote_version("1.1.0", old_pid=123, restart=True)

    assert prepared.version == "1.1.0"
    with zipfile.ZipFile(prepared.package_path) as archive:
        assert archive.read("payload.bin") == b"package"
    assert prepared.pending_manifest_path == client.pending_path()
    assert client.pending_path().is_file()
    with pytest.raises(UpdateError) as error:
        client.launch_pending_update()
    assert error.value.code is UpdateErrorCode.UPDATER_NOT_FOUND


def test_desktop_client_switches_installed_version_through_updater(
    tmp_path: Path,
) -> None:
    """验证桌面客户端通过标准 updater 切换本地版本。"""
    install_root = _write_install_root(tmp_path, version="1.0.0")
    updater = install_root / "updater" / "MyToolUpdater.exe"
    updater.parent.mkdir()
    updater.write_text("updater", encoding="utf-8")
    calls: list[list[str]] = []
    client = _client(
        install_root=install_root,
        nas_root=tmp_path / "nas",
        popen=_popen_recorder(calls),
    )

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


def test_desktop_client_switch_rejects_updater_that_exits_during_startup(
    tmp_path: Path,
) -> None:
    """本地切换时 updater 立即失败不得被报告为启动成功。"""
    install_root = _write_install_root(tmp_path, version="1.0.0")
    updater = install_root / "updater" / "MyToolUpdater.exe"
    updater.parent.mkdir()
    updater.write_text("updater", encoding="utf-8")
    client = _client(
        install_root=install_root,
        nas_root=tmp_path / "nas",
        popen=_popen_immediate_failure,
    )

    with pytest.raises(UpdateError) as error:
        client.switch_installed_version("1.0.9", old_pid=123, restart=True)

    assert error.value.code is UpdateErrorCode.UPDATER_LAUNCH_FAILED
    assert "exit code 255" in str(error.value)


def test_desktop_client_switch_rejects_zero_exit_before_old_process_handoff(
    tmp_path: Path,
) -> None:
    """本地切换等待旧 GUI 时不得接受 updater 提前返回零。"""
    install_root = _write_install_root(tmp_path, version="1.0.0")
    updater = install_root / "updater" / "MyToolUpdater.exe"
    updater.parent.mkdir()
    updater.write_text("updater", encoding="utf-8")
    client = _client(
        install_root=install_root,
        nas_root=tmp_path / "nas",
        popen=_popen_immediate_success,
    )

    with pytest.raises(UpdateError) as error:
        client.switch_installed_version("1.0.9", old_pid=123, restart=True)

    assert error.value.code is UpdateErrorCode.UPDATER_LAUNCH_FAILED
    assert "before old process handoff" in str(error.value)


def test_desktop_client_rolls_back_through_updater(tmp_path: Path) -> None:
    """验证桌面客户端通过标准 updater 回滚本地版本。"""
    install_root = _write_install_root(tmp_path, version="1.0.9")
    updater = install_root / "updater" / "MyToolUpdater.exe"
    updater.parent.mkdir()
    updater.write_text("updater", encoding="utf-8")
    calls: list[list[str]] = []
    client = _client(
        install_root=install_root,
        nas_root=tmp_path / "nas",
        popen=_popen_recorder(calls),
    )

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


def test_desktop_client_rollback_rejects_updater_that_exits_during_startup(
    tmp_path: Path,
) -> None:
    """回滚时 updater 立即失败不得被报告为启动成功。"""
    install_root = _write_install_root(tmp_path, version="1.0.0")
    updater = install_root / "updater" / "MyToolUpdater.exe"
    updater.parent.mkdir()
    updater.write_text("updater", encoding="utf-8")
    client = _client(
        install_root=install_root,
        nas_root=tmp_path / "nas",
        popen=_popen_immediate_failure,
    )

    with pytest.raises(UpdateError) as error:
        client.rollback(old_pid=123, restart=True)

    assert error.value.code is UpdateErrorCode.UPDATER_LAUNCH_FAILED
    assert "exit code 255" in str(error.value)


def test_desktop_client_rollback_rejects_zero_exit_before_old_process_handoff(
    tmp_path: Path,
) -> None:
    """回滚等待旧 GUI 时不得接受 updater 提前返回零。"""
    install_root = _write_install_root(tmp_path, version="1.0.0")
    updater = install_root / "updater" / "MyToolUpdater.exe"
    updater.parent.mkdir()
    updater.write_text("updater", encoding="utf-8")
    client = _client(
        install_root=install_root,
        nas_root=tmp_path / "nas",
        popen=_popen_immediate_success,
    )

    with pytest.raises(UpdateError) as error:
        client.rollback(old_pid=123, restart=True)

    assert error.value.code is UpdateErrorCode.UPDATER_LAUNCH_FAILED
    assert "before old process handoff" in str(error.value)


def test_desktop_client_prewarms_updater(tmp_path: Path) -> None:
    """验证桌面客户端可预热标准 updater。"""
    install_root = _write_install_root(tmp_path, version="1.0.0")
    updater = install_root / "updater" / "MyToolUpdater.exe"
    updater.parent.mkdir()
    updater.write_text("updater", encoding="utf-8")
    calls: list[tuple[list[str], dict[str, object]]] = []
    client = _client(
        install_root=install_root,
        nas_root=tmp_path / "nas",
        popen=_popen_wait_recorder(calls),
    )

    elapsed = client.prewarm_updater(timeout=12.5)

    assert elapsed >= 0.0
    assert calls
    command, kwargs = calls[0]
    assert command == [str(updater), "--help"]
    assert kwargs["cwd"] == str(updater.parent)
    assert kwargs["close_fds"] is True
    assert "stdout" in kwargs
    assert "stderr" in kwargs


def test_desktop_client_prewarm_fails_closed_when_popen_rejects_background_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """后台参数不兼容时不得退回可能弹窗的 updater 启动。

    :param tmp_path: pytest 临时目录。
    :param monkeypatch: pytest monkeypatch 工具。
    :return: None。
    """

    install_root = _write_install_root(tmp_path, version="1.0.0")
    updater = install_root / "updater" / "MyToolUpdater.exe"
    updater.parent.mkdir()
    updater.write_text("updater", encoding="utf-8")
    calls: list[dict[str, object]] = []

    def popen(args: list[str], **kwargs: object) -> object:
        """模拟不支持 Windows 后台参数的旧进程工厂。

        :param args: 进程参数。
        :param kwargs: Popen 关键字参数。
        :return: 假进程。
        """

        del args
        calls.append(dict(kwargs))
        if "creationflags" in kwargs:
            raise TypeError("creationflags is unsupported")
        return _popen_immediate_success([], **kwargs)

    monkeypatch.setattr(
        desktop_module,
        "background_process_creation_kwargs",
        lambda: {"creationflags": 0x08000000},
    )
    client = _client(
        install_root=install_root,
        nas_root=tmp_path / "nas",
        popen=popen,
    )

    with pytest.raises(UpdateError) as error:
        client.prewarm_updater()

    assert error.value.code is UpdateErrorCode.UPDATER_LAUNCH_FAILED
    assert len(calls) == 1
    assert calls[0]["creationflags"] == 0x08000000


def test_desktop_client_prewarm_rejects_nonzero_updater_exit(tmp_path: Path) -> None:
    """updater 无法加载运行时时，预热必须返回结构化失败。

    :param tmp_path: pytest 临时目录。
    :return: 测试 ZIP 路径。
    """

    install_root = _write_install_root(tmp_path, version="1.0.0")
    updater = install_root / "updater" / "MyToolUpdater.exe"
    updater.parent.mkdir()
    updater.write_text("updater", encoding="utf-8")

    def popen(args: list[str], **kwargs: object):  # noqa: ANN001
        """返回模拟 PyInstaller runtime 加载失败的进程。

        :param args: 进程参数。
        :param kwargs: Popen 关键字参数。
        :return: 失败进程。
        """

        del args, kwargs

        class Process:
            """模拟立即以 255 退出的 updater。"""

            returncode = 255

            def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
                """模拟进程结束。

                :param timeout: 等待超时。
                :return: 空输出。
                """

                del timeout
                return b"", b""

        return Process()

    client = _client(install_root=install_root, nas_root=tmp_path / "nas", popen=popen)

    with pytest.raises(UpdateError) as error:
        client.prewarm_updater()

    assert error.value.code is UpdateErrorCode.UPDATER_LAUNCH_FAILED
    assert "exit code 255" in str(error.value)


def test_desktop_client_prewarms_updater_from_release_dir_install_root(
    tmp_path: Path,
) -> None:
    """验证误传 release 目录时 updater 预热仍定位安装根 sidecar。

    :param tmp_path: pytest 临时目录
    :return: None
    """
    install_root = _write_install_root(tmp_path, version="1.0.0")
    updater = install_root / "updater" / "MyToolUpdater.exe"
    updater.parent.mkdir()
    updater.write_text("updater", encoding="utf-8")
    calls: list[tuple[list[str], dict[str, object]]] = []
    client = _client(
        install_root=install_root / "releases" / "1.0.0",
        nas_root=tmp_path / "nas",
        popen=_popen_wait_recorder(calls),
    )

    client.prewarm_updater()

    command, kwargs = calls[0]
    assert command == [str(updater), "--help"]
    assert kwargs["cwd"] == str(updater.parent)


def test_desktop_client_prewarm_reports_release_dir_hint_when_updater_missing(
    tmp_path: Path,
) -> None:
    """验证 updater 缺失时保留 release 目录误传诊断。

    :param tmp_path: pytest 临时目录
    :return: None
    """
    install_root = _write_install_root(tmp_path, version="1.0.0")
    client = _client(
        install_root=install_root / "releases" / "1.0.0", nas_root=tmp_path / "nas"
    )

    with pytest.raises(UpdateError) as error:
        client.prewarm_updater()

    assert error.value.code is UpdateErrorCode.UPDATER_NOT_FOUND
    assert "version release directory" in str(error.value)
    assert str(install_root / "updater") in str(error.value)


def test_desktop_client_reads_status_and_result(tmp_path: Path) -> None:
    """验证桌面客户端读取运行态状态和结果。"""
    install_root = _write_install_root(tmp_path, version="1.0.0")
    (install_root / "update-status.json").write_text(
        json.dumps({"phase": "success"}), encoding="utf-8"
    )
    (install_root / "update-result.json").write_text(
        json.dumps({"success": True}), encoding="utf-8"
    )
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
        settings=UpdateToolSettings(
            nas_root=nas_root, updater_executable_name="MyToolUpdater.exe"
        ),
        popen=popen,
    )


def _write_install_root(
    tmp_path: Path, *, version: str, entry_name: str = "MyTool.exe"
) -> Path:
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
                "entry": {
                    "kind": "executable",
                    "path": entry_name,
                    "platform": "windows",
                },
            }
        ),
        encoding="utf-8",
    )
    return install_root


def _write_manifest(
    root: Path,
    *,
    version: str,
    content: bytes = b"release",
    sign_key: Path | None = None,
) -> Path:
    """写入测试 NAS manifest 和包。

    :param root: 测试 NAS 根目录。
    :param version: 版本号。
    :param content: 包体内容。
    :param sign_key: 可选签名密钥。
    :return: 测试 ZIP 路径。
    """
    version_dir = root / "my-tool" / "stable" / f"v{version}"
    channel_dir = root / "my-tool" / "stable"
    package = version_dir / "package.zip"
    package.parent.mkdir(parents=True)
    channel_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("payload.bin", content)
    package_bytes = package.read_bytes()
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
            "size": len(package_bytes),
            "sha256": hashlib.sha256(package_bytes).hexdigest(),
        },
    }
    if sign_key is not None:
        payload = sign_manifest_payload(
            payload, key=load_hmac_key(sign_key), key_id="release"
        )
    (channel_dir / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    (version_dir / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    return package


def _popen_recorder(calls: list[list[str]]):
    """创建记录 Popen 参数的假函数。

    :param calls: 用于记录命令参数的列表。
    :return: 假 Popen 函数。
    """

    def popen(args: list[str], **kwargs: object):  # noqa: ANN001
        """记录进程启动参数。

        :param args: 进程命令参数。
        :param kwargs: Popen 关键字参数。
        :return: 假进程对象。
        """
        assert kwargs["close_fds"] is True
        calls.append(args)

        class Process:
            """假进程。"""

            pid = 456

        return Process()

    return popen


def _popen_immediate_failure(*args: object, **kwargs: object):  # noqa: ANN202
    """返回启动后立即以非零状态退出的 updater 进程。"""
    del args, kwargs

    class Process:
        """模拟缺失运行库的 updater。"""

        pid = 999

        def wait(self, timeout: float | None = None) -> int:
            """返回启动失败状态。"""
            assert timeout is not None
            return 255

    return Process()


def _popen_immediate_success(*args: object, **kwargs: object):  # noqa: ANN202
    """返回启动后立即以零状态退出的 updater 进程。"""
    del args, kwargs

    class Process:
        """模拟错误吞掉命令但返回零的 updater。"""

        pid = 1000

        def wait(self, timeout: float | None = None) -> int:
            """返回伪成功状态。"""
            assert timeout is not None
            return 0

    return Process()


def _popen_wait_recorder(calls: list[tuple[list[str], dict[str, object]]]):
    """创建记录 Popen 参数并支持 wait/communicate 的假函数。"""

    def popen(args: list[str], **kwargs: object):  # noqa: ANN001
        """记录进程启动参数。"""
        calls.append((args, kwargs))

        class Process:
            """假进程。"""

            pid = 456

            def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
                """模拟进程正常结束。"""
                return b"", b""

        return Process()

    return popen
