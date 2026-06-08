"""manifest 契约测试。"""

from __future__ import annotations

import pytest

from update_online_tool import UpdateError, UpdateErrorCode
from update_online_tool.manifest import UpdateManifest


def _payload() -> dict[str, object]:
    """构造有效 manifest 载荷。

    :return: manifest 字典。
    """
    return {
        "schema_version": 2,
        "app_id": "automation-manual-studio",
        "channel": "stable",
        "version": "1.0.6",
        "mandatory": False,
        "min_supported_version": "1.0.0",
        "published_at": "2026-06-08T00:00:00+00:00",
        "notes": "release",
        "package": {
            "url": "automation-manual-studio/v1.0.6/package.zip",
            "size": 7,
            "sha256": "0" * 64,
        },
    }


def test_manifest_parses_v2_payload() -> None:
    """验证 v2 manifest 可解析为模型。

    :return: None
    """
    manifest = UpdateManifest.from_payload(_payload())

    assert manifest.app_id == "automation-manual-studio"
    assert manifest.package.url == "automation-manual-studio/v1.0.6/package.zip"
    assert manifest.to_payload()["version"] == "1.0.6"


def test_manifest_rejects_ifw_fields() -> None:
    """验证第一版契约拒绝 IFW 字段。

    :return: None
    """
    payload = _payload()
    payload["installer_kind"] = "qt_ifw"

    with pytest.raises(UpdateError) as error:
        UpdateManifest.from_payload(payload)

    assert error.value.code is UpdateErrorCode.MANIFEST_INVALID


def test_manifest_rejects_bad_sha256() -> None:
    """验证 sha256 格式错误会被拒绝。

    :return: None
    """
    payload = _payload()
    package = dict(payload["package"])  # type: ignore[arg-type]
    package["sha256"] = "bad"
    payload["package"] = package

    with pytest.raises(UpdateError) as error:
        UpdateManifest.from_payload(payload)

    assert error.value.code is UpdateErrorCode.MANIFEST_INVALID
