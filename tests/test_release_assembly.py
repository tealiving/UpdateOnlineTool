"""框架无关发布目录装配测试。"""

from __future__ import annotations

import json
import os
import sys
import zipfile
from pathlib import Path

import pytest

from update_online_tool.errors import UpdateError
from update_online_tool.release_assembly import (
    ReleaseAssemblyAsset,
    ReleaseAssemblyConfig,
    assemble_release_layout,
    create_release_package,
)
from update_online_tool.release_contract import RELEASE_CONTRACT_FILENAME, ReleaseArtifactContract, read_release_contract, write_release_contract
import update_online_tool.release_assembly as release_assembly


def test_assemble_release_layout_keeps_electron_like_release_and_stable_launcher(tmp_path: Path) -> None:
    """通用装配可保留 Electron 类目录并生成标准 UOT 安装布局。"""
    release_dir = tmp_path / "electron-win"
    launcher_dir = tmp_path / "bootstrap"
    updater_bundle = tmp_path / "uot-updater"
    settings_path = tmp_path / "settings.json"
    (release_dir / "resources").mkdir(parents=True)
    launcher_dir.mkdir()
    updater_bundle.mkdir()
    (release_dir / "Demo.exe").write_text("electron", encoding="utf-8")
    (release_dir / "resources" / "app.asar").write_text("asar", encoding="utf-8")
    (launcher_dir / "DemoBootstrap.exe").write_text("bootstrap", encoding="utf-8")
    (updater_bundle / "uot-updater.exe").write_text("updater", encoding="utf-8")
    settings_path.write_text('{"nas":{"root":"D:\\\\Nas"}}', encoding="utf-8")

    result = assemble_release_layout(
        ReleaseAssemblyConfig(
            version="1.2.3",
            app_id="demo",
            release_dir=release_dir,
            launcher_dir=launcher_dir,
            install_output=tmp_path / "Demo_install_v1.2.3",
            update_output=tmp_path / "Demo_update_v1.2.3",
            platform="windows",
            entry_path="Demo.exe",
            release_entry_path="Demo.exe",
            launcher_entry_path="DemoBootstrap.exe",
            updater_bundle=updater_bundle,
            assets=(ReleaseAssemblyAsset(settings_path, "resources/uot/settings.json"),),
        )
    )

    current = json.loads((result.install_root / "current.json").read_text(encoding="utf-8"))
    assert (result.install_root / "Demo.exe").read_text(encoding="utf-8") == "bootstrap"
    assert (result.release_dir / "Demo.exe").read_text(encoding="utf-8") == "electron"
    assert (result.release_dir / "resources" / "app.asar").read_text(encoding="utf-8") == "asar"
    assert (result.release_dir / "resources" / "uot" / "settings.json").is_file()
    assert (result.update_root / "Demo.exe").read_text(encoding="utf-8") == "electron"
    assert (result.update_root / "_launcher" / "Demo.exe").read_text(encoding="utf-8") == "bootstrap"
    assert (result.update_root / "resources" / "uot" / "settings.json").is_file()
    assert (result.install_root / "updater" / "uot-updater" / "uot-updater.exe").is_file()
    assert current["entry"] == {"kind": "executable", "path": "Demo.exe", "platform": "windows"}
    install_contract = read_release_contract(result.release_dir)
    update_contract = read_release_contract(result.update_root)
    assert install_contract is not None
    assert install_contract.version == "1.2.3"
    assert install_contract.entry_path == "Demo.exe"
    assert update_contract == install_contract
    assert (result.update_root / RELEASE_CONTRACT_FILENAME).is_file()


