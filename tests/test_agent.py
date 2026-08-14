"""独立 Update Agent 的进程交接测试。"""

from __future__ import annotations

import json
import hashlib
import os
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

from update_online_tool.agent import (
    UpdateAgent,
    UpdateAgentLauncher,
    create_apply_request,
    create_switch_request,
    read_agent_status,
    write_agent_handoff,
    write_agent_request,
)
from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.manifest import UpdateManifest
from update_online_tool.runtime import RuntimeResult


def test_agent_marks_ready_before_applying_and_restarts_bootstrap(
    tmp_path: Path,
) -> None:
    """Agent 就绪后才应用 pending，并且只重启稳定 Bootstrap。"""
    install_root = tmp_path / "install"
    install_root.mkdir()
    pending_path = install_root / "pending-update.json"
    pending_path.write_text("{}", encoding="utf-8")
    request = create_apply_request(
        install_root=install_root,
        pending_path=pending_path,
        old_pid=4321,
        wait_timeout=12.5,
        bootstrap_command=(
            "/stable/Product",
            "launch",
            "--install-root",
            str(install_root),
        ),
        operation_id="update-001",
    )
    request_path = write_agent_request(request)
    events: list[str] = []
    captured_apply: dict[str, object] = {}

    def wait_for_handoff(path: Path, request: object) -> None:
        """模拟宿主确认退出前，Agent 保持 ready 状态。"""
        events.append("ready")
        assert path == request_path
        assert read_agent_status(request_path)["phase"] == "ready"

    def apply_pending(**kwargs: object) -> RuntimeResult:
        """断言 Agent 在接收交接确认后才开始安装。"""
        events.append("apply")
        captured_apply.update(kwargs)
        assert read_agent_status(request_path)["phase"] == "applying"
        return RuntimeResult(
            success=True,
            action="install_prepared",
            version="1.1.0",
            previous_version="1.0.0",
            release_dir=install_root / "releases" / "1.1.0",
            message="installed",
        )

    def launch_bootstrap(command: tuple[str, ...], cwd: Path) -> int:
        """记录稳定入口命令，避免直接启动版本化 release。"""
        events.append("bootstrap")
        assert command == request.bootstrap_command
        assert cwd == install_root
        return 9876

    result = UpdateAgent(
        apply_pending=apply_pending,
        launch_bootstrap=launch_bootstrap,
        wait_for_handoff=wait_for_handoff,
    ).run_request(request_path)

    assert events == ["ready", "apply", "bootstrap"]
    assert captured_apply == {
        "pending_path": pending_path,
        "wait_pid": 4321,
        "wait_timeout": 12.5,
        "restart": False,
    }
    assert result.success is True
    assert result.operation_id == "update-001"
    assert result.bootstrap_pid == 9876
    assert read_agent_status(request_path)["phase"] == "success"


def test_agent_request_is_auditable_json_without_secrets(tmp_path: Path) -> None:
    """宿主与 Agent 的 durable request 只保存更新交接所需信息。"""
    install_root = tmp_path / "install"
    install_root.mkdir()
    request = create_apply_request(
        install_root=install_root,
        pending_path=install_root / "pending-update.json",
        bootstrap_command=("/stable/Product", "launch"),
        operation_id="update-002",
    )

    request_path = write_agent_request(request)

    payload = json.loads(request_path.read_text(encoding="utf-8"))
    assert request_path == install_root / "operations" / "update-002.request.json"
    assert payload == {
        "action": "apply",
        "bootstrap_command": ["/stable/Product", "launch"],
        "install_root": str(install_root),
        "handoff_timeout": 30.0,
        "old_pid": None,
        "operation_id": "update-002",
        "pending_path": str(install_root / "pending-update.json"),
        "schema_version": 1,
        "target_version": "",
        "wait_timeout": 60.0,
    }


