"""UpdateService 测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from update_online_tool.service import UpdateService
from update_online_tool.settings import UpdateToolSettings
from update_online_tool.versioning import UpdateDecision


def _write_manifest(root: Path, *, version: str, content: bytes = b"release") -> None:
    """写入 NAS 模拟 manifest 和 package。

    :param root: NAS 根目录。
    :param version: 版本号。
    :param content: 包内容。
    :return: None
    """
    package = root / "automation-manual-studio" / f"v{version}" / "package.zip"
    latest = root / "automation-manual-studio" / "stable" / "latest.json"
    package.parent.mkdir(parents=True)
    latest.parent.mkdir(parents=True)
    package.write_bytes(content)
    latest.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "app_id": "automation-manual-studio",
                "channel": "stable",
                "version": version,
                "mandatory": False,
                "min_supported_version": "1.0.0",
                "published_at": "2026-06-08T00:00:00+00:00",
                "notes": "release",
                "package": {
                    "url": f"automation-manual-studio/v{version}/package.zip",
                    "size": len(content),
                    "sha256": hashlib.sha256(content).hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )


def test_service_check_returns_optional_update(tmp_path: Path) -> None:
    """验证 check 返回可选升级。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    _write_manifest(tmp_path, version="1.0.6")
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))

    result = service.check(
        app_id="automation-manual-studio",
        current_version="1.0.5",
        channel="stable",
    )

    assert result.decision is UpdateDecision.OPTIONAL_UPDATE
    assert result.manifest.version == "1.0.6"


def test_service_check_uses_settings_default_channel_when_channel_is_empty(tmp_path: Path) -> None:
    """验证 SDK check 与 CLI 一样默认读取 settings.default_channel。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    _write_manifest(tmp_path, version="1.0.6")
    stable = tmp_path / "automation-manual-studio" / "stable" / "latest.json"
    beta = tmp_path / "automation-manual-studio" / "beta" / "latest.json"
    beta.parent.mkdir(parents=True)
    stable.replace(beta)
    payload = json.loads(beta.read_text(encoding="utf-8"))
    payload["channel"] = "beta"
    beta.write_text(json.dumps(payload), encoding="utf-8")
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path, default_channel="beta"))

    result = service.check(
        app_id="automation-manual-studio",
        current_version="1.0.5",
    )

    assert result.decision is UpdateDecision.OPTIONAL_UPDATE
    assert result.manifest.channel == "beta"


def test_service_prepare_copies_package(tmp_path: Path) -> None:
    """验证 prepare 从 NAS 复制升级包。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    _write_manifest(tmp_path, version="1.0.6")
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))
    check = service.check(app_id="automation-manual-studio", current_version="1.0.5")

    prepared = service.prepare(check.manifest, tmp_path / "downloads")

    assert prepared.verified is True
    assert prepared.package_path.read_bytes() == b"release"