def test_assemble_release_layout_keeps_bootstrap_and_agent_out_of_update_package(tmp_path: Path) -> None:
    """新稳定运行时模式只把 release 放入更新包，避免覆盖 Bootstrap/Agent。"""
    release_dir = tmp_path / "electron-win"
    bootstrap_dir = tmp_path / "bootstrap"
    agent_bundle = tmp_path / "uot-agent"
    release_dir.mkdir()
    bootstrap_dir.mkdir()
    agent_bundle.mkdir()
    (release_dir / "Demo.exe").write_text("electron", encoding="utf-8")
    (bootstrap_dir / "DemoBootstrap.exe").write_text("bootstrap", encoding="utf-8")
    (agent_bundle / "uot-agent.exe").write_text("agent", encoding="utf-8")

    result = assemble_release_layout(
        ReleaseAssemblyConfig(
            version="1.2.3",
            app_id="demo",
            release_dir=release_dir,
            launcher_dir=bootstrap_dir,
            install_output=tmp_path / "Demo_install_v1.2.3",
            update_output=tmp_path / "Demo_update_v1.2.3",
            platform="windows",
            entry_path="Demo.exe",
            release_entry_path="Demo.exe",
            launcher_entry_path="DemoBootstrap.exe",
            bootstrap_agent_mode=True,
            agent_bundle=agent_bundle,
        )
    )

    assert (result.install_root / "DemoBootstrap.exe").read_text(encoding="utf-8") == "bootstrap"
    assert result.launcher_entry == result.install_root / "DemoBootstrap.exe"
    assert result.agent_path == result.install_root / "agent" / "uot-agent"
    assert (result.agent_path / "uot-agent.exe").read_text(encoding="utf-8") == "agent"
    assert (result.update_root / "Demo.exe").read_text(encoding="utf-8") == "electron"
    assert not (result.update_root / "_launcher").exists()
    assert not (result.update_root / "agent").exists()


def test_assemble_release_layout_keeps_tauri_app_and_stable_bootstrap_separate(tmp_path: Path) -> None:
    """稳定模式保留 Tauri `.app` release，并把 Bootstrap 固定在安装根。"""
    release_dir = tmp_path / "tauri-release"
    bootstrap_dir = tmp_path / "bootstrap"
    agent_bundle = tmp_path / "uot-agent"
    app_bundle = release_dir / "DesktopFloatingTimer.app"
    (app_bundle / "Contents" / "MacOS").mkdir(parents=True)
    bootstrap_dir.mkdir()
    agent_bundle.mkdir()
    (app_bundle / "Contents" / "MacOS" / "DesktopFloatingTimer").write_text("tauri", encoding="utf-8")
    (bootstrap_dir / "uot-bootstrap").write_text("bootstrap", encoding="utf-8")
    (agent_bundle / "uot-agent").write_text("agent", encoding="utf-8")

    result = assemble_release_layout(
        ReleaseAssemblyConfig(
            version="1.2.3",
            app_id="desktop-floating-timer",
            release_dir=release_dir,
            launcher_dir=bootstrap_dir,
            install_output=tmp_path / "install",
            update_output=tmp_path / "update",
            platform="macos",
            entry_path="DesktopFloatingTimer.app",
            release_entry_path="DesktopFloatingTimer.app",
            launcher_entry_path="uot-bootstrap",
            bootstrap_agent_mode=True,
            agent_bundle=agent_bundle,
        )
    )

    current = json.loads((result.install_root / "current.json").read_text(encoding="utf-8"))
    assert (result.install_root / "uot-bootstrap").read_text(encoding="utf-8") == "bootstrap"
    assert result.launcher_entry == result.install_root / "uot-bootstrap"
    assert (result.release_dir / "DesktopFloatingTimer.app" / "Contents" / "MacOS" / "DesktopFloatingTimer").read_text(encoding="utf-8") == "tauri"
    assert not (result.install_root / "DesktopFloatingTimer.app").exists()
    assert (result.update_root / "DesktopFloatingTimer.app" / "Contents" / "MacOS" / "DesktopFloatingTimer").is_file()
    assert current["executable"] == "DesktopFloatingTimer.app"


