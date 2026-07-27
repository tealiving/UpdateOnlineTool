"""跨平台 release package plan 测试矩阵。"""

from __future__ import annotations

import stat
import sys
import zipfile
import hashlib
from pathlib import Path

import pytest

from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.release_package import ReleasePackagePlan


@pytest.mark.parametrize("platform", ["windows", "macos", "linux"])
def test_release_package_plan_accepts_chinese_paths_on_all_platforms(
    tmp_path: Path,
    platform: str,
) -> None:
    """中文不是非法条件，三个目标平台应使用同一规范化结果。"""
    package = tmp_path / f"{platform}.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("资源/更新说明.txt", "内容")

    plan = ReleasePackagePlan.from_zip(
        package,
        platform=platform,
        extraction_root=Path("C:/UOT/staging")
        if platform == "windows"
        else Path("/opt/uot/staging"),
    )

    assert plan.members[0].relative_path == "资源/更新说明.txt"


def test_release_package_plan_normalizes_a_single_decomposed_unicode_name(
    tmp_path: Path,
) -> None:
    """单个 NFD 名称可规范成 NFC；只有等价名称并存时才是冲突。"""
    package = tmp_path / "unicode.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("资源/e\u0301.txt", "内容")

    plan = ReleasePackagePlan.from_zip(package)

    assert plan.members[0].relative_path == "资源/é.txt"


def test_release_package_plan_rejects_file_directory_conflict(tmp_path: Path) -> None:
    """文件不得同时作为其他成员的父目录。"""
    package = tmp_path / "conflict.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("resources", "file")
        archive.writestr("resources/config.json", "{}")

    with pytest.raises(UpdateError) as error:
        ReleasePackagePlan.from_zip(package)

    assert error.value.code is UpdateErrorCode.PACKAGE_LAYOUT_INVALID
    assert "collision" in error.value.message


def test_release_package_plan_applies_windows_destination_budget(
    tmp_path: Path,
) -> None:
    """Windows profile 应在解压前计算完整目标路径 UTF-16 预算。"""
    package = tmp_path / "windows.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("app.exe", "binary")
    long_root = Path("C:/") / ("r" * 250)

    with pytest.raises(UpdateError) as error:
        ReleasePackagePlan.from_zip(
            package,
            platform="windows",
            extraction_root=long_root,
        )

    assert error.value.code is UpdateErrorCode.PACKAGE_PATH_TOO_LONG
    assert "Windows extraction path" in error.value.message


def test_release_package_plan_uses_host_budget_when_manifest_platform_is_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """旧 manifest 未声明平台时，完整目标路径必须使用当前宿主预算。"""
    package = tmp_path / "host.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("app.exe", "binary")
    monkeypatch.setattr(sys, "platform", "win32")
    long_root = Path("C:/") / ("r" * 250)

    with pytest.raises(UpdateError) as error:
        ReleasePackagePlan.from_zip(package, extraction_root=long_root)

    assert error.value.code is UpdateErrorCode.PACKAGE_PATH_TOO_LONG
    assert "Windows extraction path" in error.value.message


def test_release_package_plan_rejects_unknown_target_platform(
    tmp_path: Path,
) -> None:
    """未知平台不得回退到最宽松的 Linux 路径预算。"""
    package = tmp_path / "unknown-platform.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("app.exe", "binary")

    with pytest.raises(UpdateError) as error:
        ReleasePackagePlan.from_zip(package, platform="win32")

    assert error.value.code is UpdateErrorCode.MANIFEST_INVALID


@pytest.mark.parametrize(
    "name",
    ["CONIN$", "CONOUT$", "COM¹", "COM²", "COM³", "LPT¹", "LPT²", "LPT³"],
)
def test_release_package_plan_rejects_extended_windows_reserved_names(
    tmp_path: Path,
    name: str,
) -> None:
    """Win32 扩展设备名同样不能进入跨平台 release。"""
    package = tmp_path / "reserved.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(name, "binary")

    with pytest.raises(UpdateError) as error:
        ReleasePackagePlan.from_zip(package)

    assert error.value.code is UpdateErrorCode.PACKAGE_LAYOUT_INVALID


