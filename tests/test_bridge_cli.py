"""UOT JSON bridge 命令行测试。"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import update_online_tool.bridge_cli as bridge_cli
from update_online_tool.agent import AgentLaunchResult
from update_online_tool.bridge_cli import main


def test_bridge_check_returns_json_for_desktop_host(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    """bridge 向 Electron/Tauri 主进程返回稳定 JSON 升级决策。"""
    install_root = _write_install_root(tmp_path)
    nas_root = tmp_path / "nas"
    _write_manifest(nas_root, version="1.1.0")
    config_path = _write_bridge_config(
        tmp_path, install_root=install_root, nas_root=nas_root
    )

    exit_code = main(["check", "--config", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    payload = json.loads(captured.out)
    assert payload["ok"] is True
    assert payload["decision"] == "optional_update"
    assert payload["manifest"]["version"] == "1.1.0"


def test_bridge_lists_installed_versions_for_version_selector(
    tmp_path: Path, capsys
) -> None:  # noqa: ANN001
    """版本选择器通过 bridge 获取本地 release，不能自行读取 current.json。"""
    install_root = _write_install_root(tmp_path)
    second_release = install_root / "releases" / "1.1.0"
    second_release.mkdir()
    (second_release / "MyTool.exe").write_text("app", encoding="utf-8")
    config_path = _write_bridge_config(
        tmp_path, install_root=install_root, nas_root=tmp_path / "nas"
    )

    exit_code = main(["list-installed", "--config", str(config_path)])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert [(item["version"], item["current"]) for item in payload["versions"]] == [
        ("1.1.0", False),
        ("1.0.0", True),
    ]


def test_bridge_excludes_release_missing_configured_runtime_resource(
    tmp_path: Path, capsys
) -> None:  # noqa: ANN001
    """验证 bridge 在 UOT 内核中排除缺少 settings 的历史 release。"""
    install_root = _write_install_root(tmp_path)
    second_release = install_root / "releases" / "1.1.0"
    second_release.mkdir()
    (second_release / "MyTool.exe").write_text("app", encoding="utf-8")
    required_path = "resources/uot/settings.json"
    current_settings = install_root / "releases" / "1.0.0" / required_path
    current_settings.parent.mkdir(parents=True)
    current_settings.write_text("{}", encoding="utf-8")
    config_path = _write_bridge_config(
        tmp_path, install_root=install_root, nas_root=tmp_path / "nas"
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["release_required_paths"] = [required_path]
    config_path.write_text(json.dumps(config), encoding="utf-8")

    exit_code = main(["list-installed", "--config", str(config_path)])

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert [item["version"] for item in payload["versions"]] == ["1.0.0"]


def test_bridge_prepare_writes_pending_without_starting_updater(
    tmp_path: Path, capsys
) -> None:  # noqa: ANN001
    """bridge prepare 支持宿主退出前的两阶段更新交接。"""
    install_root = _write_install_root(tmp_path)
    updater = install_root / "updater" / "MyToolUpdater.exe"
    updater.parent.mkdir()
    updater.write_text("updater", encoding="utf-8")
    nas_root = tmp_path / "nas"
    _write_manifest(nas_root, version="1.1.0", content=b"package")
    config_path = _write_bridge_config(
        tmp_path, install_root=install_root, nas_root=nas_root
    )

    exit_code = main(
        [
            "prepare",
            "--config",
            str(config_path),
            "--version",
            "1.1.0",
            "--old-pid",
            "123",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    payload = json.loads(captured.out)
    pending = json.loads(
        (install_root / "pending-update.json").read_text(encoding="utf-8")
    )
    assert payload["ok"] is True
    assert payload["version"] == "1.1.0"
    with zipfile.ZipFile(Path(payload["package_path"])) as archive:
        assert archive.read("payload.bin") == b"package"
    assert pending["old_pid"] == 123
    assert pending["restart"] is True


def test_bridge_prepare_returns_package_plan_error_without_mutating_install_state(
    tmp_path: Path,
    capsys,
) -> None:  # noqa: ANN001
    """Bridge 只透传 Core 布局错误，失败时不得写 pending 或覆盖当前版本。"""
    install_root = _write_install_root(tmp_path)
    current_path = install_root / "current.json"
    current_bytes = current_path.read_bytes()
    nas_root = tmp_path / "nas"
    _write_manifest(
        nas_root,
        version="1.1.0",
        members={"Config.json": "first", "config.json": "second"},
    )
    config_path = _write_bridge_config(
        tmp_path, install_root=install_root, nas_root=nas_root
    )

    exit_code = main(["prepare", "--config", str(config_path), "--version", "1.1.0"])

    payload = json.loads(capsys.readouterr().err)
    assert exit_code == 1
    assert payload["error"]["code"] == "PACKAGE_LAYOUT_INVALID"
    assert current_path.read_bytes() == current_bytes
    assert not (install_root / "pending-update.json").exists()
    assert not list((install_root / "updates").rglob("package.zip"))
    assert not list((install_root / "updates").rglob(".package.zip.*.tmp"))


def test_bridge_starts_agent_before_host_confirms_handoff(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:  # noqa: ANN001
    """bridge 将已准备更新交给 ready 状态的独立 Agent。"""
    install_root = _write_install_root(tmp_path)
    nas_root = tmp_path / "nas"
    config_path = _write_bridge_config(
        tmp_path, install_root=install_root, nas_root=nas_root
    )
    agent_executable = tmp_path / "uot-agent"
    agent_executable.write_text("agent", encoding="utf-8")
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload.update(
        {
            "agent_executable": str(agent_executable),
            "bootstrap_command": [
                "/stable/Product",
                "launch",
                "--install-root",
                str(install_root),
            ],
            "agent_ready_timeout": 45,
            "handoff_timeout": 90,
        }
    )
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    (install_root / "pending-update.json").write_text("{}", encoding="utf-8")
    captured: dict[str, object] = {}

    class Launcher:
        """隔离 bridge 的 Agent 启动协议。"""

        def __init__(self, executable: Path) -> None:
            assert executable == agent_executable

        def start(self, request, *, ready_timeout: float) -> AgentLaunchResult:  # noqa: ANN001
            captured["request"] = request
            captured["ready_timeout"] = ready_timeout
            request_path = install_root / "operations" / "bridge-operation.request.json"
            return AgentLaunchResult("bridge-operation", 7654, request_path)

    monkeypatch.setattr(bridge_cli, "UpdateAgentLauncher", Launcher)

    exit_code = main(
        [
            "agent-start",
            "--config",
            str(config_path),
            "--old-pid",
            "456",
            "--operation-id",
            "bridge-operation",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    request = captured["request"]
    assert exit_code == 0
    assert output == {
        "agent_pid": 7654,
        "ok": True,
        "operation_id": "bridge-operation",
        "request_path": str(
            install_root / "operations" / "bridge-operation.request.json"
        ),
    }
    assert request.pending_path == install_root / "pending-update.json"
    assert request.old_pid == 456
    assert request.bootstrap_command == (
        "/stable/Product",
        "launch",
        "--install-root",
        str(install_root),
    )
    assert captured["ready_timeout"] == 45.0
    assert request.handoff_timeout == 90.0


def test_bridge_starts_agent_for_local_switch(
    tmp_path: Path, monkeypatch, capsys
) -> None:  # noqa: ANN001
    """bridge 的本地切换也只能通过 ready Agent 进入 runtime。"""
    install_root = _write_install_root(tmp_path)
    nas_root = tmp_path / "nas"
    config_path = _write_bridge_config(
        tmp_path, install_root=install_root, nas_root=nas_root
    )
    agent_executable = tmp_path / "uot-agent"
    agent_executable.write_text("agent", encoding="utf-8")
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    payload.update(
        {
            "agent_executable": str(agent_executable),
            "bootstrap_command": ["/stable/Product", "launch"],
        }
    )
    config_path.write_text(json.dumps(payload), encoding="utf-8")
    captured: dict[str, object] = {}

    class Launcher:
        """隔离 Agent 进程，仅验证 bridge 的公开请求契约。"""

        def __init__(self, executable: Path) -> None:
            assert executable == agent_executable

        def start(self, request, *, ready_timeout: float) -> AgentLaunchResult:  # noqa: ANN001
            captured["request"] = request
            return AgentLaunchResult(
                "switch-operation",
                6543,
                install_root / "operations" / "switch.request.json",
            )

    monkeypatch.setattr(bridge_cli, "UpdateAgentLauncher", Launcher)

    exit_code = main(
        [
            "agent-switch",
            "--config",
            str(config_path),
            "--version",
            "1.1.0",
            "--operation-id",
            "switch-operation",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    request = captured["request"]
    assert exit_code == 0
    assert output["operation_id"] == "switch-operation"
    assert request.action == "switch"
    assert request.target_version == "1.1.0"
    assert request.pending_path is None


def test_bridge_passes_release_requirements_to_agent_request(
    tmp_path: Path, monkeypatch, capsys
) -> None:  # noqa: ANN001
    """验证 Agent 的切换与回滚不能绕过 bridge 声明的 release 契约。"""
    install_root = _write_install_root(tmp_path)
    config_path = _write_bridge_config(
        tmp_path, install_root=install_root, nas_root=tmp_path / "nas"
    )
    agent_executable = tmp_path / "uot-agent"
    agent_executable.write_text("agent", encoding="utf-8")
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config.update(
        {
            "agent_executable": str(agent_executable),
            "bootstrap_command": ["/stable/Product", "launch"],
            "release_required_paths": ["resources/uot/settings.json"],
        }
    )
    config_path.write_text(json.dumps(config), encoding="utf-8")
    captured: dict[str, object] = {}

    class Launcher:
        """捕获 bridge 创建的 Agent request。"""

        def __init__(self, executable: Path) -> None:
            assert executable == agent_executable

        def start(self, request, *, ready_timeout: float) -> AgentLaunchResult:  # noqa: ANN001
            captured["request"] = request
            return AgentLaunchResult(
                "switch-operation",
                6543,
                install_root / "operations" / "switch.request.json",
            )

    monkeypatch.setattr(bridge_cli, "UpdateAgentLauncher", Launcher)

    exit_code = main(
        [
            "agent-switch",
            "--config",
            str(config_path),
            "--version",
            "1.1.0",
        ]
    )

    assert exit_code == 0
    assert captured["request"].release_required_paths == (
        "resources/uot/settings.json",
    )


def test_bridge_rejects_invalid_config_shape(tmp_path: Path, capsys) -> None:  # noqa: ANN001
    """bridge 配置错误时仍返回机器可读 JSON。"""
    config_path = tmp_path / "bridge.json"
    config_path.write_text('{"app_id":"demo"}', encoding="utf-8")

    exit_code = main(["check", "--config", str(config_path)])

    payload = json.loads(capsys.readouterr().err)
    assert exit_code == 1
    assert payload["ok"] is False
    assert payload["error"]["code"] == "SETTINGS_INVALID"


def _write_bridge_config(tmp_path: Path, *, install_root: Path, nas_root: Path) -> Path:
    """写入 bridge 与 UOT settings 配置。"""
    settings_path = tmp_path / "settings.json"
    config_path = tmp_path / "bridge.json"
    settings_path.write_text(
        json.dumps(
            {
                "nas": {"root": str(nas_root)},
                "updater": {"executable_name": "MyToolUpdater.exe"},
            }
        ),
        encoding="utf-8",
    )
    config_path.write_text(
        json.dumps(
            {
                "app_id": "my-tool",
                "install_root": str(install_root),
                "settings_path": str(settings_path),
                "platform": "windows",
            }
        ),
        encoding="utf-8",
    )
    return config_path


def _write_install_root(tmp_path: Path) -> Path:
    """写入最小 UOT 安装根。"""
    install_root = tmp_path / "install"
    release_dir = install_root / "releases" / "1.0.0"
    release_dir.mkdir(parents=True)
    (release_dir / "MyTool.exe").write_text("app", encoding="utf-8")
    (install_root / "current.json").write_text(
        json.dumps(
            {
                "app_id": "my-tool",
                "version": "1.0.0",
                "release_dir": "releases/1.0.0",
                "executable": "MyTool.exe",
                "entry": {
                    "kind": "executable",
                    "path": "MyTool.exe",
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
    members: dict[str, str] | None = None,
) -> Path:
    """写入测试 NAS manifest 和包。"""
    version_dir = root / "my-tool" / "stable" / f"v{version}" / "windows"
    channel_dir = root / "my-tool" / "stable"
    platform_dir = channel_dir / "windows"
    package = version_dir / "package.zip"
    package.parent.mkdir(parents=True)
    with zipfile.ZipFile(package, "w") as archive:
        if members is None:
            archive.writestr("MyTool.exe", "entry")
            archive.writestr("payload.bin", content)
        else:
            for name, member_content in members.items():
                archive.writestr(name, member_content)
    package_bytes = package.read_bytes()
    payload = {
        "schema_version": 2,
        "app_id": "my-tool",
        "channel": "stable",
        "version": version,
        "mandatory": False,
        "min_supported_version": "1.0.0",
        "published_at": "2026-07-16T00:00:00+00:00",
        "notes": "release",
        "platform": "windows",
        "package": {
            "url": f"my-tool/stable/v{version}/windows/package.zip",
            "size": len(package_bytes),
            "sha256": hashlib.sha256(package_bytes).hexdigest(),
        },
    }
    platform_dir.mkdir(parents=True, exist_ok=True)
    (platform_dir / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    (version_dir / "latest.json").write_text(json.dumps(payload), encoding="utf-8")
    return package
