"""uot-updater PyInstaller spec 生成测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.pyinstaller_assembly import (
    write_agent_pyinstaller_spec,
    write_bootstrap_pyinstaller_spec,
    write_bridge_pyinstaller_spec,
    write_updater_pyinstaller_spec,
)


def test_write_updater_pyinstaller_spec_generates_onedir_files(tmp_path: Path) -> None:
    """验证可生成默认 onedir updater spec。"""
    result = write_updater_pyinstaller_spec(output_dir=tmp_path / "updater", name="MyToolUpdater")

    entry_text = result.entry_script.read_text(encoding="utf-8")
    spec_text = result.spec_path.read_text(encoding="utf-8")
    assert result.pyinstaller_command == ["python", "-m", "PyInstaller", "--noconfirm", str(result.spec_path)]
    assert "update_online_tool.updater_cli" in entry_text
    assert "collect_submodules(\"cryptography\")" not in spec_text
    assert '"update_online_tool.signature"' in spec_text
    assert '"cryptography.hazmat.primitives.asymmetric.ed25519"' in spec_text
    assert "COLLECT(" in spec_text
    assert "name='MyToolUpdater'" in spec_text


def test_write_updater_pyinstaller_spec_can_generate_onefile(tmp_path: Path) -> None:
    """验证可生成 onefile spec。"""
    result = write_updater_pyinstaller_spec(
        output_dir=tmp_path / "updater",
        name="MyToolUpdater",
        onefile=True,
        console=False,
    )

    spec_text = result.spec_path.read_text(encoding="utf-8")
    assert "COLLECT(" not in spec_text
    assert "console=False" in spec_text


def test_write_updater_pyinstaller_spec_refuses_overwrite_without_force(tmp_path: Path) -> None:
    """验证默认不覆盖已有 spec 文件。"""
    write_updater_pyinstaller_spec(output_dir=tmp_path / "updater", name="MyToolUpdater")

    with pytest.raises(UpdateError) as error:
        write_updater_pyinstaller_spec(output_dir=tmp_path / "updater", name="MyToolUpdater")

    assert error.value.code is UpdateErrorCode.SETTINGS_INVALID


def test_write_agent_and_bootstrap_pyinstaller_specs_use_stable_runtime_clis(tmp_path: Path) -> None:
    """参考运行时可独立生成 Agent 与 Bootstrap 的可打包入口。"""
    agent = write_agent_pyinstaller_spec(output_dir=tmp_path / "agent", name="MyToolAgent")
    bootstrap = write_bootstrap_pyinstaller_spec(output_dir=tmp_path / "bootstrap", name="MyToolBootstrap")

    assert "update_online_tool.agent_cli" in agent.entry_script.read_text(encoding="utf-8")
    assert "name='MyToolAgent'" in agent.spec_path.read_text(encoding="utf-8")
    assert "update_online_tool.bootstrap_cli" in bootstrap.entry_script.read_text(encoding="utf-8")
    assert "name='MyToolBootstrap'" in bootstrap.spec_path.read_text(encoding="utf-8")


def test_write_bridge_pyinstaller_spec_uses_json_bridge_cli(tmp_path: Path) -> None:
    """Tauri/Electron 可将独立 bridge 作为 release 内资源打包。"""
    bridge = write_bridge_pyinstaller_spec(output_dir=tmp_path / "bridge", name="MyToolBridge", onefile=True)

    assert "update_online_tool.bridge_cli" in bridge.entry_script.read_text(encoding="utf-8")
    assert "name='MyToolBridge'" in bridge.spec_path.read_text(encoding="utf-8")
    assert "COLLECT(" not in bridge.spec_path.read_text(encoding="utf-8")
