"""Release artifact contract tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.release_contract import (
    RELEASE_CONTRACT_FILENAME,
    ReleaseArtifactContract,
    validate_release_artifact,
    write_release_contract,
)


def test_write_and_validate_release_contract_with_required_runtime_files(
    tmp_path: Path,
) -> None:
    """验证契约会绑定版本、入口和必需运行时资源。"""
    release_dir = _write_release(tmp_path, version="1.2.3")
    contract = ReleaseArtifactContract(
        app_id="desktop-floating-timer",
        version="1.2.3",
        platform="macos",
        entry_path="DesktopFloatingTimer.app",
        required_paths=(
            "DesktopFloatingTimer.app/Contents/Resources/uot/settings.json",
            "DesktopFloatingTimer.app/Contents/Resources/uot/uot-bridge/uot-bridge",
        ),
    )
    _write_runtime_files(release_dir)

    target = write_release_contract(release_dir, contract)
    validated = validate_release_artifact(
        release_dir=release_dir,
        version="1.2.3",
        entry_path="DesktopFloatingTimer.app",
        app_id="desktop-floating-timer",
        platform="macos",
    )

    assert target == release_dir / RELEASE_CONTRACT_FILENAME
    assert validated == contract


def test_validate_release_artifact_rejects_missing_required_runtime_file(
    tmp_path: Path,
) -> None:
    """验证缺少 settings 的 release 不能被 UOT 切换或回滚。"""
    release_dir = _write_release(tmp_path, version="1.2.3")

    with pytest.raises(UpdateError) as error:
        validate_release_artifact(
            release_dir=release_dir,
            version="1.2.3",
            entry_path="DesktopFloatingTimer.app",
            required_paths=(
                "DesktopFloatingTimer.app/Contents/Resources/uot/settings.json",
            ),
        )

    assert error.value.code is UpdateErrorCode.SETTINGS_INVALID
    assert "required release path not found" in error.value.message


def test_validate_release_artifact_rejects_contract_version_mismatch(
    tmp_path: Path,
) -> None:
    """验证 release 目录版本与契约版本不一致时拒绝启动。"""
    release_dir = _write_release(tmp_path, version="1.2.3")
    invalid = ReleaseArtifactContract(
        app_id="desktop-floating-timer",
        version="0.1.2",
        platform="macos",
        entry_path="DesktopFloatingTimer.app",
    )
    (release_dir / RELEASE_CONTRACT_FILENAME).write_text(
        json.dumps(invalid.to_payload()),
        encoding="utf-8",
    )

    with pytest.raises(UpdateError) as error:
        validate_release_artifact(
            release_dir=release_dir,
            version="1.2.3",
            entry_path="DesktopFloatingTimer.app",
            app_id="desktop-floating-timer",
            platform="macos",
        )

    assert error.value.code is UpdateErrorCode.SETTINGS_INVALID
    assert "release contract version" in error.value.message


def test_validate_release_artifact_keeps_legacy_release_compatible(
    tmp_path: Path,
) -> None:
    """验证未生成契约的旧 release 仍可按宿主必需资源规则检查。"""
    release_dir = _write_release(tmp_path, version="1.2.3")
    settings_path = (
        release_dir / "DesktopFloatingTimer.app/Contents/Resources/uot/settings.json"
    )
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text("{}", encoding="utf-8")

    validated = validate_release_artifact(
        release_dir=release_dir,
        version="1.2.3",
        entry_path="DesktopFloatingTimer.app",
        required_paths=(
            "DesktopFloatingTimer.app/Contents/Resources/uot/settings.json",
        ),
    )

    assert validated is None


def test_release_contract_payload_rejects_unsafe_required_path() -> None:
    """验证契约不会接受可逃逸 release 根目录的路径。"""
    with pytest.raises(UpdateError) as error:
        ReleaseArtifactContract.from_payload(
            {
                "schema_version": 1,
                "app_id": "desktop-floating-timer",
                "version": "1.2.3",
                "platform": "macos",
                "entry_path": "DesktopFloatingTimer.app",
                "required_paths": ["../settings.json"],
            }
        )

    assert error.value.code is UpdateErrorCode.MANIFEST_INVALID


def test_release_contract_payload_rejects_platform_alias() -> None:
    """新 release contract 必须使用规范平台值。"""
    with pytest.raises(UpdateError) as error:
        ReleaseArtifactContract.from_payload(
            {
                "schema_version": 1,
                "app_id": "desktop-floating-timer",
                "version": "1.2.3",
                "platform": "win32",
                "entry_path": "DesktopFloatingTimer.app",
                "required_paths": [],
            }
        )

    assert error.value.code is UpdateErrorCode.MANIFEST_INVALID


def _write_release(tmp_path: Path, *, version: str) -> Path:
    """写入最小 macOS release。"""
    release_dir = tmp_path / "releases" / version
    executable = (
        release_dir / "DesktopFloatingTimer.app/Contents/MacOS/DesktopFloatingTimer"
    )
    executable.parent.mkdir(parents=True)
    executable.write_text("app", encoding="utf-8")
    return release_dir


def _write_runtime_files(release_dir: Path) -> None:
    """写入 Tauri 需要的最小 UOT 资源。"""
    uot_dir = release_dir / "DesktopFloatingTimer.app/Contents/Resources/uot"
    bridge = uot_dir / "uot-bridge/uot-bridge"
    bridge.parent.mkdir(parents=True)
    bridge.write_text("bridge", encoding="utf-8")
    (uot_dir / "settings.json").write_text("{}", encoding="utf-8")
