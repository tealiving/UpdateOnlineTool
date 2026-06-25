"""UpdateService 测试。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from update_online_tool.service import UpdateService
from update_online_tool.settings import UpdateToolSettings
from update_online_tool.versioning import UpdateDecision


def _write_manifest(
    root: Path,
    *,
    version: str,
    content: bytes = b"release",
    platform: str = "",
    channel: str = "stable",
    notes: str = "release",
    policy: dict[str, object] | None = None,
) -> None:
    """写入 NAS 模拟 manifest 和 package。

    :param root: NAS 根目录。
    :param version: 版本号。
    :param content: 包内容。
    :param platform: 平台标识。
    :param channel: 发布通道。
    :param policy: 额外 manifest 策略字段。
    :return: None
    """
    version_dir = (
        root / "automation-manual-studio" / channel / f"v{version}" / platform
        if platform
        else root / "automation-manual-studio" / channel / f"v{version}"
    )
    channel_dir = root / "automation-manual-studio" / channel / platform if platform else root / "automation-manual-studio" / channel
    package = version_dir / "package.zip"
    latest = channel_dir / "latest.json"
    version_latest = version_dir / "latest.json"
    package.parent.mkdir(parents=True, exist_ok=True)
    latest.parent.mkdir(parents=True, exist_ok=True)
    package.write_bytes(content)
    payload = {
        "schema_version": 2,
        "app_id": "automation-manual-studio",
        "channel": channel,
        "version": version,
        "mandatory": False,
        "min_supported_version": "1.0.0",
        "published_at": "2026-06-08T00:00:00+00:00",
        "notes": notes,
        "package": {
            "url": (
                f"automation-manual-studio/{channel}/v{version}/{platform}/package.zip"
                if platform
                else f"automation-manual-studio/{channel}/v{version}/package.zip"
            ),
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
    }
    if platform:
        payload["platform"] = platform
    if policy:
        payload.update(policy)
    latest.write_text(json.dumps(payload), encoding="utf-8")
    version_latest.write_text(json.dumps(payload), encoding="utf-8")


def _write_legacy_manifest(
    root: Path,
    *,
    version: str,
    content: bytes = b"release",
    channel: str = "stable",
) -> None:
    """写入旧版全局版本目录的 NAS 模拟数据。

    :param root: NAS 根目录。
    :param version: 版本号。
    :param content: 包内容。
    :param channel: manifest 所属通道。
    :return: None
    """
    version_dir = root / "automation-manual-studio" / f"v{version}"
    package = version_dir / "package.zip"
    version_latest = version_dir / "latest.json"
    package.parent.mkdir(parents=True, exist_ok=True)
    package.write_bytes(content)
    payload = {
        "schema_version": 2,
        "app_id": "automation-manual-studio",
        "channel": channel,
        "version": version,
        "mandatory": False,
        "min_supported_version": "1.0.0",
        "published_at": "2026-06-08T00:00:00+00:00",
        "notes": "legacy release",
        "package": {
            "url": f"automation-manual-studio/v{version}/package.zip",
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
    }
    version_latest.write_text(json.dumps(payload), encoding="utf-8")


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


def test_service_check_does_not_offer_hidden_latest(tmp_path: Path) -> None:
    """验证 hidden latest 不会被普通自动检查作为可升级版本。"""
    _write_manifest(tmp_path, version="1.0.6", policy={"hidden": True})
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))

    result = service.check(app_id="automation-manual-studio", current_version="1.0.5")

    assert result.decision is UpdateDecision.NOT_AVAILABLE
    assert result.manifest.hidden is True


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


def test_service_lists_remote_versions_sorted_by_version(tmp_path: Path) -> None:
    """验证 SDK 可列出历史版本并按版本倒序排列。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    _write_manifest(tmp_path, version="1.0.5", content=b"old", notes="old release")
    _write_manifest(tmp_path, version="1.0.7", content=b"new", notes="new release")
    _write_manifest(tmp_path, version="1.0.6", content=b"mid", notes="mid release")
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))

    versions = service.list_remote_versions(app_id="automation-manual-studio")

    assert [item.version for item in versions] == ["1.0.7", "1.0.6", "1.0.5"]
    assert [item.notes for item in versions] == ["new release", "mid release", "old release"]
    assert all(item.package_exists for item in versions)


