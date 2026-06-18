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


def test_nas_resolves_platform_version_manifest_path(tmp_path: Path) -> None:
    """验证平台版本 manifest 路径解析。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    source = NasReleaseSource(tmp_path)

    assert (
        source.version_manifest_path("demo-app", "1.0.6", "macos")
        == tmp_path / "demo-app" / "v1.0.6" / "macos" / "latest.json"
    )


def test_nas_iterates_version_manifest_paths(tmp_path: Path) -> None:
    """验证可扫描历史版本 manifest。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    source = NasReleaseSource(tmp_path)
    first = tmp_path / "demo-app" / "v1.0.5" / "latest.json"
    second = tmp_path / "demo-app" / "v1.0.6" / "latest.json"
    ignored = tmp_path / "demo-app" / "stable" / "latest.json"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    ignored.parent.mkdir(parents=True)
    first.write_text("{}", encoding="utf-8")
    second.write_text("{}", encoding="utf-8")
    ignored.write_text("{}", encoding="utf-8")

    assert source.iter_version_manifest_paths("demo-app") == [first, second]


def test_nas_rejects_parent_relative_package_url(tmp_path: Path) -> None:
    """验证 package.url 不允许跳出 NAS 根目录。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    source = NasReleaseSource(tmp_path)

    with pytest.raises(UpdateError) as error:
        source.resolve_package_path("../secret.zip")

    assert error.value.code is UpdateErrorCode.MANIFEST_INVALID
