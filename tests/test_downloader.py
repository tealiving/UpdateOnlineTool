"""包复制和校验测试。"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from update_online_tool import UpdateError, UpdateErrorCode
from update_online_tool.downloader import CancellationToken, copy_package_with_verification


def test_copy_package_reports_progress_and_verifies_hash(tmp_path: Path) -> None:
    """验证包复制会报告进度并校验 sha256。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    source = tmp_path / "source.zip"
    target = tmp_path / "downloads" / "package.zip"
    source.write_bytes(b"release")
    progress: list[tuple[int, int]] = []

    result = copy_package_with_verification(
        source_path=source,
        target_path=target,
        expected_size=7,
        expected_sha256=hashlib.sha256(b"release").hexdigest(),
        progress=lambda current, total: progress.append((current, total)),
    )

    assert result.package_path == target
    assert result.verified is True
    assert target.read_bytes() == b"release"
    assert progress[-1] == (7, 7)


def test_copy_package_rejects_hash_mismatch(tmp_path: Path) -> None:
    """验证 sha256 不匹配会失败。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    source = tmp_path / "source.zip"
    source.write_bytes(b"release")

    with pytest.raises(UpdateError) as error:
        copy_package_with_verification(
            source_path=source,
            target_path=tmp_path / "package.zip",
            expected_size=7,
            expected_sha256="0" * 64,
        )

    assert error.value.code is UpdateErrorCode.PACKAGE_HASH_MISMATCH


def test_copy_package_honors_cancellation(tmp_path: Path) -> None:
    """验证取消令牌会中止复制。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    source = tmp_path / "source.zip"
    source.write_bytes(b"x" * 4096)
    token = CancellationToken()
    calls = 0

    def progress(current: int, total: int) -> None:
        """首次进度回调后取消。

        :param current: 已复制字节数。
        :param total: 总字节数。
        :return: None
        """
        nonlocal calls
        calls += 1
        token.cancel()

    with pytest.raises(UpdateError) as error:
        copy_package_with_verification(
            source_path=source,
            target_path=tmp_path / "package.zip",
            expected_size=4096,
            expected_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
            progress=progress,
            cancellation_token=token,
            chunk_size=1024,
        )

    assert calls == 1
    assert error.value.code is UpdateErrorCode.OPERATION_CANCELLED