@pytest.mark.parametrize(
    "operation_id", ["../escape", "/tmp/escape", "nested/request", "with space"]
)
def test_agent_rejects_operation_ids_that_cannot_be_operation_filenames(
    tmp_path: Path, operation_id: str
) -> None:
    """operation ID 不能让 request 写出安装根 operations 目录。"""
    with pytest.raises(UpdateError) as error:
        create_apply_request(
            install_root=tmp_path / "install",
            pending_path=tmp_path / "install" / "pending-update.json",
            bootstrap_command=("/stable/Product", "launch"),
            operation_id=operation_id,
        )

    assert error.value.code is UpdateErrorCode.SETTINGS_INVALID
    assert "operation_id" in error.value.message


def test_agent_rejects_request_path_outside_its_operation_contract(
    tmp_path: Path,
) -> None:
    """handoff 和 Agent 执行都不能消费 operations 目录之外的 request。"""
    install_root = tmp_path / "install"
    request = create_apply_request(
        install_root=install_root,
        pending_path=install_root / "pending-update.json",
        bootstrap_command=("/stable/Product", "launch"),
        operation_id="outside-request",
    )
    valid_path = write_agent_request(request)
    outside_path = tmp_path / "outside-request.request.json"
    outside_path.write_text(valid_path.read_text(encoding="utf-8"), encoding="utf-8")

    with pytest.raises(UpdateError, match="agent request path must equal"):
        write_agent_handoff(outside_path)

    with pytest.raises(UpdateError, match="agent request path must equal"):
        UpdateAgent().run_request(outside_path)


def test_agent_switches_installed_release_then_restarts_bootstrap(
    tmp_path: Path,
) -> None:
    """本地切换也必须经 Agent 事务完成并回到稳定 Bootstrap。"""
    install_root = tmp_path / "install"
    install_root.mkdir()
    request = create_switch_request(
        install_root=install_root,
        version="1.1.0",
        old_pid=4321,
        bootstrap_command=("/stable/Product", "launch"),
        operation_id="switch-001",
    )
    request_path = write_agent_request(request)
    captured_switch: dict[str, object] = {}

    def wait_for_handoff(path: Path, request: object) -> None:
        """以同步测试替代真实宿主确认。"""
        assert path == request_path

    def switch_release(**kwargs: object) -> RuntimeResult:
        """记录 Agent 委托给 UOT runtime 的本地切换参数。"""
        captured_switch.update(kwargs)
        return RuntimeResult(
            success=True,
            action="switch_installed",
            version="1.1.0",
            previous_version="1.0.0",
            release_dir=install_root / "releases" / "1.1.0",
            message="switched",
        )

    result = UpdateAgent(
        switch_release=switch_release,
        launch_bootstrap=lambda command, cwd: 2468,
        wait_for_handoff=wait_for_handoff,
    ).run_request(request_path)

    assert captured_switch == {
        "install_root": install_root,
        "version": "1.1.0",
        "wait_pid": 4321,
        "wait_timeout": 60.0,
        "restart": False,
    }
    assert result.runtime_result.action == "switch_installed"
    assert result.bootstrap_pid == 2468


def test_agent_failure_marks_status_and_never_starts_bootstrap(tmp_path: Path) -> None:
    """验证 UOT runtime 失败时，Agent 保留旧版本且不拉起 Bootstrap。"""
    install_root = tmp_path / "install"
    install_root.mkdir()
    (install_root / "current.json").write_text(
        json.dumps(
            {
                "version": "1.0.0",
                "release_dir": "releases/1.0.0",
                "executable": "Product",
            }
        ),
        encoding="utf-8",
    )
    request = create_apply_request(
        install_root=install_root,
        pending_path=install_root / "pending-update.json",
        bootstrap_command=("/stable/Product", "launch"),
        operation_id="failed-update-001",
    )
    request_path = write_agent_request(request)
    bootstrap_calls: list[tuple[str, ...]] = []

    def apply_pending(**kwargs: object) -> RuntimeResult:
        """模拟 Core 在写入 current.json 前发现包校验失败。"""
        raise UpdateError(
            UpdateErrorCode.PACKAGE_HASH_MISMATCH, "package hash mismatch"
        )

    def launch_bootstrap(command: tuple[str, ...], cwd: Path) -> int:
        """记录是否发生不安全的重启。"""
        bootstrap_calls.append(command)
        return 1234

    with pytest.raises(
        UpdateError, match="PACKAGE_HASH_MISMATCH: package hash mismatch"
    ):
        UpdateAgent(
            apply_pending=apply_pending,
            launch_bootstrap=launch_bootstrap,
            wait_for_handoff=lambda path, agent_request: None,
        ).run_request(request_path)

    status = read_agent_status(request_path)
    current = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
    assert bootstrap_calls == []
    assert status["phase"] == "failed"
    assert status["error"] == "PACKAGE_HASH_MISMATCH: package hash mismatch"
    assert current["version"] == "1.0.0"


