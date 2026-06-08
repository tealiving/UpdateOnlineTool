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
    assert calls == [[str(updater), "--pending", str(tmp_path / "pending-update.json")]]


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
