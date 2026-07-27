"""NAS 包复制和校验。"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from update_online_tool.errors import UpdateError, UpdateErrorCode

ProgressCallback = Callable[[int, int], None]
PackageValidator = Callable[[Path], None]


class CancellationToken:
    """复制取消令牌。"""

    def __init__(self) -> None:
        """初始化未取消状态。

        :return: None
        """
        self._cancelled = False

    @property
    def cancelled(self) -> bool:
        """返回是否已取消。

        :return: 已取消返回 True。
        """
        return self._cancelled

    def cancel(self) -> None:
        """标记取消。

        :return: None
        """
        self._cancelled = True


@dataclass(frozen=True)
class PreparedPackage:
    """已准备好的升级包。

    :param package_path: 本地包路径。
    :param sha256: 已计算 SHA-256。
    :param verified: 是否校验通过。
    :return: None
    """

    package_path: Path
    sha256: str
    verified: bool


def copy_package_with_verification(
    *,
    source_path: Path,
    target_path: Path,
    expected_size: int,
    expected_sha256: str,
    progress: ProgressCallback | None = None,
    cancellation_token: CancellationToken | None = None,
    validator: PackageValidator | None = None,
    chunk_size: int = 1024 * 1024,
) -> PreparedPackage:
    """复制包并校验大小和 SHA-256。

    :param source_path: NAS 源包路径。
    :param target_path: 本地目标路径。
    :param expected_size: manifest 中声明的大小。
    :param expected_sha256: manifest 中声明的 SHA-256。
    :param progress: 进度回调，参数为已复制字节和总字节。
    :param cancellation_token: 取消令牌。
    :param validator: 临时包校验回调，仅在原子替换目标前执行。
    :param chunk_size: 复制块大小。
    :return: 已准备包信息。
    """
    source_path = Path(source_path)
    target_path = Path(target_path)
    if not source_path.is_file():
        raise UpdateError(
            UpdateErrorCode.PACKAGE_NOT_FOUND, f"package not found: {source_path}"
        )
    if source_path.stat().st_size != expected_size:
        raise UpdateError(
            UpdateErrorCode.PACKAGE_SIZE_MISMATCH,
            f"package size mismatch before copy: {source_path}",
        )

    target_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = (
        target_path.parent / f".{target_path.name}.{os.getpid()}.{uuid4().hex}.tmp"
    )
    digest = hashlib.sha256()
    copied_bytes = 0
    try:
        with source_path.open("rb") as source_file, temp_path.open("wb") as target_file:
            while True:
                if cancellation_token is not None and cancellation_token.cancelled:
                    raise UpdateError(
                        UpdateErrorCode.OPERATION_CANCELLED, "package copy cancelled"
                    )
                chunk = source_file.read(chunk_size)
                if not chunk:
                    break
                target_file.write(chunk)
                digest.update(chunk)
                copied_bytes += len(chunk)
                if progress is not None:
                    progress(copied_bytes, expected_size)
                if cancellation_token is not None and cancellation_token.cancelled:
                    raise UpdateError(
                        UpdateErrorCode.OPERATION_CANCELLED, "package copy cancelled"
                    )

        actual_sha256 = digest.hexdigest()
        if copied_bytes != expected_size:
            raise UpdateError(
                UpdateErrorCode.PACKAGE_SIZE_MISMATCH,
                f"package.size {expected_size} != actual {copied_bytes}",
            )
        if actual_sha256.lower() != expected_sha256.lower():
            raise UpdateError(
                UpdateErrorCode.PACKAGE_HASH_MISMATCH,
                f"package.sha256 {expected_sha256.lower()} != actual {actual_sha256}",
            )
        if validator is not None:
            validator(temp_path)
        temp_path.replace(target_path)
        return PreparedPackage(
            package_path=target_path, sha256=actual_sha256, verified=True
        )
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise
