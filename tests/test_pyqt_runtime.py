"""PyQt runtime SDK 测试。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from update_online_tool.manifest import UpdateManifest, UpdatePackageInfo
from update_online_tool import launcher as launcher_module
from update_online_tool.pyqt_runtime import (
    PyQtPendingUpdateRequest,
    launch_existing_pending,
    write_pyqt_pending_manifest,
)


def _manifest() -> UpdateManifest:
    """构造测试 manifest。

    :return: manifest 模型。
    """
    return UpdateManifest(
        schema_version=2,
        app_id="automation-manual-studio",
        channel="stable",
        version="1.0.5",
        mandatory=False,
        min_supported_version="1.0.0",
        published_at="2026-06-08T00:00:00+00:00",
        notes="release",
        package=UpdatePackageInfo(
            url="automation-manual-studio/v1.0.5/package.zip",
            size=10,
            sha256="0" * 64,
        ),
    )


def test_write_pyqt_pending_manifest_uses_standard_uot_contract(tmp_path: Path) -> None:
    """验证 pending manifest 使用标准 UOT 契约。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    pending_path = tmp_path / "pending-update.json"

    result = write_pyqt_pending_manifest(
        pending_path=pending_path,
        request=PyQtPendingUpdateRequest(
            package_path=tmp_path / "package.zip",
            manifest=_manifest(),
            install_root=tmp_path,
            old_pid=123,
            restart_executable="AutomationManualStudio.exe",
            from_version="1.0.4",
        ),
    )

    payload = json.loads(result.read_text(encoding="utf-8"))
    assert result == pending_path
    assert payload["from_version"] == "1.0.4"
    assert payload["install_root"] == str(tmp_path)
    assert payload["old_pid"] == 123
    assert payload["package_path"] == str(tmp_path / "package.zip")
    assert payload["restart_executable"] == "AutomationManualStudio.exe"
    assert payload["manifest"]["app_id"] == "automation-manual-studio"
    assert payload["manifest"]["version"] == "1.0.5"


def test_launch_existing_pending_starts_updater_without_rewriting_manifest(
    tmp_path: Path,
) -> None:
    """验证已有 pending 文件可直接交给 updater 进程。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    updater = tmp_path / "AutomationManualUpdater.exe"
    pending = tmp_path / "pending-update.json"
    updater.write_text("fake", encoding="utf-8")
    pending.write_text('{"package_path":"package.zip"}', encoding="utf-8")
    calls: list[list[str]] = []

    def popen(args: list[str], **kwargs: object):  # noqa: ANN001
        """捕获进程启动参数。

        :param args: 命令参数。
        :param kwargs: Popen 关键字参数。
        :return: 假进程。
        """
        assert kwargs["close_fds"] is True
        calls.append(args)

        class Process:
            """假进程。"""

            pid = 456

        return Process()

    result = launch_existing_pending(
        updater_executable=updater, pending_manifest_path=pending, popen=popen
    )

    assert result.started is True
    assert result.updater_pid == 456
    assert result.pending_manifest_path == pending
    assert calls == [[str(updater), "apply", "--pending", str(pending), "--restart"]]


def test_launch_existing_pending_hides_windows_console(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PyQt 兼容入口必须复用统一的 Windows 后台启动合同。

    :param tmp_path: pytest 临时目录。
    :param monkeypatch: pytest monkeypatch 工具。
    :return: None。
    """

    updater = tmp_path / "AutomationManualUpdater.exe"
    pending = tmp_path / "pending-update.json"
    updater.write_text("fake", encoding="utf-8")
    pending.write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    def popen(command: list[str], **kwargs: object) -> object:
        """捕获兼容入口传给 Popen 的参数。

        :param command: updater 命令。
        :param kwargs: Popen 关键字参数。
        :return: 假进程。
        """

        captured["command"] = tuple(command)
        captured.update(kwargs)
        return SimpleNamespace(pid=457)

    monkeypatch.setattr(
        launcher_module,
        "os",
        SimpleNamespace(name="nt", fspath=os.fspath),
    )
    monkeypatch.setattr(
        launcher_module.subprocess,
        "CREATE_NO_WINDOW",
        0x08000000,
        raising=False,
    )

    launch_existing_pending(
        updater_executable=updater,
        pending_manifest_path=pending,
        popen=popen,
    )

    assert int(captured["creationflags"]) & 0x08000000
