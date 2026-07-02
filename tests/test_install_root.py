"""安装根解析测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.install_root import missing_current_json_message, normalize_install_root, resolve_install_root


def test_normalize_install_root_keeps_standard_root(tmp_path: Path) -> None:
    """验证标准安装根不会被改写。

    :param tmp_path: pytest 临时目录
    :return: None
    """
    install_root = tmp_path / "install"
    install_root.mkdir()
    (install_root / "current.json").write_text("{}", encoding="utf-8")

    resolution = resolve_install_root(install_root)

    assert resolution.normalized == install_root
    assert resolution.normalized_from_release_dir is False
    assert normalize_install_root(install_root) == install_root


def test_normalize_install_root_accepts_release_dir_when_parent_has_current_json(tmp_path: Path) -> None:
    """验证误传 releases/<version> 时可回到安装根。

    :param tmp_path: pytest 临时目录
    :return: None
    """
    install_root = tmp_path / "install"
    release_dir = install_root / "releases" / "1.2.7"
    release_dir.mkdir(parents=True)
    (install_root / "current.json").write_text("{}", encoding="utf-8")

    resolution = resolve_install_root(release_dir)

    assert resolution.requested == release_dir
    assert resolution.normalized == install_root
    assert resolution.looked_like_release_dir is True
    assert resolution.normalized_from_release_dir is True
    assert normalize_install_root(release_dir) == install_root


def test_normalize_install_root_rejects_release_dir_without_parent_current_json(tmp_path: Path) -> None:
    """验证 release 目录无法纠偏时输出明确建议。

    :param tmp_path: pytest 临时目录
    :return: None
    """
    install_root = tmp_path / "install"
    release_dir = install_root / "releases" / "1.2.7"
    release_dir.mkdir(parents=True)

    with pytest.raises(UpdateError) as error:
        normalize_install_root(release_dir)

    assert error.value.code is UpdateErrorCode.MANIFEST_NOT_FOUND
    assert "version release directory" in str(error.value)
    assert str(install_root) in str(error.value)


def test_missing_current_json_message_mentions_release_dir_hint(tmp_path: Path) -> None:
    """验证 current.json 缺失提示可识别 release 目录。

    :param tmp_path: pytest 临时目录
    :return: None
    """
    release_dir = tmp_path / "install" / "releases" / "1.2.7"

    message = missing_current_json_message(release_dir)

    assert "version release directory" in message
    assert str(tmp_path / "install") in message
