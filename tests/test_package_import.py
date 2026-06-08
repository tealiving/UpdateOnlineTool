"""包入口测试。"""

from __future__ import annotations

import update_online_tool
from update_online_tool import UpdateError, UpdateErrorCode


def test_public_package_exports_version_and_errors() -> None:
    """验证包入口导出版本与错误类型。

    :return: None
    """
    assert isinstance(update_online_tool.__version__, str)
    assert UpdateErrorCode.MANIFEST_INVALID.value == "MANIFEST_INVALID"
    assert str(UpdateError(UpdateErrorCode.MANIFEST_INVALID, "bad manifest")) == "MANIFEST_INVALID: bad manifest"
