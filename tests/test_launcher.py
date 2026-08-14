"""updater 启动器测试。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from update_online_tool import UpdateError, UpdateErrorCode
from update_online_tool import launcher as launcher_module
from update_online_tool.launcher import (
    StandaloneUpdaterLauncher,
    launch_updater_process,
)


def test_launcher_writes_pending_manifest_and_starts_process(tmp_path: Path) -> None:
    """验证 launcher 写入 pending manifest 并调用进程启动函数。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    updater = tmp_path / "AutomationManualUpdater.exe"
    updater.write_text("fake", encoding="utf-8")
    calls: list[list[str]] = []

    def popen(args: list[str], **kwargs: object):  # noqa: ANN001
        """捕获 Popen 参数。

        :param args: 命令参数。
        :param kwargs: Popen 关键字参数。
        :return: 假进程。
        """
        assert kwargs["close_fds"] is True
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
    assert (
        json.loads((tmp_path / "pending-update.json").read_text(encoding="utf-8"))[
            "package_path"
        ]
        == "package.zip"
    )
    assert calls == [
        [
            str(updater),
            "apply",
            "--pending",
            str(tmp_path / "pending-update.json"),
            "--restart",
        ]
    ]


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


def test_launcher_stages_sidecar_before_starting_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证 sidecar updater 从临时副本运行，以便安装过程替换原目录。"""
    updater = tmp_path / "install" / "updater" / "MyToolUpdater" / "MyToolUpdater.exe"
    updater.parent.mkdir(parents=True)
    updater.write_text("updater", encoding="utf-8")
    (updater.parent / "_internal").mkdir()
    (updater.parent / "_internal" / "runtime.dll").write_text(
        "runtime", encoding="utf-8"
    )
    pending = tmp_path / "install" / "pending-update.json"
    stage_root = tmp_path / "stage"
    stage_root.mkdir()
    monkeypatch.setattr(
        "update_online_tool.launcher.tempfile.mkdtemp", lambda prefix: str(stage_root)
    )
    calls: list[list[str]] = []

    def popen(args: list[str], **kwargs: object):  # noqa: ANN001
        assert kwargs["close_fds"] is True
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
    assert (staged_updater.parent / "_internal" / "runtime.dll").read_text(
        encoding="utf-8"
    ) == "runtime"


def test_launcher_rejects_updater_that_exits_during_startup(tmp_path: Path) -> None:
    """updater 在启动窗口内异常退出时不得报告启动成功。

    :param tmp_path: pytest 临时目录。
    :return: None。
    """

    updater = tmp_path / "AutomationManualUpdater"
    updater.write_text("fake", encoding="utf-8")

    def popen(args: list[str], **kwargs: object):  # noqa: ANN001
        """返回立即失败的 updater 进程。

        :param args: 启动命令。
        :param kwargs: Popen 关键字参数。
        :return: 失败进程。
        """

        del args, kwargs

        class Process:
            """模拟启动后立即退出的进程。"""

            pid = 456

            def wait(self, timeout: float | None = None) -> int:
                """返回启动失败退出码。

                :param timeout: 启动探测窗口。
                :return: 退出码 255。
                """

                assert timeout is not None
                return 255

        return Process()

    with pytest.raises(UpdateError) as error:
        StandaloneUpdaterLauncher(updater, popen=popen).launch(
            pending_payload={"package_path": "package.zip"},
            pending_manifest_path=tmp_path / "pending-update.json",
        )

    assert error.value.code is UpdateErrorCode.UPDATER_LAUNCH_FAILED
    assert "exit code 255" in str(error.value)


def test_launcher_rejects_zero_exit_before_old_process_handoff(tmp_path: Path) -> None:
    """等待旧 GUI 时 updater 即使返回零也不得伪装为交接成功。"""
    updater = tmp_path / "AutomationManualUpdater"
    updater.write_text("fake", encoding="utf-8")

    def popen(args: list[str], **kwargs: object):  # noqa: ANN001
        """返回立即正常退出但未等待旧进程的 updater。"""
        del args, kwargs

        class Process:
            """模拟错误吞掉命令后返回零的 updater。"""

            pid = 457

            def wait(self, timeout: float | None = None) -> int:
                """立即返回零。"""
                assert timeout is not None
                return 0

        return Process()

    with pytest.raises(UpdateError) as error:
        StandaloneUpdaterLauncher(updater, popen=popen).launch(
            pending_payload={"package_path": "package.zip", "old_pid": 123},
            pending_manifest_path=tmp_path / "pending-update.json",
        )

    assert error.value.code is UpdateErrorCode.UPDATER_LAUNCH_FAILED
    assert "before old process handoff" in str(error.value)


def test_launch_updater_accepts_process_running_after_probe(tmp_path: Path) -> None:
    """真实 Popen 风格的存活进程应通过启动探测。"""
    updater = tmp_path / "AutomationManualUpdater"
    updater.write_text("fake", encoding="utf-8")

    class Process:
        """模拟启动后持续运行的进程。"""

        def wait(self, timeout: float | None = None) -> int:
            """用 TimeoutExpired 表示探测窗口内仍在运行。"""
            raise subprocess.TimeoutExpired(str(updater), timeout)

    process = Process()

    result = launch_updater_process(
        [str(updater), "--help"],
        updater_executable=updater,
        popen=lambda *args, **kwargs: process,
        require_running=True,
    )

    assert result is process


def test_launch_updater_hides_console_on_windows(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """程序化启动 Windows updater 时不得创建可见控制台。

    :param tmp_path: pytest 临时目录。
    :param monkeypatch: pytest monkeypatch 工具。
    :return: None。
    """

    updater = tmp_path / "AutomationManualUpdater.exe"
    updater.write_text("fake", encoding="utf-8")
    captured: dict[str, object] = {}

    class Process:
        """提供启动探测所需的最小进程形状。"""

        pid = 458

    def popen(command: list[str], **kwargs: object) -> Process:
        """捕获 updater 的平台启动参数。

        :param command: updater 命令。
        :param kwargs: Popen 关键字参数。
        :return: 假进程。
        """

        captured["command"] = tuple(command)
        captured.update(kwargs)
        return Process()

    monkeypatch.setattr(
        launcher_module,
        "os",
        SimpleNamespace(name="nt", fspath=os.fspath),
        raising=False,
    )
    monkeypatch.setattr(
        launcher_module.subprocess,
        "CREATE_NO_WINDOW",
        0x08000000,
        raising=False,
    )

    launch_updater_process(
        [str(updater), "apply"],
        updater_executable=updater,
        popen=popen,
    )

    assert int(captured["creationflags"]) & 0x08000000


@pytest.mark.skipif(os.name != "nt", reason="需要真实 Windows console subsystem")
def test_launch_updater_has_no_console_window_on_windows(tmp_path: Path) -> None:
    """程序化启动的真实 Windows 子进程不得获得控制台窗口。

    :param tmp_path: pytest 临时目录。
    :return: None。
    """

    result_path = tmp_path / "console-window.txt"
    child_code = (
        "import ctypes,pathlib;"
        f"pathlib.Path({str(result_path)!r}).write_text("
        "str(int(bool(ctypes.windll.kernel32.GetConsoleWindow()))),encoding='utf-8')"
    )

    process = launch_updater_process(
        [sys.executable, "-c", child_code],
        updater_executable=Path(sys.executable),
    )

    assert process.wait(timeout=10.0) == 0
    assert result_path.read_text(encoding="utf-8") == "0"


def test_launch_updater_converts_popen_oserror_to_update_error(tmp_path: Path) -> None:
    """Popen 无法启动时应返回 UPDATER_LAUNCH_FAILED。"""
    updater = tmp_path / "AutomationManualUpdater"
    updater.write_text("fake", encoding="utf-8")

    def popen(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        """模拟操作系统拒绝启动 updater。"""
        raise OSError("permission denied")

    with pytest.raises(UpdateError) as error:
        launch_updater_process(
            [str(updater), "--help"],
            updater_executable=updater,
            popen=popen,
        )

    assert error.value.code is UpdateErrorCode.UPDATER_LAUNCH_FAILED


def test_launch_updater_converts_incompatible_popen_contract_to_update_error(
    tmp_path: Path,
) -> None:
    """Popen 不接受后台参数时必须结构化失败且禁止兼容降级。

    :param tmp_path: pytest 临时目录。
    :return: None。
    """

    updater = tmp_path / "AutomationManualUpdater"
    updater.write_text("fake", encoding="utf-8")

    def popen(*args: object, **kwargs: object) -> object:
        """模拟旧进程工厂拒绝标准 Popen 参数。

        :param args: Popen 位置参数。
        :param kwargs: Popen 关键字参数。
        :return: 不返回，始终抛出异常。
        """

        del args, kwargs
        raise TypeError("creationflags is unsupported")

    with pytest.raises(UpdateError) as error:
        launch_updater_process(
            [str(updater), "--help"],
            updater_executable=updater,
            popen=popen,
        )

    assert error.value.code is UpdateErrorCode.UPDATER_LAUNCH_FAILED


def test_launcher_passes_restart_executable_as_entry_name(tmp_path: Path) -> None:
    """验证 pending 重启入口会传递给标准 updater。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    updater = tmp_path / "AutomationManualUpdater"
    pending = tmp_path / "pending-update.json"
    updater.write_text("fake", encoding="utf-8")
    calls: list[list[str]] = []

    def popen(args: list[str], **kwargs: object):  # noqa: ANN001
        """捕获 Popen 参数。

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

    def popen(args: list[str], **kwargs: object):  # noqa: ANN001
        """捕获 Popen 参数。

        :param args: 命令参数。
        :param kwargs: Popen 关键字参数。
        :return: 假进程。
        """
        assert kwargs["close_fds"] is True
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
