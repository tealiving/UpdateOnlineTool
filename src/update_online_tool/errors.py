"""在线升级结构化错误。"""

from __future__ import annotations

from enum import Enum


class UpdateErrorCode(str, Enum):
    """在线升级错误码。"""

    NAS_SOURCE_UNAVAILABLE = "NAS_SOURCE_UNAVAILABLE"
    SETTINGS_INVALID = "SETTINGS_INVALID"
    MANIFEST_NOT_FOUND = "MANIFEST_NOT_FOUND"
    MANIFEST_INVALID = "MANIFEST_INVALID"
    UPDATE_NOT_AVAILABLE = "UPDATE_NOT_AVAILABLE"
    PACKAGE_NOT_FOUND = "PACKAGE_NOT_FOUND"
    PACKAGE_SIZE_MISMATCH = "PACKAGE_SIZE_MISMATCH"
    PACKAGE_HASH_MISMATCH = "PACKAGE_HASH_MISMATCH"
    UPDATER_NOT_FOUND = "UPDATER_NOT_FOUND"
    UPDATER_LAUNCH_FAILED = "UPDATER_LAUNCH_FAILED"
    OPERATION_CANCELLED = "OPERATION_CANCELLED"


class UpdateError(RuntimeError):
    """在线升级异常。

    :param code: 结构化错误码。
    :param message: 可展示或记录的错误消息。
    :return: None
    """

    def __init__(self, code: UpdateErrorCode, message: str) -> None:
        """保存错误码和消息。

        :param code: 结构化错误码。
        :param message: 错误消息。
        :return: None
        """
        self.code = code
        self.message = message
        super().__init__(f"{code.value}: {message}")