def test_service_list_remote_versions_skips_other_channels(tmp_path: Path) -> None:
    """验证列出 stable 版本时不会被 beta 历史 manifest 阻断。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    _write_manifest(tmp_path, version="1.0.5", content=b"stable", channel="stable")
    _write_manifest(tmp_path, version="1.1.0", content=b"beta", channel="beta")
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))

    versions = service.list_remote_versions(app_id="automation-manual-studio", channel="stable")

    assert [item.version for item in versions] == ["1.0.5"]


def test_service_list_remote_versions_hides_hidden_by_default(tmp_path: Path) -> None:
    """验证远程版本列表默认不返回 hidden 版本。"""
    _write_manifest(tmp_path, version="1.0.5", content=b"visible")
    _write_manifest(tmp_path, version="1.0.6", content=b"hidden", policy={"hidden": True})
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))

    versions = service.list_remote_versions(app_id="automation-manual-studio")
    all_versions = service.list_remote_versions(app_id="automation-manual-studio", include_hidden=True)

    assert [item.version for item in versions] == ["1.0.5"]
    assert [item.version for item in all_versions] == ["1.0.6", "1.0.5"]
    assert all_versions[0].manifest.hidden is True


def test_service_list_remote_versions_supplements_versions_index(tmp_path: Path) -> None:
    """验证存在 versions.json 时优先索引且补充未索引历史版本。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    _write_manifest(tmp_path, version="1.0.4", content=b"not-indexed")
    _write_manifest(tmp_path, version="1.0.5", content=b"indexed")
    index_path = tmp_path / "automation-manual-studio" / "stable" / "versions.json"
    index_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "app_id": "automation-manual-studio",
                "channel": "stable",
                "platform": "",
                "versions": [
                    {
                        "version": "1.0.5",
                        "manifest_url": "automation-manual-studio/stable/v1.0.5/latest.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))

    versions = service.list_remote_versions(app_id="automation-manual-studio", channel="stable")

    assert [item.version for item in versions] == ["1.0.5", "1.0.4"]


def test_service_get_remote_manifest_supports_platform_version(tmp_path: Path) -> None:
    """验证 SDK 可读取平台隔离的指定版本 manifest。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    _write_manifest(tmp_path, version="1.0.6", content=b"macos", platform="macos")
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))

    manifest = service.get_remote_manifest(
        app_id="automation-manual-studio",
        version="1.0.6",
        platform="macos",
    )

    assert manifest.version == "1.0.6"
    assert manifest.platform == "macos"
    assert manifest.package.url == "automation-manual-studio/stable/v1.0.6/macos/package.zip"


def test_service_get_remote_manifest_keeps_legacy_version_path_fallback(tmp_path: Path) -> None:
    """验证 SDK 读取指定版本时兼容旧版全局版本目录。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    _write_legacy_manifest(tmp_path, version="1.0.6", content=b"legacy")
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))

    manifest = service.get_remote_manifest(app_id="automation-manual-studio", version="1.0.6")

    assert manifest.version == "1.0.6"
    assert manifest.package.url == "automation-manual-studio/v1.0.6/package.zip"


def test_service_prepares_specific_remote_version(tmp_path: Path) -> None:
    """验证 SDK 可准备指定历史版本包。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    _write_manifest(tmp_path, version="1.0.4", content=b"rollback")
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))
    manifest = service.get_remote_manifest(app_id="automation-manual-studio", version="1.0.4")

    prepared = service.prepare(manifest, tmp_path / "downloads")

    assert prepared.verified is True
    assert prepared.package_path.read_bytes() == b"rollback"
