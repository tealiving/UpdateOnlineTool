"""标准 updater runtime 测试。"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

import update_online_tool.runtime as runtime
from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.manifest import UpdateManifest
from update_online_tool.runtime import (
    RuntimeStatus,
    apply_pending_update,
    install_prepared_package,
    preflight_prepared_package,
    rollback_installation,
    switch_installed_release,
)
from update_online_tool.signature import load_hmac_key, sign_manifest_payload


def test_runtime_status_serializes_zero_duration_fields() -> None:
    """亚毫秒阶段也必须保留稳定的耗时字段。

    :return: None。
    """

    payload = RuntimeStatus(
        phase="success",
        percent=100,
        message="installed",
        version="1.1.0",
        previous_version="1.0.0",
    ).to_payload()

    assert payload["phase_elapsed_ms"] == 0
    assert payload["total_elapsed_ms"] == 0


def test_install_prepared_package_installs_switches_and_writes_result(
    tmp_path: Path,
) -> None:
    """验证 runtime 可安装已准备包并切换 current.json。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(
        tmp_path / "package.zip", {"MyTool.exe": "new", "resources/info.txt": "ok"}
    )
    manifest = _manifest(package_path, version="1.1.0")

    result = install_prepared_package(
        install_root=install_root,
        package_path=package_path,
        manifest=manifest,
    )

    current_payload = json.loads(
        (install_root / "current.json").read_text(encoding="utf-8")
    )
    result_payload = json.loads(
        (install_root / "update-result.json").read_text(encoding="utf-8")
    )
    assert result.success is True
    assert (install_root / "releases" / "1.1.0" / "MyTool.exe").read_text(
        encoding="utf-8"
    ) == "new"
    assert current_payload["version"] == "1.1.0"
    assert current_payload["previous_version"] == "1.0.0"
    assert result_payload["action"] == "install_prepared"
    assert result_payload["version"] == "1.1.0"
    status_payload = json.loads(
        (install_root / "update-status.json").read_text(encoding="utf-8")
    )
    assert status_payload["phase"] == "success"
    assert status_payload["percent"] == 100
    assert status_payload["version"] == "1.1.0"
    assert "started_at" in status_payload
    assert "total_elapsed_ms" in status_payload
    assert "elapsed_ms" in result_payload
    assert "phase_durations_ms" in result_payload
    assert not list(install_root.glob(".update-result.json.*.tmp"))
    assert not list(install_root.glob(".update-status.json.*.tmp"))