def test_assemble_release_layout_rejects_asset_path_outside_release(tmp_path: Path) -> None:
    """通用装配拒绝写到 release 根目录之外的附加资源路径。"""
    release_dir = tmp_path / "release"
    launcher_dir = tmp_path / "launcher"
    release_dir.mkdir()
    launcher_dir.mkdir()
    (release_dir / "Demo.exe").write_text("release", encoding="utf-8")
    (launcher_dir / "Demo.exe").write_text("launcher", encoding="utf-8")
    settings_path = tmp_path / "settings.json"
    settings_path.write_text("{}", encoding="utf-8")

    with pytest.raises(UpdateError, match="asset target path must be relative"):
        assemble_release_layout(
            ReleaseAssemblyConfig(
                version="1.0.0",
                app_id="demo",
                release_dir=release_dir,
                launcher_dir=launcher_dir,
                install_output=tmp_path / "install",
                update_output=tmp_path / "update",
                platform="windows",
                entry_path="Demo.exe",
                release_entry_path="Demo.exe",
                launcher_entry_path="Demo.exe",
                assets=(ReleaseAssemblyAsset(settings_path, "../settings.json"),),
            )
        )


def test_assemble_release_layout_rejects_source_contract_with_wrong_version(tmp_path: Path) -> None:
    """验证装配不会用错误 CLI 版本覆盖框架构建时写入的契约。"""
    release_dir = tmp_path / "release"
    launcher_dir = tmp_path / "launcher"
    release_dir.mkdir()
    launcher_dir.mkdir()
    (release_dir / "Demo.exe").write_text("release", encoding="utf-8")
    (launcher_dir / "DemoBootstrap.exe").write_text("launcher", encoding="utf-8")
    write_release_contract(
        release_dir,
        ReleaseArtifactContract(
            app_id="demo",
            version="1.0.1",
            platform="windows",
            entry_path="Demo.exe",
        ),
    )

    with pytest.raises(UpdateError, match="release contract version"):
        assemble_release_layout(
            ReleaseAssemblyConfig(
                version="1.0.2",
                app_id="demo",
                release_dir=release_dir,
                launcher_dir=launcher_dir,
                install_output=tmp_path / "install",
                update_output=tmp_path / "update",
                platform="windows",
                entry_path="Demo.exe",
                release_entry_path="Demo.exe",
                launcher_entry_path="DemoBootstrap.exe",
            )
        )


def test_assemble_release_layout_preserves_source_contract_required_paths(tmp_path: Path) -> None:
    """装配不得丢失框架 release 已声明的运行时必需资源。"""
    release_dir = tmp_path / "release"
    launcher_dir = tmp_path / "launcher"
    release_dir.mkdir()
    launcher_dir.mkdir()
    (release_dir / "Demo.exe").write_text("release", encoding="utf-8")
    required_path = release_dir / "resources" / "uot" / "settings.json"
    required_path.parent.mkdir(parents=True)
    required_path.write_text("{}", encoding="utf-8")
    (launcher_dir / "DemoBootstrap.exe").write_text("launcher", encoding="utf-8")
    source_contract = ReleaseArtifactContract(
        app_id="demo",
        version="1.0.2",
        platform="windows",
        entry_path="Demo.exe",
        required_paths=("resources/uot/settings.json",),
    )
    write_release_contract(release_dir, source_contract)

    result = assemble_release_layout(
        ReleaseAssemblyConfig(
            version="1.0.2",
            app_id="demo",
            release_dir=release_dir,
            launcher_dir=launcher_dir,
            install_output=tmp_path / "install",
            update_output=tmp_path / "update",
            platform="windows",
            entry_path="Demo.exe",
            release_entry_path="Demo.exe",
            launcher_entry_path="DemoBootstrap.exe",
        )
    )

    assert read_release_contract(result.release_dir) == source_contract
    assert read_release_contract(result.update_root) == source_contract


