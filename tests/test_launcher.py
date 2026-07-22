"""updater 启动器测试。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from update_online_tool import UpdateError, UpdateErrorCode
from update_online_tool.launcher import StandaloneUpdaterLauncher


def test_launcher_writes_pending_manifest_and_starts_process(tmp_path: Path) -> None:
    """验证 launcher 写入 pending manifest 并调用进程启动函数。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    updater = tmp_path / "AutomationManualUpdater.exe"
    updater.write_text("fake", encoding="utf-8")
    calls: list[list[str]] = []

    def popen(args: list[str], cwd: str, close_fds: bool):  # noqa: ANN001
        """捕获 Popen 参数。

        :param args: 命令参数。
        :param cwd: 工作目录。
        :param close_fds: 是否关闭文件描述符。
        :return: 假进程。
        """
        calls.append(args)

        class Process:
            """假进程。"""

            pid = 123

        return Process()

    result = StandaloneUpdaterLauncher(updater, popen=popen).launch(
        pending_payload={"package_path": "package.zip"},
        pending_manifest_path=tmp_path / "pending-update.json",
    )

    assert result.started is True
    assert result.updater_pid == 123
    assert json.loads((tmp_path / "pending-update.json").read_text(encoding="utf-8"))["package_path"] == "package.zip"
    assert calls == [[str(updater), "apply", "--pending", str(tmp_path / "pending-update.json"), "--restart"]]


def test_launcher_rejects_missing_updater(tmp_path: Path) -> None:
    """验证 updater exe 缺失时返回结构化错误。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    with pytest.raises(UpdateError) as error:
        StandaloneUpdaterLauncher(tmp_path / "missing.exe").launch(
            pending_payload={},
            pending_manifest_path=tmp_path / "pending.json",
        )

    assert error.value.code is UpdateErrorCode.UPDATER_NOT_FOUND


def test_launcher_stages_sidecar_before_starting_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """验证 sidecar updater 从临时副本运行，以便安装过程替换原目录。"""
    updater = tmp_path / "install" / "updater" / "MyToolUpdater" / "MyToolUpdater.exe"
    updater.parent.mkdir(parents=True)
    updater.write_text("updater", encoding="utf-8")
    (updater.parent / "_internal").mkdir()
    (updater.parent / "_internal" / "runtime.dll").write_text("runtime", encoding="utf-8")
    pending = tmp_path / "install" / "pending-update.json"
    stage_root = tmp_path / "stage"
    stage_root.mkdir()
    monkeypatch.setattr("update_online_tool.launcher.tempfile.mkdtemp", lambda prefix: str(stage_root))
    calls: list[list[str]] = []

    def popen(args: list[str], cwd: str, close_fds: bool):  # noqa: ANN001
        calls.append(args)

        class Process:
            pid = 321

        return Process()

    StandaloneUpdaterLauncher(updater, popen=popen).launch(
        pending_payload={"package_path": "package.zip"},
        pending_manifest_path=pending,
    )

    staged_updater = stage_root / "updater" / "MyToolUpdater" / "MyToolUpdater.exe"
    assert calls[0][0] == str(staged_updater)
    assert staged_updater.read_text(encoding="utf-8") == "updater"
    assert (staged_updater.parent / "_internal" / "runtime.dll").read_text(encoding="utf-8") == "runtime"


def test_launcher_passes_restart_executable_as_entry_name(tmp_path: Path) -> None:
    """验证 pending 重启入口会传递给标准 updater。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    updater = tmp_path / "AutomationManualUpdater"
    pending = tmp_path / "pending-update.json"
    updater.write_text("fake", encoding="utf-8")
    calls: list[list[str]] = []

    def popen(args: list[str], cwd: str, close_fds: bool):  # noqa: ANN001
        """捕获 Popen 参数。

        :param args: 命令参数。
        :param cwd: 工作目录。
        :param close_fds: 是否关闭文件描述符。
        :return: 假进程。
        """
        calls.append(args)

        class Process:
            """假进程。"""

            pid = 456

        return Process()

    StandaloneUpdaterLauncher(updater, popen=popen).launch(
        pending_payload={
            "package_path": "package.zip",
            "restart_executable": "AutomationManualStudio.app",
            "old_pid": 123,
        },
        pending_manifest_path=pending,
    )

    assert calls == [
        [
            str(updater),
            "apply",
            "--pending",
            str(pending),
            "--restart",
            "--entry-name",
            "AutomationManualStudio.app",
            "--wait-pid",
            "123",
        ]
    ]


def test_launcher_passes_signature_key_and_wait_timeout(tmp_path: Path) -> None:
    """验证 launcher 会把验签密钥和等待超时传给 updater。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    updater = tmp_path / "AutomationManualUpdater"
    pending = tmp_path / "pending-update.json"
    signature_key = tmp_path / "uot-signing.pub"
    updater.write_text("fake", encoding="utf-8")
    signature_key.write_text("public", encoding="utf-8")
    calls: list[list[str]] = []

    def popen(args: list[str], cwd: str, close_fds: bool):  # noqa: ANN001
        """捕获 Popen 参数。

        :param args: 命令参数。
        :param cwd: 工作目录。
        :param close_fds: 是否关闭文件描述符。
        :return: 假进程。
        """
        calls.append(args)

        class Process:
            """假进程。"""

            pid = 789

        return Process()

    StandaloneUpdaterLauncher(updater, popen=popen).launch(
        pending_payload={
            "package_path": "package.zip",
            "signature_key": str(signature_key),
            "old_pid": 123,
            "wait_timeout": 12.5,
        },
        pending_manifest_path=pending,
    )

    assert calls == [
        [
            str(updater),
            "apply",
            "--pending",
            str(pending),
            "--restart",
            "--signature-key",
            str(signature_key),
            "--wait-pid",
            "123",
            "--wait-timeout",
            "12.5",
        ]
    ]