def test_apply_pending_update_reads_pending_manifest(tmp_path: Path) -> None:
    """验证 runtime 可从 pending-update.json 应用升级。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    manifest = _manifest(package_path, version="1.1.0")
    pending_path = tmp_path / "pending-update.json"
    pending_path.write_text(
        json.dumps(
            {
                "package_path": str(package_path),
                "install_root": str(install_root),
                "manifest": manifest.to_payload(),
            }
        ),
        encoding="utf-8",
    )

    result = apply_pending_update(pending_path=pending_path)

    assert result.version == "1.1.0"
    assert (install_root / "releases" / "1.1.0" / "MyTool.exe").is_file()


def test_apply_pending_update_verifies_pending_manifest_inside_runtime(
    tmp_path: Path,
) -> None:
    """验证 apply runtime 直接校验 pending 中的 manifest，防止外部预校验后被替换。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    key_path = tmp_path / "signing.key"
    key_path.write_text("release-secret\n", encoding="utf-8")
    manifest_payload = sign_manifest_payload(
        _manifest(package_path, version="1.1.0").to_payload(),
        key=load_hmac_key(key_path),
        key_id="release",
    )
    manifest_payload["version"] = "9.9.9"
    pending_path = tmp_path / "pending-update.json"
    pending_path.write_text(
        json.dumps(
            {
                "package_path": str(package_path),
                "install_root": str(install_root),
                "manifest": manifest_payload,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(UpdateError) as error:
        apply_pending_update(pending_path=pending_path, signature_key=key_path)

    assert error.value.code is UpdateErrorCode.MANIFEST_INVALID
    assert not (install_root / "releases" / "9.9.9").exists()


def test_rollback_installation_switches_to_previous_version(tmp_path: Path) -> None:
    """验证 runtime 可根据 previous_version 回滚。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    manifest = _manifest(package_path, version="1.1.0")
    install_prepared_package(
        install_root=install_root, package_path=package_path, manifest=manifest
    )

    result = rollback_installation(install_root=install_root)

    current_payload = json.loads(
        (install_root / "current.json").read_text(encoding="utf-8")
    )
    assert result.action == "rollback"
    assert result.version == "1.0.0"
    assert current_payload["version"] == "1.0.0"
    assert current_payload["previous_version"] == "1.1.0"


def test_rollback_installation_can_wait_and_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 runtime 回滚可等待旧进程退出并重启。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    manifest = _manifest(package_path, version="1.1.0")
    install_prepared_package(
        install_root=install_root, package_path=package_path, manifest=manifest
    )

    class FakeProcess:
        """测试进程对象。"""

        pid = 5252

    monkeypatch.setattr(
        runtime, "wait_for_process_exit", lambda *, pid, timeout_seconds: None
    )
    monkeypatch.setattr(
        runtime, "launch_current", lambda *, install_root: FakeProcess()
    )

    result = rollback_installation(
        install_root=install_root,
        wait_pid=os.getpid(),
        restart=True,
    )

    status_payload = json.loads(
        (install_root / "update-status.json").read_text(encoding="utf-8")
    )
    result_payload = json.loads(
        (install_root / "update-result.json").read_text(encoding="utf-8")
    )
    assert result.message == "rolled back and restarted"
    assert result.restarted_pid == 5252
    assert status_payload["phase"] == "success"
    assert result_payload["phase_durations_ms"]["restarting"] >= 0


def test_switch_installed_release_can_restart_current_release(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 runtime 本地版本切换可等待后重启并写状态。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    _write_release_entry(install_root, "1.1.0", "MyTool.exe", "new")

    class FakeProcess:
        pid = 4242

    monkeypatch.setattr(
        runtime, "wait_for_process_exit", lambda *, pid, timeout_seconds: None
    )
    monkeypatch.setattr(
        runtime, "launch_current", lambda *, install_root: FakeProcess()
    )

    result = switch_installed_release(
        install_root=install_root,
        version="1.1.0",
        wait_pid=os.getpid(),
        restart=True,
    )

    current_payload = json.loads(
        (install_root / "current.json").read_text(encoding="utf-8")
    )
    status_payload = json.loads(
        (install_root / "update-status.json").read_text(encoding="utf-8")
    )
    result_payload = json.loads(
        (install_root / "update-result.json").read_text(encoding="utf-8")
    )
    assert result.action == "switch_installed"
    assert result.message == "switched and restarted"
    assert result.restarted_pid == 4242
    assert current_payload["version"] == "1.1.0"
    assert current_payload["previous_version"] == "1.0.0"
    assert status_payload["phase"] == "success"
    assert result_payload["phase_durations_ms"]["restarting"] >= 0


def test_install_prepared_package_rejects_unsafe_zip_member(tmp_path: Path) -> None:
    """验证 runtime 拒绝 zip 路径穿越。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(tmp_path / "package.zip", {"../evil.txt": "bad"})
    manifest = _manifest(package_path, version="1.1.0")

    with pytest.raises(UpdateError) as error:
        install_prepared_package(
            install_root=install_root, package_path=package_path, manifest=manifest
        )

    assert error.value.code is UpdateErrorCode.MANIFEST_INVALID
    assert not (tmp_path / "evil.txt").exists()
    assert not (install_root / "releases" / "1.1.0").exists()


def test_install_prepared_package_rejects_manifest_version_escape_without_residue(
    tmp_path: Path,
) -> None:
    """runtime 必须独立拒绝绕过 manifest 解析构造的恶意版本。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    manifest = replace(
        _manifest(package_path, version="1.1.0"), version="../../escaped-release"
    )
    current_before = (install_root / "current.json").read_bytes()

    with pytest.raises(UpdateError) as error:
        install_prepared_package(
            install_root=install_root, package_path=package_path, manifest=manifest
        )

    assert error.value.code is UpdateErrorCode.SETTINGS_INVALID
    assert (install_root / "current.json").read_bytes() == current_before
    assert not (tmp_path / "escaped-release").exists()
    assert not list(install_root.glob(".update-*.tmp"))
    assert not list(install_root.glob(".release-backup.*.tmp"))


def test_install_prepared_package_rejects_hash_mismatch(tmp_path: Path) -> None:
    """验证 runtime 安装前校验包 hash。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    payload = _manifest(package_path, version="1.1.0").to_payload()
    payload["package"]["sha256"] = "0" * 64
    manifest = UpdateManifest.from_payload(payload)

    with pytest.raises(UpdateError) as error:
        install_prepared_package(
            install_root=install_root, package_path=package_path, manifest=manifest
        )

    assert error.value.code is UpdateErrorCode.PACKAGE_HASH_MISMATCH


@pytest.mark.skipif(os.name == "nt", reason="Windows 不提供 POSIX 可执行位语义")
def test_install_prepared_package_preserves_zip_file_mode(tmp_path: Path) -> None:
    """验证 runtime 解压时保留 zip 中的可执行权限。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool"
    )
    package_path = tmp_path / "package.zip"
    info = zipfile.ZipInfo("MyTool")
    info.external_attr = 0o755 << 16
    with zipfile.ZipFile(package_path, "w") as archive:
        archive.writestr(info, "new")
    manifest = _manifest(package_path, version="1.1.0")

    install_prepared_package(
        install_root=install_root, package_path=package_path, manifest=manifest
    )

    installed_mode = (install_root / "releases" / "1.1.0" / "MyTool").stat().st_mode
    assert installed_mode & stat.S_IXUSR


def test_install_prepared_package_preserves_zip_symlink(tmp_path: Path) -> None:
    """验证 runtime 解压时保留 zip 中的 POSIX symlink。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.app"
    )
    package_path = tmp_path / "package.zip"
    with zipfile.ZipFile(package_path, "w") as archive:
        archive.writestr("MyTool.app/Contents/MacOS/MyTool", "new")
        archive.writestr("MyTool.app/Contents/Frameworks/runtime.dylib", "runtime")
        _write_zip_symlink(
            archive,
            "MyTool.app/Contents/Resources/runtime.dylib",
            "../Frameworks/runtime.dylib",
        )
    manifest = _manifest(package_path, version="1.1.0")

    install_prepared_package(
        install_root=install_root, package_path=package_path, manifest=manifest
    )

    installed_link = (
        install_root
        / "releases"
        / "1.1.0"
        / "MyTool.app"
        / "Contents"
        / "Resources"
        / "runtime.dylib"
    )
    assert installed_link.is_symlink()
    assert installed_link.readlink() == Path("../Frameworks/runtime.dylib")


def test_install_prepared_package_dry_run_rejects_escaping_symlink(
    tmp_path: Path,
) -> None:
    """symlink 目标必须在 package plan 阶段校验，不能延迟到真实解压。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool"
    )
    package_path = tmp_path / "package.zip"
    with zipfile.ZipFile(package_path, "w") as archive:
        archive.writestr("MyTool", "new")
        _write_zip_symlink(archive, "links/escape", "../../outside")
    manifest = _manifest(package_path, version="1.1.0")

    with pytest.raises(UpdateError) as error:
        install_prepared_package(
            install_root=install_root,
            package_path=package_path,
            manifest=manifest,
            dry_run=True,
        )

    assert error.value.code is UpdateErrorCode.PACKAGE_LAYOUT_INVALID
    assert not list(install_root.glob(".update-*.tmp"))


def test_install_prepared_package_accepts_macos_app_with_different_inner_executable(
    tmp_path: Path,
) -> None:
    """验证 macOS .app 内部可执行文件名可不同于 bundle 名。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.app"
    )
    package_path = _write_package(
        tmp_path / "package.zip",
        {"MyTool.app/Contents/MacOS/MyTool-v1.1.0": "new"},
    )
    manifest = _manifest(package_path, version="1.1.0")

    result = install_prepared_package(
        install_root=install_root,
        package_path=package_path,
        manifest=manifest,
        dry_run=True,
    )

    assert result.success is True
    install_prepared_package(
        install_root=install_root, package_path=package_path, manifest=manifest
    )
    assert (
        install_root
        / "releases"
        / "1.1.0"
        / "MyTool.app"
        / "Contents"
        / "MacOS"
        / "MyTool-v1.1.0"
    ).is_file()


def test_install_prepared_package_promotes_launcher_and_updater_sidecars(
    tmp_path: Path,
) -> None:
    """验证 update 包中的 launcher/updater sidecar 会提升到安装根。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(
        tmp_path / "package.zip",
        {
            "MyTool.exe": "new",
            "_launcher/MyTool.exe": "launcher",
            "updater/MyToolUpdater.exe": "updater",
        },
    )
    manifest = _manifest(package_path, version="1.1.0")

    install_prepared_package(
        install_root=install_root, package_path=package_path, manifest=manifest
    )

    release_root = install_root / "releases" / "1.1.0"
    assert (release_root / "MyTool.exe").read_text(encoding="utf-8") == "new"
    assert not (release_root / "_launcher").exists()
    assert not (release_root / "updater").exists()
    assert (install_root / "MyTool.exe").read_text(encoding="utf-8") == "launcher"
    assert (install_root / "updater" / "MyToolUpdater.exe").read_text(
        encoding="utf-8"
    ) == "updater"


def test_install_prepared_package_rolls_back_sidecars_when_switch_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 current.json 切换失败时恢复安装根旧 launcher/updater。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    (install_root / "MyTool.exe").write_text("old launcher", encoding="utf-8")
    (install_root / "updater").mkdir()
    (install_root / "updater" / "MyToolUpdater.exe").write_text(
        "old updater", encoding="utf-8"
    )
    package_path = _write_package(
        tmp_path / "package.zip",
        {
            "MyTool.exe": "new",
            "_launcher/MyTool.exe": "new launcher",
            "updater/MyToolUpdater.exe": "new updater",
        },
    )
    manifest = _manifest(package_path, version="1.1.0")

    def fail_switch(**kwargs: object) -> None:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "switch failed")

    monkeypatch.setattr(runtime, "_switch_installed_version", fail_switch)

    with pytest.raises(UpdateError):
        install_prepared_package(
            install_root=install_root, package_path=package_path, manifest=manifest
        )

    assert (install_root / "MyTool.exe").read_text(encoding="utf-8") == "old launcher"
    assert (install_root / "updater" / "MyToolUpdater.exe").read_text(
        encoding="utf-8"
    ) == "old updater"
    assert not (install_root / "releases" / "1.1.0").exists()


def test_install_prepared_package_rolls_back_updater_only_sidecar_when_switch_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证仅包含 updater sidecar 的包也会在失败时恢复旧 updater。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    (install_root / "updater").mkdir()
    (install_root / "updater" / "MyToolUpdater.exe").write_text(
        "old updater", encoding="utf-8"
    )
    package_path = _write_package(
        tmp_path / "package.zip",
        {
            "MyTool.exe": "new",
            "updater/MyToolUpdater.exe": "new updater",
        },
    )
    manifest = _manifest(package_path, version="1.1.0")

    def fail_switch(**kwargs: object) -> None:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "switch failed")

    monkeypatch.setattr(runtime, "_switch_installed_version", fail_switch)

    with pytest.raises(UpdateError):
        install_prepared_package(
            install_root=install_root, package_path=package_path, manifest=manifest
        )

    assert (install_root / "updater" / "MyToolUpdater.exe").read_text(
        encoding="utf-8"
    ) == "old updater"
    assert not (install_root / "releases" / "1.1.0").exists()


def test_install_prepared_package_restores_forced_release_when_switch_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 force 覆盖同版本失败时恢复旧 release，避免 current.json 指向损坏目录。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.1.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    manifest = _manifest(package_path, version="1.1.0")

    def fail_switch(**kwargs: object) -> None:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "switch failed")

    monkeypatch.setattr(runtime, "_switch_installed_version", fail_switch)

    with pytest.raises(UpdateError):
        install_prepared_package(
            install_root=install_root,
            package_path=package_path,
            manifest=manifest,
            force=True,
        )

    current_payload = json.loads(
        (install_root / "current.json").read_text(encoding="utf-8")
    )
    assert current_payload["version"] == "1.1.0"
    assert (install_root / "releases" / "1.1.0" / "MyTool.exe").read_text(
        encoding="utf-8"
    ) == "current"


def test_install_prepared_package_extracts_package_outside_releases(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证完整 update 包先解到安装根 staging，而不是 releases 目录。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(
        tmp_path / "package.zip",
        {
            "MyTool.exe": "new",
            "updater/MyToolUpdater.exe": "updater",
        },
    )
    manifest = _manifest(package_path, version="1.1.0")
    extracted_targets: list[Path] = []
    original_extract = runtime._extract_zip_safe

    def spy_extract_zip_safe(
        package: Path,
        target_dir: Path,
        package_plan: runtime.ReleasePackagePlan | None = None,
    ) -> None:
        """记录解压目标后继续执行真实解压。"""
        extracted_targets.append(target_dir)
        original_extract(package, target_dir, package_plan)

    monkeypatch.setattr(runtime, "_extract_zip_safe", spy_extract_zip_safe)

    install_prepared_package(
        install_root=install_root, package_path=package_path, manifest=manifest
    )

    assert len(extracted_targets) == 1
    assert extracted_targets[0].parent == install_root
    assert not extracted_targets[0].is_relative_to(install_root / "releases")
    assert (install_root / "releases" / "1.1.0" / "MyTool.exe").read_text(
        encoding="utf-8"
    ) == "new"
    assert not (install_root / "releases" / "1.1.0" / "updater").exists()
    assert (install_root / "updater" / "MyToolUpdater.exe").read_text(
        encoding="utf-8"
    ) == "updater"


def test_install_prepared_package_dry_run_does_not_write_install_state(
    tmp_path: Path,
) -> None:
    """验证 dry-run 只校验计划，不写 release、current.json 或 update-result.json。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    manifest = _manifest(package_path, version="1.1.0")

    result = install_prepared_package(
        install_root=install_root,
        package_path=package_path,
        manifest=manifest,
        dry_run=True,
    )

    current_payload = json.loads(
        (install_root / "current.json").read_text(encoding="utf-8")
    )
    assert result.message == "dry-run ok"
    assert current_payload["version"] == "1.0.0"
    assert not (install_root / "releases" / "1.1.0").exists()
    assert not (install_root / "update-result.json").exists()
    assert not (install_root / "update.lock").exists()


def test_install_prepared_package_rejects_existing_runtime_lock(tmp_path: Path) -> None:
    """验证 update.lock 存在时拒绝并发安装。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    (install_root / "update.lock").write_text('{"pid": 1}\n', encoding="utf-8")
    package_path = _write_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    manifest = _manifest(package_path, version="1.1.0")

    with pytest.raises(UpdateError) as error:
        install_prepared_package(
            install_root=install_root, package_path=package_path, manifest=manifest
        )

    assert error.value.code is UpdateErrorCode.UPDATE_LOCKED
    assert (install_root / "update.lock").is_file()
    assert not (install_root / "update-result.json").exists()


def test_install_prepared_package_wait_pid_timeout_blocks_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证旧进程等待超时会阻止安装。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    manifest = _manifest(package_path, version="1.1.0")
    monkeypatch.setattr(runtime, "_is_process_alive", lambda pid: True)

    with pytest.raises(UpdateError) as error:
        install_prepared_package(
            install_root=install_root,
            package_path=package_path,
            manifest=manifest,
            wait_pid=os.getpid(),
            wait_timeout=0,
        )

    assert error.value.code is UpdateErrorCode.PROCESS_TIMEOUT
    assert not (install_root / "releases" / "1.1.0").exists()


def test_install_prepared_package_restart_writes_restarted_pid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证安装后重启会记录新进程 PID。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    manifest = _manifest(package_path, version="1.1.0")

    class FakeProcess:
        pid = 4242

    monkeypatch.setattr(
        runtime, "launch_current", lambda *, install_root: FakeProcess()
    )

    result = install_prepared_package(
        install_root=install_root,
        package_path=package_path,
        manifest=manifest,
        restart=True,
    )

    result_payload = json.loads(
        (install_root / "update-result.json").read_text(encoding="utf-8")
    )
    assert result.message == "installed and restarted"
    assert result.restarted_pid == 4242
    assert result_payload["restarted_pid"] == 4242


def test_install_prepared_package_rejects_restart_without_switch(
    tmp_path: Path,
) -> None:
    """验证不切换 current.json 时不能请求重启当前版本。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    manifest = _manifest(package_path, version="1.1.0")

    with pytest.raises(UpdateError) as error:
        install_prepared_package(
            install_root=install_root,
            package_path=package_path,
            manifest=manifest,
            switch_current=False,
            restart=True,
        )

    assert error.value.code is UpdateErrorCode.SETTINGS_INVALID
    assert not (install_root / "releases" / "1.1.0").exists()


def test_is_process_alive_uses_windows_api_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 Windows 下不会走 os.kill(pid, 0) 探测。"""
    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setattr(runtime, "_is_windows_process_alive", lambda pid: pid == 123)

    assert runtime._is_process_alive(123) is True
    assert runtime._is_process_alive(456) is False


def test_install_prepared_package_writes_failure_result(tmp_path: Path) -> None:
    """验证安装失败时写入失败 update-result.json。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(tmp_path / "package.zip", {"Other.exe": "new"})
    manifest = _manifest(package_path, version="1.1.0")

    with pytest.raises(UpdateError) as error:
        install_prepared_package(
            install_root=install_root, package_path=package_path, manifest=manifest
        )

    result_payload = json.loads(
        (install_root / "update-result.json").read_text(encoding="utf-8")
    )
    assert error.value.code is UpdateErrorCode.SETTINGS_INVALID
    assert result_payload["success"] is False
    assert result_payload["action"] == "install_prepared"
    assert result_payload["version"] == "1.1.0"
    assert "release entry not found" in result_payload["message"]
    assert result_payload["schema_version"] == 1
    assert result_payload["error_code"] == UpdateErrorCode.SETTINGS_INVALID.value
    assert result_payload["failure_phase"] == "verifying"
    assert len(result_payload["operation_id"]) == 32
    assert result_payload["completed_at"].endswith("Z")
    status_payload = json.loads(
        (install_root / "update-status.json").read_text(encoding="utf-8")
    )
    assert status_payload["phase"] == "failed"
    assert "release entry not found" in status_payload["message"]
    assert status_payload["error_code"] == UpdateErrorCode.SETTINGS_INVALID.value
    assert status_payload["operation_id"] == result_payload["operation_id"]
    assert not (install_root / "update.lock").exists()


def test_preflight_and_install_reuse_operation_temporary_directory(
    tmp_path: Path,
) -> None:
    """预检与安装必须使用同一个 operation_id 临时目录。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    manifest = _manifest(package_path, version="1.1.0")

    preflight = preflight_prepared_package(
        install_root=install_root,
        package_path=package_path,
        manifest=manifest,
    )
    result = install_prepared_package(
        install_root=install_root,
        package_path=package_path,
        manifest=manifest,
        operation_id=preflight.operation_id,
    )

    assert preflight.temporary_release_dir.name == (
        f".update-1.1.0.{preflight.operation_id}.tmp"
    )
    assert result.success is True
    assert not preflight.temporary_release_dir.exists()


def test_preflight_rejects_windows_path_before_pending_or_extraction(
    tmp_path: Path,
) -> None:
    """Windows 长路径预检必须在创建临时目录前 fail-closed。"""
    package_path = _write_package(tmp_path / "package.zip", {"MyTool.exe": "new"})
    manifest = replace(_manifest(package_path, version="1.1.0"), platform="windows")
    long_root = Path("C:/") / ("r" * 250)

    with pytest.raises(UpdateError) as error:
        preflight_prepared_package(
            install_root=long_root,
            package_path=package_path,
            manifest=manifest,
            entry_name="MyTool.exe",
        )

    assert error.value.code is UpdateErrorCode.PACKAGE_PATH_TOO_LONG
    assert not long_root.exists()


def test_install_prepared_package_rejects_windows_style_path_traversal(
    tmp_path: Path,
) -> None:
    """验证 runtime 拒绝 Windows 风格 zip 路径穿越。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(tmp_path / "package.zip", {"..\\evil.txt": "bad"})
    manifest = _manifest(package_path, version="1.1.0")

    with pytest.raises(UpdateError) as error:
        install_prepared_package(
            install_root=install_root, package_path=package_path, manifest=manifest
        )

    assert error.value.code is UpdateErrorCode.MANIFEST_INVALID
    assert not (tmp_path / "evil.txt").exists()
    assert not (install_root / "releases" / "1.1.0").exists()


@pytest.mark.parametrize(
    "unsafe_name", ["资源/CON.txt", "资源/name. ", "资源/file:stream"]
)
def test_install_prepared_package_rejects_cross_platform_invalid_member(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    """解压前拒绝在 Windows 上无效或具有特殊语义的名称。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(
        tmp_path / "package.zip", {"MyTool.exe": "new", unsafe_name: "invalid"}
    )
    manifest = _manifest(package_path, version="1.1.0")

    with pytest.raises(UpdateError) as error:
        install_prepared_package(
            install_root=install_root, package_path=package_path, manifest=manifest
        )

    assert error.value.code is UpdateErrorCode.PACKAGE_LAYOUT_INVALID
    assert not (install_root / "releases" / "1.1.0").exists()


@pytest.mark.parametrize(
    "members",
    [
        {"MyTool.exe": "new", "Config.json": "first", "config.json": "second"},
        {"MyTool.exe": "new", "资源/é.txt": "first", "资源/e\u0301.txt": "second"},
    ],
)
def test_install_prepared_package_rejects_portable_name_collision(
    tmp_path: Path,
    members: dict[str, str],
) -> None:
    """大小写或 Unicode 等价名称不得在目标文件系统静默覆盖。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(tmp_path / "package.zip", members)
    manifest = _manifest(package_path, version="1.1.0")

    with pytest.raises(UpdateError) as error:
        install_prepared_package(
            install_root=install_root, package_path=package_path, manifest=manifest
        )

    assert error.value.code is UpdateErrorCode.PACKAGE_LAYOUT_INVALID
    assert "collision" in error.value.message


@pytest.mark.parametrize(
    "unsafe_name",
    [f"{'a' * 260}.txt", "/".join(["路径段" * 20] * 12) + "/文件.txt"],
)
def test_install_prepared_package_rejects_overlong_member_before_extraction(
    tmp_path: Path,
    unsafe_name: str,
) -> None:
    """组件或完整相对路径超过跨平台预算时应返回专用错误。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(
        tmp_path / "package.zip", {"MyTool.exe": "new", unsafe_name: "too long"}
    )
    manifest = _manifest(package_path, version="1.1.0")

    with pytest.raises(UpdateError) as error:
        install_prepared_package(
            install_root=install_root, package_path=package_path, manifest=manifest
        )

    assert error.value.code is UpdateErrorCode.PACKAGE_PATH_TOO_LONG
    assert not list(install_root.glob(".update-*.tmp"))


def test_install_prepared_package_preserves_chinese_member_names(
    tmp_path: Path,
) -> None:
    """合法中文目录与文件名应无损安装。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    package_path = _write_package(
        tmp_path / "package.zip",
        {"MyTool.exe": "new", "资源/更新说明.txt": "中文内容"},
    )
    manifest = _manifest(package_path, version="1.1.0")

    install_prepared_package(
        install_root=install_root, package_path=package_path, manifest=manifest
    )

    installed = install_root / "releases" / "1.1.0" / "资源" / "更新说明.txt"
    assert installed.read_text(encoding="utf-8") == "中文内容"


def test_install_and_dry_run_share_the_same_package_plan_rejection(
    tmp_path: Path,
) -> None:
    """dry-run 与真实安装必须返回相同布局错误且都不创建 staging。"""
    package_path = _write_package(
        tmp_path / "package.zip",
        {"MyTool.exe": "new", "Config.json": "first", "config.json": "second"},
    )
    manifest = _manifest(package_path, version="1.1.0")
    error_codes: list[UpdateErrorCode] = []

    for name, dry_run in (("dry", True), ("install", False)):
        install_root = _write_install_root(
            tmp_path / name, current_version="1.0.0", entry_name="MyTool.exe"
        )
        with pytest.raises(UpdateError) as error:
            install_prepared_package(
                install_root=install_root,
                package_path=package_path,
                manifest=manifest,
                dry_run=dry_run,
            )
        error_codes.append(error.value.code)
        assert not list(install_root.glob(".update-*.tmp"))

    assert error_codes == [
        UpdateErrorCode.PACKAGE_LAYOUT_INVALID,
        UpdateErrorCode.PACKAGE_LAYOUT_INVALID,
    ]


def test_switch_installed_release_normalizes_release_dir_install_root(
    tmp_path: Path,
) -> None:
    """验证 runtime 本地切换可纠正误传的 release 目录。

    :param tmp_path: pytest 临时目录
    :return: None
    """
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    _write_release_entry(install_root, "1.0.1", "MyTool.exe", "new")

    result = switch_installed_release(
        install_root=install_root / "releases" / "1.0.0", version="1.0.1"
    )

    current_payload = json.loads(
        (install_root / "current.json").read_text(encoding="utf-8")
    )
    assert result.version == "1.0.1"
    assert current_payload["version"] == "1.0.1"


def test_launch_current_uses_open_n_for_macos_app_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证 macOS 上 launch_current 会使用 open -n 启动 .app。"""
    install_root = _write_install_root_with_app_bundle(
        tmp_path, current_version="1.0.0", entry_name="MyTool.app"
    )

    launched: dict[str, object] = {}

    class FakeProcess:
        pid = 1234

    def fake_popen(
        args: list[str], cwd: str | None = None, close_fds: bool = False
    ) -> object:
        launched["args"] = tuple(args)
        launched["cwd"] = cwd
        launched["close_fds"] = close_fds
        return FakeProcess()

    monkeypatch.setattr(runtime.sys, "platform", "darwin")
    monkeypatch.setattr(runtime.subprocess, "Popen", fake_popen)

    runtime.launch_current(install_root=install_root)

    assert launched["args"] == (
        runtime._macos_open_executable(),
        "-n",
        str(install_root / "releases" / "1.0.0" / "MyTool.app"),
    )
    assert launched["cwd"] == str(
        (install_root / "releases" / "1.0.0" / "MyTool.app").parent
    )
    assert launched["close_fds"] is True


def test_launch_current_normalizes_release_dir_install_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证 launch_current 可纠正误传的 release 目录。

    :param tmp_path: pytest 临时目录
    :param monkeypatch: pytest monkeypatch
    :return: None
    """
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    launched: dict[str, object] = {}

    class FakeProcess:
        pid = 4321

    def fake_popen(
        args: list[str], cwd: str | None = None, close_fds: bool = False
    ) -> object:
        launched["args"] = tuple(args)
        launched["cwd"] = cwd
        launched["close_fds"] = close_fds
        return FakeProcess()

    monkeypatch.setattr(runtime.sys, "platform", "win32")
    monkeypatch.setattr(runtime.subprocess, "Popen", fake_popen)

    process = runtime.launch_current(install_root=install_root / "releases" / "1.0.0")

    assert process.pid == 4321
    assert launched["args"] == (
        str(install_root / "releases" / "1.0.0" / "MyTool.exe"),
    )
    assert launched["cwd"] == str(install_root / "releases" / "1.0.0")


def test_launch_current_falls_back_to_entry_path_when_executable_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """验证 current.json 缺少 executable 时回退到 entry.path。"""
    install_root = _write_install_root_with_app_bundle(
        tmp_path, current_version="1.0.0", entry_name="MyTool.app"
    )
    current_path = install_root / "current.json"
    payload = json.loads(current_path.read_text(encoding="utf-8"))
    payload.pop("executable", None)
    current_path.write_text(json.dumps(payload), encoding="utf-8")

    launched: list[tuple[str, ...]] = []

    def fake_popen(
        args: list[str], cwd: str | None = None, close_fds: bool = False
    ) -> object:
        launched.append(tuple(args))

        class FakeProcess:
            pid = 4321

        return FakeProcess()

    monkeypatch.setattr(runtime.sys, "platform", "darwin")
    monkeypatch.setattr(runtime.subprocess, "Popen", fake_popen)

    runtime.launch_current(install_root=install_root)

    assert launched
    # 使用 .app 启动时，首参数应包含 open -n 命令。
    assert launched[0] == (
        runtime._macos_open_executable(),
        "-n",
        str(install_root / "releases" / "1.0.0" / "MyTool.app"),
    )


def test_launch_current_rejects_release_dir_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python Bootstrap 不得启动安装根之外的可执行文件。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "MyTool.exe").write_text("outside", encoding="utf-8")
    current_path = install_root / "current.json"
    payload = json.loads(current_path.read_text(encoding="utf-8"))
    payload["release_dir"] = "../outside"
    current_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(
        runtime.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("must not launch"),
    )

    with pytest.raises(UpdateError) as error:
        runtime.launch_current(install_root=install_root)

    assert error.value.code is UpdateErrorCode.SETTINGS_INVALID


@pytest.mark.skipif(os.name == "nt", reason="symlink 创建权限依赖 Windows 环境")
def test_launch_current_rejects_entry_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Python Bootstrap 必须与 Rust Bootstrap 一样拒绝入口 symlink 逃逸。"""
    install_root = _write_install_root(
        tmp_path, current_version="1.0.0", entry_name="MyTool.exe"
    )
    entry = install_root / "releases" / "1.0.0" / "MyTool.exe"
    outside = tmp_path / "outside.exe"
    outside.write_text("outside", encoding="utf-8")
    entry.unlink()
    entry.symlink_to(outside)
    monkeypatch.setattr(
        runtime.subprocess,
        "Popen",
        lambda *args, **kwargs: pytest.fail("must not launch"),
    )

    with pytest.raises(UpdateError) as error:
        runtime.launch_current(install_root=install_root)

    assert error.value.code is UpdateErrorCode.SETTINGS_INVALID


def _write_install_root(
    tmp_path: Path, *, current_version: str, entry_name: str
) -> Path:
    """写入测试安装根。"""
    install_root = tmp_path / "install"
    release_dir = install_root / "releases" / current_version
    release_dir.mkdir(parents=True)
    (release_dir / entry_name).write_text("current", encoding="utf-8")
    (install_root / "current.json").write_text(
        json.dumps(
            {
                "app_id": "my-tool",
                "version": current_version,
                "release_dir": f"releases/{current_version}",
                "executable": entry_name,
                "entry": {
                    "kind": "executable",
                    "path": entry_name,
                    "platform": "windows",
                },
            }
        ),
        encoding="utf-8",
    )
    return install_root


def _write_install_root_with_app_bundle(
    tmp_path: Path,
    *,
    current_version: str,
    entry_name: str = "MyTool.app",
) -> Path:
    """写入包含 macOS .app 的测试安装根。"""
    install_root = _write_install_root(
        tmp_path, current_version=current_version, entry_name=entry_name
    )
    release_dir = install_root / "releases" / current_version / entry_name
    if release_dir.exists():
        if release_dir.is_dir():
            shutil.rmtree(release_dir)
        else:
            release_dir.unlink()
    release_dir.mkdir(parents=True)
    binary_path = release_dir / "Contents" / "MacOS" / "MyTool"
    binary_path.parent.mkdir(parents=True, exist_ok=True)
    binary_path.write_text("current", encoding="utf-8")

    current_payload = json.loads(
        (install_root / "current.json").read_text(encoding="utf-8")
    )
    current_payload.update(
        {
            "entry": {
                "kind": "app_bundle",
                "path": entry_name,
                "platform": "macos",
            }
        }
    )
    current_payload["executable"] = entry_name
    current_payload["version"] = current_version
    current_payload["release_dir"] = f"releases/{current_version}"
    (install_root / "current.json").write_text(
        json.dumps(current_payload), encoding="utf-8"
    )
    return install_root


def _write_release_entry(
    install_root: Path, version: str, entry_name: str, content: str
) -> None:
    """写入一个已安装 release 入口。"""
    release_dir = install_root / "releases" / version
    release_dir.mkdir(parents=True, exist_ok=True)
    (release_dir / entry_name).write_text(content, encoding="utf-8")


def _write_package(path: Path, files: dict[str, str]) -> Path:
    """写入测试 zip 包。"""
    with zipfile.ZipFile(path, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return path


def _write_zip_symlink(
    archive: zipfile.ZipFile, member_name: str, link_target: str
) -> None:
    """向 zip 写入 POSIX symlink 成员。

    :param archive: 待写入的 zip 包。
    :param member_name: symlink 成员路径。
    :param link_target: symlink 指向的相对路径。
    :return: None
    """
    info = zipfile.ZipInfo(member_name)
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    archive.writestr(info, link_target.encode("utf-8"))


def _manifest(package_path: Path, *, version: str) -> UpdateManifest:
    """构建测试 manifest。"""
    payload = {
        "schema_version": 2,
        "app_id": "my-tool",
        "channel": "stable",
        "version": version,
        "mandatory": False,
        "min_supported_version": "1.0.0",
        "published_at": "2026-06-18T00:00:00+00:00",
        "notes": f"v{version}",
        "platform": "windows",
        "package": {
            "url": "my-tool/package.zip",
            "size": package_path.stat().st_size,
            "sha256": hashlib.sha256(package_path.read_bytes()).hexdigest(),
        },
    }
    return UpdateManifest.from_payload(payload)