@pytest.mark.parametrize(
    ("members", "expected_code"),
    [
        (
            {"Product": "new", "Config.json": "first", "config.json": "second"},
            UpdateErrorCode.PACKAGE_LAYOUT_INVALID,
        ),
        (
            {"Product": "new", f"{'a' * 260}.txt": "too long"},
            UpdateErrorCode.PACKAGE_PATH_TOO_LONG,
        ),
    ],
)
def test_agent_propagates_package_plan_failures_without_transaction_residue(
    tmp_path: Path,
    members: dict[str, str],
    expected_code: UpdateErrorCode,
) -> None:
    """Agent 应透传 Core 路径错误，且不得切换版本、启动 Bootstrap 或留下事务残留。"""
    install_root = tmp_path / "install"
    install_root.mkdir()
    current_path = install_root / "current.json"
    current_payload = {
        "app_id": "demo",
        "version": "1.0.0",
        "release_dir": "releases/1.0.0",
        "executable": "Product",
        "entry": {"kind": "executable", "path": "Product", "platform": "windows"},
    }
    current_path.write_text(json.dumps(current_payload), encoding="utf-8")
    package_path = tmp_path / "package.zip"
    with zipfile.ZipFile(package_path, "w") as archive:
        for name, content in members.items():
            archive.writestr(name, content)
    manifest = UpdateManifest.from_payload(
        {
            "schema_version": 2,
            "app_id": "demo",
            "channel": "stable",
            "version": "1.1.0",
            "mandatory": False,
            "min_supported_version": "1.0.0",
            "published_at": "2026-07-27T00:00:00+00:00",
            "notes": "path failure",
            "platform": "windows",
            "package": {
                "url": "demo/stable/v1.1.0/windows/package.zip",
                "size": package_path.stat().st_size,
                "sha256": hashlib.sha256(package_path.read_bytes()).hexdigest(),
            },
        }
    )
    pending_path = install_root / "pending-update.json"
    pending_path.write_text(
        json.dumps(
            {
                "install_root": str(install_root),
                "package_path": str(package_path),
                "manifest": manifest.to_payload(),
            }
        ),
        encoding="utf-8",
    )
    request = create_apply_request(
        install_root=install_root,
        pending_path=pending_path,
        bootstrap_command=("/stable/Product", "launch"),
        operation_id=f"path-failure-{expected_code.value.lower()}",
    )
    request_path = write_agent_request(request)
    bootstrap_calls: list[tuple[str, ...]] = []

    with pytest.raises(UpdateError) as error:
        UpdateAgent(
            launch_bootstrap=lambda command, cwd: (
                bootstrap_calls.append(command) or 1234
            ),
            wait_for_handoff=lambda path, agent_request: None,
        ).run_request(request_path)

    agent_status = read_agent_status(request_path)
    runtime_status = json.loads(
        (install_root / "update-status.json").read_text(encoding="utf-8")
    )
    runtime_result = json.loads(
        (install_root / "update-result.json").read_text(encoding="utf-8")
    )
    assert error.value.code is expected_code
    assert expected_code.value in str(agent_status["error"])
    assert expected_code.value in str(runtime_status["error"])
    assert expected_code.value in str(runtime_result["message"])
    assert json.loads(current_path.read_text(encoding="utf-8")) == current_payload
    assert bootstrap_calls == []
    assert not (install_root / "releases" / "1.1.0").exists()
    assert not list(install_root.glob(".update-*.tmp"))
    assert not list(install_root.glob(".release-backup.*.tmp"))
    assert not list(install_root.glob(".sidecar-backup.*.tmp"))


