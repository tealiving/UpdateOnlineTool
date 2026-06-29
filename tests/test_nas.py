"""NAS 路径解析测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from update_online_tool import UpdateError, UpdateErrorCode
from update_online_tool.nas import NasReleaseSource


def test_nas_resolves_manifest_path(tmp_path: Path) -> None:
    """验证 app/channel manifest 路径解析。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    source = NasReleaseSource(tmp_path)

    assert source.manifest_path("demo-app", "stable") == tmp_path / "demo-app" / "stable" / "latest.json"


def test_nas_resolves_channel_platform_version_manifest_path(tmp_path: Path) -> None:
    """验证通道和平台版本 manifest 路径解析。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    source = NasReleaseSource(tmp_path)

    assert (
        source.version_manifest_path("demo-app", "1.0.6", "macos", "beta")
        == tmp_path / "demo-app" / "beta" / "v1.0.6" / "macos" / "latest.json"
    )


def test_nas_iterates_channel_version_manifest_paths(tmp_path: Path) -> None:
    """验证可扫描指定通道历史版本 manifest。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    source = NasReleaseSource(tmp_path)
    first = tmp_path / "demo-app" / "stable" / "v1.0.5" / "latest.json"
    second = tmp_path / "demo-app" / "stable" / "v1.0.6" / "latest.json"
    ignored = tmp_path / "demo-app" / "beta" / "v1.0.6" / "latest.json"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    ignored.parent.mkdir(parents=True)
    first.write_text("{}", encoding="utf-8")
    second.write_text("{}", encoding="utf-8")
    ignored.write_text("{}", encoding="utf-8")

    assert source.iter_version_manifest_paths("demo-app", channel="stable") == [first, second]


def test_nas_keeps_legacy_version_manifest_fallback(tmp_path: Path) -> None:
    """验证旧版全局版本目录仍可被扫描。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    source = NasReleaseSource(tmp_path)
    legacy = tmp_path / "demo-app" / "v1.0.5" / "latest.json"
    legacy.parent.mkdir(parents=True)
    legacy.write_text("{}", encoding="utf-8")

    assert source.iter_version_manifest_paths("demo-app", channel="stable") == [legacy]


def test_nas_merges_channel_and_legacy_versions_without_duplicate(tmp_path: Path) -> None:
    """验证新旧版本目录共存时同版本优先通道目录。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    source = NasReleaseSource(tmp_path)
    channel_current = tmp_path / "demo-app" / "stable" / "v1.0.6" / "latest.json"
    legacy_same = tmp_path / "demo-app" / "v1.0.6" / "latest.json"
    legacy_old = tmp_path / "demo-app" / "v1.0.5" / "latest.json"
    for path in (channel_current, legacy_same, legacy_old):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")

    paths = source.iter_version_manifest_paths("demo-app", channel="stable")
    assert set(paths) == {legacy_old, channel_current}
    assert legacy_same not in paths


def test_nas_rejects_parent_relative_package_url(tmp_path: Path) -> None:
    """验证 package.url 不允许跳出 NAS 根目录。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    source = NasReleaseSource(tmp_path)

    with pytest.raises(UpdateError) as error:
        source.resolve_package_path("../secret.zip")

    assert error.value.code is UpdateErrorCode.MANIFEST_INVALID


def test_nas_rejects_windows_or_unc_package_url(tmp_path: Path) -> None:
    """验证 package.url 不能写成 Windows/UNC 路径。"""
    source = NasReleaseSource(tmp_path)

    with pytest.raises(UpdateError) as error:
        source.resolve_package_path(r"\\server\share\package.zip")

    assert error.value.code is UpdateErrorCode.MANIFEST_INVALID


def test_nas_rejects_drive_letter_package_url(tmp_path: Path) -> None:
    """验证 package.url 不能写成盘符绝对路径。"""
    source = NasReleaseSource(tmp_path)

    with pytest.raises(UpdateError) as error:
        source.resolve_package_path("C:/packages/package.zip")

    assert error.value.code is UpdateErrorCode.MANIFEST_INVALID