def test_release_package_plan_rejects_overlong_symlink_target_before_reading_it(
    tmp_path: Path,
) -> None:
    """symlink 目标也必须受相对路径预算约束，不能延迟成系统解压错误。"""
    package = tmp_path / "symlink.zip"
    info = zipfile.ZipInfo("links/runtime")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr(info, ("a/" * 600) + "runtime")

    with pytest.raises(UpdateError) as error:
        ReleasePackagePlan.from_zip(package)

    assert error.value.code is UpdateErrorCode.PACKAGE_PATH_TOO_LONG
    assert "symlink target" in error.value.message


def test_release_package_plan_rejects_empty_zip(tmp_path: Path) -> None:
    """空包不得进入发布或安装阶段。"""
    package = tmp_path / "empty.zip"
    with zipfile.ZipFile(package, "w"):
        pass

    with pytest.raises(UpdateError) as error:
        ReleasePackagePlan.from_zip(package)

    assert error.value.code is UpdateErrorCode.PACKAGE_LAYOUT_INVALID


def test_release_package_plan_requires_regular_file_entry(tmp_path: Path) -> None:
    """同名目录不能冒充可执行入口通过 dry-run。"""
    package = tmp_path / "directory-entry.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("MyTool.exe/", b"")

    plan = ReleasePackagePlan.from_zip(package)

    with pytest.raises(UpdateError) as error:
        plan.require_entry("MyTool.exe")

    assert error.value.code is UpdateErrorCode.SETTINGS_INVALID


def test_release_package_plan_requires_direct_macos_bundle_executable(
    tmp_path: Path,
) -> None:
    """嵌套文件不能冒充 Contents/MacOS 的直接可执行入口。"""
    package = tmp_path / "nested-app-entry.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("MyTool.app/Contents/MacOS/helpers/tool", "binary")

    plan = ReleasePackagePlan.from_zip(package)

    with pytest.raises(UpdateError):
        plan.require_entry("MyTool.app")


def test_release_package_plan_normalizes_symlink_target_to_nfc(
    tmp_path: Path,
) -> None:
    """symlink 目标必须与成员名称使用同一 Unicode 规范形式。"""
    package = tmp_path / "unicode-symlink.zip"
    link = zipfile.ZipInfo("links/current")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("资源/e\u0301.txt", "内容")
        archive.writestr(link, "../资源/e\u0301.txt")

    plan = ReleasePackagePlan.from_zip(package)

    assert plan.members[1].link_target == "../资源/é.txt"


def test_release_package_plan_rejects_excessive_compression_ratio(
    tmp_path: Path,
) -> None:
    """高压缩比成员必须在实际解压前被资源预算拒绝。"""
    package = tmp_path / "zip-bomb.zip"
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("payload.bin", b"\0" * (2 * 1024 * 1024))

    with pytest.raises(UpdateError) as error:
        ReleasePackagePlan.from_zip(package)

    assert error.value.code is UpdateErrorCode.PACKAGE_LAYOUT_INVALID
    assert "compression ratio" in error.value.message


def test_release_package_plan_rechecks_expected_hash_before_extract(
    tmp_path: Path,
) -> None:
    """计划生成后替换包时，不得解压未经 manifest 授权的内容。"""
    package = tmp_path / "package.zip"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("app.exe", "trusted")
    expected = package.read_bytes()
    plan = ReleasePackagePlan.from_zip(
        package,
        expected_size=len(expected),
        expected_sha256=hashlib.sha256(expected).hexdigest(),
    )
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("app.exe", "evil!!!")

    with pytest.raises(UpdateError) as error:
        plan.extract(tmp_path / "release")

    assert error.value.code is UpdateErrorCode.PACKAGE_HASH_MISMATCH