def test_agent_launcher_waits_for_ready_then_allows_host_handoff(
    tmp_path: Path,
) -> None:
    """宿主只能在独立 Agent ready 后确认交接并退出。"""
    install_root = tmp_path / "install"
    install_root.mkdir()
    agent_executable = tmp_path / "uot-agent"
    agent_executable.write_text("agent", encoding="utf-8")
    request = create_apply_request(
        install_root=install_root,
        pending_path=install_root / "pending-update.json",
        bootstrap_command=("/stable/Product", "launch"),
        operation_id="update-003",
    )
    calls: list[list[str]] = []

    def popen(args: list[str], cwd: str, close_fds: bool, **kwargs: object):  # noqa: ANN001
        """模拟 Agent 启动后立即写入 ready 状态。"""
        calls.append(args)
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL
        request_path = Path(args[-1])
        status_path = request_path.with_name("update-003.status.json")
        status_path.write_text(
            json.dumps(
                {"schema_version": 1, "operation_id": "update-003", "phase": "ready"}
            ),
            encoding="utf-8",
        )

        class Process:
            """假 Agent 进程。"""

            pid = 8642

        return Process()

    launcher = UpdateAgentLauncher(agent_executable, popen=popen, poll_interval=0.001)
    started = launcher.start(request)
    handoff_path = launcher.confirm_handoff(started.request_path)

    assert started.agent_pid == 8642
    assert calls == [
        [str(agent_executable), "apply", "--request", str(started.request_path)]
    ]
    assert (
        json.loads(handoff_path.read_text(encoding="utf-8"))["operation_id"]
        == "update-003"
    )


def test_agent_launcher_fails_fast_when_agent_exits_before_ready(
    tmp_path: Path,
) -> None:
    """Agent 提前退出时宿主不应等待完整 ready 超时。"""
    install_root = tmp_path / "install"
    install_root.mkdir()
    agent_executable = tmp_path / "uot-agent"
    agent_executable.write_text("agent", encoding="utf-8")
    request = create_apply_request(
        install_root=install_root,
        pending_path=install_root / "pending-update.json",
        bootstrap_command=("/stable/Product", "launch"),
        operation_id="agent-exited",
    )

    class Process:
        """模拟 ready 前已退出的 Agent 进程。"""

        pid = 8642

        def poll(self) -> int:
            """返回进程提前退出的状态码。"""

            return 17

    launcher = UpdateAgentLauncher(
        agent_executable, popen=lambda *args, **kwargs: Process(), poll_interval=0.001
    )

    with pytest.raises(UpdateError, match="exited before ready with code 17"):
        launcher.start(request, ready_timeout=5.0)

    status = read_agent_status(
        install_root / "operations" / "agent-exited.request.json"
    )
    assert status["phase"] == "failed"


def test_agent_launcher_terminates_waiting_agent_after_ready_timeout(
    tmp_path: Path,
) -> None:
    """ready 超时时终止仍在等待交接的 Agent，并保留失败诊断。"""
    install_root = tmp_path / "install"
    install_root.mkdir()
    agent_executable = tmp_path / "uot-agent"
    agent_executable.write_text("agent", encoding="utf-8")
    request = create_apply_request(
        install_root=install_root,
        pending_path=install_root / "pending-update.json",
        bootstrap_command=("/stable/Product", "launch"),
        operation_id="agent-timeout",
    )

    class Process:
        """模拟一直未写入 ready 的 Agent 进程。"""

        pid = 8642

        def __init__(self) -> None:
            """初始化进程终止记录。"""

            self.terminated = False

        def poll(self) -> None:
            """表示进程仍在运行。"""

            return None

        def terminate(self) -> None:
            """记录 launcher 已请求终止。"""

            self.terminated = True

        def wait(self, *, timeout: float) -> int:
            """模拟进程在终止后立即退出。"""

            assert timeout == 1.0
            return 0

    process = Process()
    launcher = UpdateAgentLauncher(
        agent_executable, popen=lambda *args, **kwargs: process, poll_interval=0.001
    )

    with pytest.raises(UpdateError, match="did not become ready"):
        launcher.start(request, ready_timeout=0.0)

    status = read_agent_status(
        install_root / "operations" / "agent-timeout.request.json"
    )
    assert process.terminated is True
    assert status["phase"] == "failed"


