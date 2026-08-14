"""包入口测试。"""

from __future__ import annotations

from pathlib import Path
import tomllib

import update_online_tool
from update_online_tool import UpdateError, UpdateErrorCode


def test_public_package_exports_version_and_errors() -> None:
    """验证包入口导出版本与错误类型。

    :return: None
    """
    project = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    assert update_online_tool.__version__ == project["version"] == "0.2.7"
    assert UpdateErrorCode.MANIFEST_INVALID.value == "MANIFEST_INVALID"
    assert (
        str(UpdateError(UpdateErrorCode.MANIFEST_INVALID, "bad manifest"))
        == "MANIFEST_INVALID: bad manifest"
    )
