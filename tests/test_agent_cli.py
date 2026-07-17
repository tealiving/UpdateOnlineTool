"""Bootstrap 与 Update Agent 窄 CLI 测试。"""

from __future__ import annotations

import json
from pathlib import Path

import update_online_tool.agent_cli as agent_cli
import update_online_tool.bootstrap_cli as bootstrap_cli
from update_online_tool.agent import AgentRunResult, create_apply_request, write_agent_request
from update_online_tool.runtime import RuntimeResult


def test_agent_cli_runs_request_and_returns_json(tmp_path: Path, monkeypatch, capsys) -> None:  # noqa: ANN001
    """打包后的 Agent CLI 对宿主返回可机器读取的成功结果。"""
    request_path = write_agent_request(
        create_apply_request(
            install_root=tmp_path,
            pending_path=tmp_path / "pending-update.json",
            bootstrap_command=("/stable/Product", "launch"),
            operation_id="operation-01",
        )
    )
    runtime_result = RuntimeResult(
        success=True,
        action="install_prepared",
        version="1.1.0",
        previous_version="1.0.0",
        release_dir=tmp_path / "releases" / "1.1.0",
        message="installed",
    )

    class Agent:
        """隔离 CLI 协议的假 Agent。"""

        def run_request(self, path: Path) -> AgentRunResult:
            assert path == request_path
            return AgentRunResult("operation-01", True, runtime_result, 2468)

    monkeypatch.setattr(agent_cli, "UpdateAgent", Agent)

    exit_code = agent_cli.main(["apply", "--request", str(request_path)])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload == {
        "bootstrap_pid": 2468,
        "ok": True,
        "operation_id": "operation-01",
        "version": "1.1.0",
    }


def test_bootstrap_cli_launches_current_release(tmp_path: Path, monkeypatch, capsys) -> None:  # noqa: ANN001
    """稳定 Bootstrap CLI 只按 current.json 入口启动当前 release。"""
    install_root = tmp_path / "install"
    install_root.mkdir()

    class Process:
        """假 Bootstrap 启动的 release 进程。"""

        pid = 1357

    expected_root = install_root

    def launch_current(*, install_root: Path) -> Process:
        assert install_root == expected_root
        return Process()

    monkeypatch.setattr(bootstrap_cli, "launch_current", launch_current)

    exit_code = bootstrap_cli.main(["launch", "--install-root", str(install_root)])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert payload == {"ok": True, "release_pid": 1357}