def test_assemble_release_layout_rejects_output_overlapping_source(tmp_path: Path) -> None:
    """通用装配在 force 模式下也不能删除输入构建产物。"""
    release_dir = tmp_path / "release"
    launcher_dir = tmp_path / "launcher"
    release_dir.mkdir()
    launcher_dir.mkdir()
    (release_dir / "Demo.exe").write_text("release", encoding="utf-8")
    (launcher_dir / "Demo.exe").write_text("launcher", encoding="utf-8")

    with pytest.raises(UpdateError, match="assembly output overlaps source"):
        assemble_release_layout(
            ReleaseAssemblyConfig(
                version="1.0.0",
                app_id="demo",
                release_dir=release_dir,
                launcher_dir=launcher_dir,
                install_output=release_dir,
                update_output=tmp_path / "update",
                platform="windows",
                entry_path="Demo.exe",
                release_entry_path="Demo.exe",
                launcher_entry_path="Demo.exe",
                force=True,
            )
        )


def test_assemble_release_layout_force_ignores_vanished_external_drive_metadata(tmp_path: Path, monkeypatch) -> None:
    """ExFAT 清理期间消失的 AppleDouble 元数据不能中断受控重建。"""
    release_dir = tmp_path / "release"
    launcher_dir = tmp_path / "bootstrap"
    agent_bundle = tmp_path / "agent"
    install_root = tmp_path / "install"
    release_dir.mkdir()
    launcher_dir.mkdir()
    agent_bundle.mkdir()
    install_root.mkdir()
    (release_dir / "Demo.exe").write_text("release", encoding="utf-8")
    (launcher_dir / "uot-bootstrap").write_text("bootstrap", encoding="utf-8")
    (agent_bundle / "uot-agent").write_text("agent", encoding="utf-8")
    original_rmtree = release_assembly.shutil.rmtree

    def rmtree_with_vanished_metadata(path: Path, *, onerror=None) -> None:  # noqa: ANN001
        assert onerror is not None
        try:
            raise FileNotFoundError("._Demo.exe disappeared")
        except FileNotFoundError:
            onerror(os.unlink, str(Path(path) / "._Demo.exe"), sys.exc_info())
        original_rmtree(path, onerror=onerror)

    monkeypatch.setattr(release_assembly.shutil, "rmtree", rmtree_with_vanished_metadata)

    result = assemble_release_layout(
        ReleaseAssemblyConfig(
            version="1.0.0",
            app_id="demo",
            release_dir=release_dir,
            launcher_dir=launcher_dir,
            install_output=install_root,
            update_output=tmp_path / "update",
            platform="windows",
            entry_path="Demo.exe",
            release_entry_path="Demo.exe",
            launcher_entry_path="uot-bootstrap",
            bootstrap_agent_mode=True,
            agent_bundle=agent_bundle,
            force=True,
        )
    )

    assert (result.install_root / "releases" / "1.0.0" / "Demo.exe").is_file()


def test_create_release_package_preserves_unicode_paths_and_excludes_appledouble_metadata(tmp_path: Path) -> None:
    """发布包使用 UTF-8 路径，且不携带 ExFAT/macOS 伴生元数据。"""
    release_dir = tmp_path / "release"
    app_dir = release_dir / "桌面悬浮计时器.app" / "Contents" / "MacOS"
    app_dir.mkdir(parents=True)
    (app_dir / "app").write_text("tauri", encoding="utf-8")
    (app_dir / "._app").write_text("metadata", encoding="utf-8")
    (release_dir / ".DS_Store").write_text("metadata", encoding="utf-8")

    package = create_release_package(release_dir, tmp_path / "package.zip")

    with zipfile.ZipFile(package) as archive:
        names = archive.namelist()
    assert "桌面悬浮计时器.app/Contents/MacOS/app" in names
    assert not any(Path(name).name.startswith("._") for name in names)
    assert ".DS_Store" not in names