@pytest.mark.skipif(os.name == "nt", reason="该真实 release 使用 POSIX shell 入口")
def test_agent_performs_real_process_handoff_and_bootstrap_starts_new_release(
    tmp_path: Path,
) -> None:
    """旧进程退出后，Agent 安装并由 Bootstrap 拉起 current.json 的新 release。"""
    install_root = tmp_path / "install"
    old_release = install_root / "releases" / "1.0.0"
    old_release.mkdir(parents=True)
    (old_release / "Product").write_text("old", encoding="utf-8")
    (install_root / "current.json").write_text(
        json.dumps(
            {
                "app_id": "demo",
                "version": "1.0.0",
                "release_dir": "releases/1.0.0",
                "executable": "Product",
                "entry": {"kind": "executable", "path": "Product", "platform": "linux"},
            }
        ),
        encoding="utf-8",
    )
    launched_version = install_root / "launched-version.txt"
    package_path = tmp_path / "package.zip"
    script = f"#!/bin/sh\nprintf '1.1.0' > '{launched_version}'\n"
    info = zipfile.ZipInfo("Product")
    info.external_attr = 0o755 << 16
    with zipfile.ZipFile(package_path, "w") as archive:
        archive.writestr(info, script)
    manifest = UpdateManifest.from_payload(
        {
            "schema_version": 2,
            "app_id": "demo",
            "channel": "stable",
            "version": "1.1.0",
            "mandatory": False,
            "min_supported_version": "1.0.0",
            "published_at": "2026-07-16T00:00:00+00:00",
            "notes": "test",
            "platform": "linux",
            "package": {
                "url": "demo/stable/v1.1.0/package.zip",
                "size": package_path.stat().st_size,
                "sha256": hashlib.sha256(package_path.read_bytes()).hexdigest(),
            },
        }
    )
    pending_path = install_root / "pending-update.json"
    pending_path.write_text(
        json.dumps(
            {
                "install_root": str(install_root),
                "package_path": str(package_path),
                "manifest": manifest.to_payload(),
            }
        ),
        encoding="utf-8",
    )
    bootstrap_script = tmp_path / "bootstrap.py"
    bootstrap_script.write_text(
        "import json\n"
        "import subprocess\n"
        "import sys\n"
        "from pathlib import Path\n"
        "root = Path(sys.argv[1])\n"
        "current = json.loads((root / 'current.json').read_text(encoding='utf-8'))\n"
        "subprocess.Popen([str(root / current['release_dir'] / current['executable'])])\n",
        encoding="utf-8",
    )
    old_process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(0.15)"]
    )
    request = create_apply_request(
        install_root=install_root,
        pending_path=pending_path,
        old_pid=old_process.pid,
        wait_timeout=2.0,
        bootstrap_command=(sys.executable, str(bootstrap_script), str(install_root)),
        operation_id="real-handoff",
    )
    request_path = write_agent_request(request)
    write_agent_handoff(request_path)

    result = UpdateAgent().run_request(request_path)

    os.waitpid(result.bootstrap_pid, 0)
    deadline = time.monotonic() + 2
    while not launched_version.is_file() and time.monotonic() < deadline:
        time.sleep(0.02)
    current = json.loads((install_root / "current.json").read_text(encoding="utf-8"))
    assert result.success is True
    assert current["version"] == "1.1.0"
    assert launched_version.read_text(encoding="utf-8") == "1.1.0"
