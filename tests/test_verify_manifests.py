"""UpdateOnlineTool manifest 校验脚本测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from _scripts.verify_manifests import verify_manifest


def test_verify_manifest_accepts_v2_relative_package_url(tmp_path: Path) -> None:
    """验证 v2 manifest 使用相对 package.url 时可校验本地包。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    app_dir = tmp_path / "automation-manual-studio"
    version_dir = app_dir / "v1.0.5"
    channel_dir = app_dir / "stable"
    package_path = version_dir / "package.zip"
    channel_dir.mkdir(parents=True)
    version_dir.mkdir(parents=True)
    package_path.write_bytes(b"release")
    payload = {
        "schema_version": 2,
        "app_id": "automation-manual-studio",
        "channel": "stable",
        "version": "1.0.5",
        "mandatory": False,
        "min_supported_version": "1.0.0",
        "published_at": "2026-06-03T00:00:00+00:00",
        "notes": "release",
        "package": {
            "url": "automation-manual-studio/v1.0.5/package.zip",
            "size": package_path.stat().st_size,
            "sha256": hashlib.sha256(b"release").hexdigest(),
        },
    }
    manifest_path = channel_dir / "latest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    assert verify_manifest(manifest_path, root=tmp_path) == []


def test_verify_manifest_rejects_ifw_repository_contract(tmp_path: Path) -> None:
    """验证第一版 NAS-only 契约拒绝 IFW 仓库字段。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    app_dir = tmp_path / "automation-manual-studio"
    version_dir = app_dir / "v1.0.6"
    channel_dir = app_dir / "stable"
    package_path = version_dir / "package.zip"
    channel_dir.mkdir(parents=True)
    version_dir.mkdir(parents=True)
    package_path.write_bytes(b"release")
    payload = {
        "schema_version": 2,
        "app_id": "automation-manual-studio",
        "channel": "stable",
        "version": "1.0.6",
        "mandatory": False,
        "min_supported_version": "1.0.0",
        "published_at": "2026-06-03T00:00:00+00:00",
        "notes": "release",
        "installer_kind": "qt_ifw",
        "repository_url": "https://raw.githubusercontent.com/tealiving/UpdateOnlineTool/main/automation-manual-studio/stable/ifw-repository",
        "package": {
            "url": "automation-manual-studio/v1.0.6/package.zip",
            "size": package_path.stat().st_size,
            "sha256": hashlib.sha256(b"release").hexdigest(),
        },
    }
    manifest_path = channel_dir / "latest.json"
    manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    errors = verify_manifest(manifest_path, root=tmp_path)

    assert errors
    assert "unsupported keys" in errors[0]
