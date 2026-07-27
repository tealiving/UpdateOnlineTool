"""UpdateService 测试。"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

import update_online_tool.launcher as launcher
from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.launcher import LaunchResult
from update_online_tool.service import UpdateService
from update_online_tool.settings import UpdateToolSettings
from update_online_tool.versioning import UpdateDecision


def _write_test_package(path: Path, content: bytes) -> bytes:
    """写入最小合法 release ZIP，并返回最终包字节。"""
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("payload.bin", content)
    return path.read_bytes()


def _read_test_package(path: Path) -> bytes:
    """读取测试 ZIP 中的业务载荷。"""
    with zipfile.ZipFile(path) as archive:
        return archive.read("payload.bin")


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
    channel_dir = (
        root / "automation-manual-studio" / channel / platform
        if platform
        else root / "automation-manual-studio" / channel
    )
    package = version_dir / "package.zip"
    latest = channel_dir / "latest.json"
    version_latest = version_dir / "latest.json"
    package.parent.mkdir(parents=True, exist_ok=True)
    latest.parent.mkdir(parents=True, exist_ok=True)
    package_bytes = _write_test_package(package, content)
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
            "size": len(package_bytes),
            "sha256": hashlib.sha256(package_bytes).hexdigest(),
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


def test_service_uses_first_available_nas_root(tmp_path: Path) -> None:
    """验证读取操作按配置顺序选择第一个可访问 NAS。"""
    missing_root = tmp_path / "missing"
    available_root = tmp_path / "available"
    available_root.mkdir()
    _write_manifest(available_root, version="1.0.6")
    service = UpdateService(
        UpdateToolSettings(
            nas_root=missing_root,
            nas_roots=(missing_root, available_root),
        )
    )

    result = service.check(app_id="automation-manual-studio", current_version="1.0.5")

    assert service.source.root == available_root
    assert result.manifest.version == "1.0.6"


def test_service_check_offers_allow_downgrade_latest(tmp_path: Path) -> None:
    """验证 latest 允许降级时高版本客户端可看到回退版本。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    _write_manifest(tmp_path, version="1.0.9", policy={"allow_downgrade": True})
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))

    result = service.check(
        app_id="automation-manual-studio",
        current_version="1.1.0",
        channel="stable",
    )

    assert result.decision is UpdateDecision.OPTIONAL_UPDATE
    assert result.manifest.version == "1.0.9"


def test_service_check_uses_settings_default_channel_when_channel_is_empty(
    tmp_path: Path,
) -> None:
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
    service = UpdateService(
        UpdateToolSettings(nas_root=tmp_path, default_channel="beta")
    )

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
    assert _read_test_package(prepared.package_path) == b"release"
    assert prepared.package_path == (
        tmp_path
        / "downloads"
        / "automation-manual-studio"
        / "stable"
        / "any"
        / "1.0.6"
        / "package.zip"
    )


def test_service_prepare_rejects_invalid_layout_without_replacing_cached_package(
    tmp_path: Path,
) -> None:
    """prepare 应在原子提升前运行 package plan，并保留旧缓存。"""
    _write_manifest(tmp_path, version="1.0.6")
    package = (
        tmp_path / "automation-manual-studio" / "stable" / "v1.0.6" / "package.zip"
    )
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("Config.json", "first")
        archive.writestr("config.json", "second")
    package_bytes = package.read_bytes()
    for manifest_path in (
        tmp_path / "automation-manual-studio" / "stable" / "latest.json",
        package.parent / "latest.json",
    ):
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        payload["package"]["size"] = len(package_bytes)
        payload["package"]["sha256"] = hashlib.sha256(package_bytes).hexdigest()
        manifest_path.write_text(json.dumps(payload), encoding="utf-8")

    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))
    manifest = service.get_remote_manifest(
        app_id="automation-manual-studio", version="1.0.6"
    )
    cached = (
        tmp_path
        / "downloads"
        / "automation-manual-studio"
        / "stable"
        / "any"
        / "1.0.6"
        / "package.zip"
    )
    cached.parent.mkdir(parents=True)
    cached.write_bytes(b"previous-cache")

    with pytest.raises(UpdateError) as error:
        service.prepare(manifest, tmp_path / "downloads")

    assert error.value.code is UpdateErrorCode.PACKAGE_LAYOUT_INVALID
    assert cached.read_bytes() == b"previous-cache"
    assert not list(cached.parent.glob(".package.zip.*.tmp"))


def test_service_prepare_keeps_versions_in_separate_paths(tmp_path: Path) -> None:
    """验证准备多个版本时不会互相覆盖 package.zip。"""
    _write_manifest(tmp_path, version="1.0.4", content=b"old")
    _write_manifest(tmp_path, version="1.0.6", content=b"new")
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))

    old_manifest = service.get_remote_manifest(
        app_id="automation-manual-studio", version="1.0.4"
    )
    new_manifest = service.get_remote_manifest(
        app_id="automation-manual-studio", version="1.0.6"
    )
    old_prepared = service.prepare(old_manifest, tmp_path / "downloads")
    new_prepared = service.prepare(new_manifest, tmp_path / "downloads")

    assert old_prepared.package_path != new_prepared.package_path
    assert _read_test_package(old_prepared.package_path) == b"old"
    assert _read_test_package(new_prepared.package_path) == b"new"


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
    assert [item.notes for item in versions] == [
        "new release",
        "mid release",
        "old release",
    ]
    assert all(item.package_exists for item in versions)


def test_service_list_remote_versions_skips_other_channels(tmp_path: Path) -> None:
    """验证列出 stable 版本时不会被 beta 历史 manifest 阻断。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    _write_manifest(tmp_path, version="1.0.5", content=b"stable", channel="stable")
    _write_manifest(tmp_path, version="1.1.0", content=b"beta", channel="beta")
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))

    versions = service.list_remote_versions(
        app_id="automation-manual-studio", channel="stable"
    )

    assert [item.version for item in versions] == ["1.0.5"]


def test_service_list_remote_versions_hides_hidden_by_default(tmp_path: Path) -> None:
    """验证远程版本列表默认不返回 hidden 版本。"""
    _write_manifest(tmp_path, version="1.0.5", content=b"visible")
    _write_manifest(
        tmp_path, version="1.0.6", content=b"hidden", policy={"hidden": True}
    )
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))

    versions = service.list_remote_versions(app_id="automation-manual-studio")
    all_versions = service.list_remote_versions(
        app_id="automation-manual-studio", include_hidden=True
    )

    assert [item.version for item in versions] == ["1.0.5"]
    assert [item.version for item in all_versions] == ["1.0.6", "1.0.5"]
    assert all_versions[0].manifest.hidden is True


def test_service_list_remote_versions_supplements_versions_index(
    tmp_path: Path,
) -> None:
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

    versions = service.list_remote_versions(
        app_id="automation-manual-studio", channel="stable"
    )

    assert [item.version for item in versions] == ["1.0.5", "1.0.4"]


def test_service_list_remote_versions_skips_inaccessible_index_item(
    tmp_path: Path, monkeypatch
) -> None:
    """验证 versions.json 中单个不可访问 manifest 不会阻断版本列表。"""
    _write_manifest(tmp_path, version="1.0.4", content=b"valid")
    _write_manifest(tmp_path, version="1.0.5", content=b"inaccessible")
    broken_manifest = (
        tmp_path / "automation-manual-studio" / "stable" / "v1.0.5" / "latest.json"
    )
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
                    },
                    {
                        "version": "1.0.4",
                        "manifest_url": "automation-manual-studio/stable/v1.0.4/latest.json",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )
    original_is_file = Path.is_file

    def flaky_is_file(path: Path) -> bool:
        if path == broken_manifest:
            raise OSError("simulated smb stat failure")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", flaky_is_file)
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))

    versions = service.list_remote_versions(
        app_id="automation-manual-studio", channel="stable"
    )

    assert [item.version for item in versions] == ["1.0.4"]


def test_service_list_remote_versions_skips_unreadable_scanned_manifest(
    tmp_path: Path, monkeypatch
) -> None:
    """验证扫描路径中单个 manifest 读取失败时跳过该版本。"""
    _write_manifest(tmp_path, version="1.0.4", content=b"valid")
    _write_manifest(tmp_path, version="1.0.5", content=b"unreadable")
    broken_manifest = (
        tmp_path / "automation-manual-studio" / "stable" / "v1.0.5" / "latest.json"
    )
    original_read_text = Path.read_text

    def flaky_read_text(path: Path, *args, **kwargs) -> str:
        if path == broken_manifest:
            raise OSError("simulated smb read failure")
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", flaky_read_text)
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))

    versions = service.list_remote_versions(
        app_id="automation-manual-studio", channel="stable"
    )

    assert [item.version for item in versions] == ["1.0.4"]


def test_service_list_remote_versions_marks_inaccessible_package_missing(
    tmp_path: Path, monkeypatch
) -> None:
    """验证 package 存在性探测失败不会阻断版本列表。"""
    _write_manifest(tmp_path, version="1.0.4", content=b"valid")
    package_path = (
        tmp_path / "automation-manual-studio" / "stable" / "v1.0.4" / "package.zip"
    )
    original_is_file = Path.is_file

    def flaky_is_file(path: Path) -> bool:
        if path == package_path:
            raise OSError("simulated smb package stat failure")
        return original_is_file(path)

    monkeypatch.setattr(Path, "is_file", flaky_is_file)
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))

    versions = service.list_remote_versions(
        app_id="automation-manual-studio", channel="stable"
    )

    assert [item.version for item in versions] == ["1.0.4"]
    assert versions[0].package_exists is False


def test_service_list_remote_versions_deduplicates_indexed_version_over_scanned_path(
    tmp_path: Path,
) -> None:
    """验证 versions.json 中的同版本路径优先于扫描到的旧路径。"""
    _write_manifest(tmp_path, version="1.0.8", content=b"direct", notes="direct")
    indexed_package = (
        tmp_path
        / "automation-manual-studio"
        / "stable"
        / "custom"
        / "1.0.8"
        / "package.zip"
    )
    indexed_manifest = indexed_package.parent / "latest.json"
    indexed_package.parent.mkdir(parents=True)
    indexed_package.write_bytes(b"indexed")
    indexed_manifest.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "app_id": "automation-manual-studio",
                "channel": "stable",
                "version": "1.0.8",
                "mandatory": False,
                "min_supported_version": "1.0.0",
                "published_at": "2026-06-08T00:00:00+00:00",
                "notes": "indexed",
                "package": {
                    "url": "automation-manual-studio/stable/custom/1.0.8/package.zip",
                    "size": len(b"indexed"),
                    "sha256": hashlib.sha256(b"indexed").hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )
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
                        "version": "1.0.8",
                        "manifest_url": "automation-manual-studio/stable/custom/1.0.8/latest.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))

    versions = service.list_remote_versions(
        app_id="automation-manual-studio", channel="stable"
    )

    assert [(item.version, item.notes) for item in versions] == [("1.0.8", "indexed")]
    assert versions[0].manifest_path == indexed_manifest


def test_service_list_remote_versions_hidden_index_blocks_visible_duplicate(
    tmp_path: Path,
) -> None:
    """验证 hidden 索引版本不会被同版本扫描路径重新补回普通列表。"""
    _write_manifest(tmp_path, version="1.0.8", content=b"direct", notes="direct")
    indexed_package = (
        tmp_path
        / "automation-manual-studio"
        / "stable"
        / "custom"
        / "1.0.8"
        / "package.zip"
    )
    indexed_manifest = indexed_package.parent / "latest.json"
    indexed_package.parent.mkdir(parents=True)
    indexed_package.write_bytes(b"indexed")
    indexed_manifest.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "app_id": "automation-manual-studio",
                "channel": "stable",
                "version": "1.0.8",
                "mandatory": False,
                "min_supported_version": "1.0.0",
                "published_at": "2026-06-08T00:00:00+00:00",
                "notes": "indexed hidden",
                "hidden": True,
                "package": {
                    "url": "automation-manual-studio/stable/custom/1.0.8/package.zip",
                    "size": len(b"indexed"),
                    "sha256": hashlib.sha256(b"indexed").hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )
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
                        "version": "1.0.8",
                        "manifest_url": "automation-manual-studio/stable/custom/1.0.8/latest.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))

    visible_versions = service.list_remote_versions(
        app_id="automation-manual-studio", channel="stable"
    )
    all_versions = service.list_remote_versions(
        app_id="automation-manual-studio",
        channel="stable",
        include_hidden=True,
    )

    assert visible_versions == []
    assert [(item.version, item.notes) for item in all_versions] == [
        ("1.0.8", "indexed hidden")
    ]


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
    assert (
        manifest.package.url
        == "automation-manual-studio/stable/v1.0.6/macos/package.zip"
    )


def test_service_get_remote_manifest_keeps_legacy_version_path_fallback(
    tmp_path: Path,
) -> None:
    """验证 SDK 读取指定版本时兼容旧版全局版本目录。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    _write_legacy_manifest(tmp_path, version="1.0.6", content=b"legacy")
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))

    manifest = service.get_remote_manifest(
        app_id="automation-manual-studio", version="1.0.6"
    )

    assert manifest.version == "1.0.6"
    assert manifest.package.url == "automation-manual-studio/v1.0.6/package.zip"


def test_service_get_remote_manifest_uses_versions_index_when_direct_path_missing(
    tmp_path: Path,
) -> None:
    """验证指定版本可复用 versions.json 中记录的真实 manifest 路径。"""
    package = (
        tmp_path
        / "automation-manual-studio"
        / "stable"
        / "custom"
        / "1.0.8"
        / "package.zip"
    )
    manifest_path = package.parent / "latest.json"
    package.parent.mkdir(parents=True)
    package.write_bytes(b"indexed")
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "app_id": "automation-manual-studio",
                "channel": "stable",
                "version": "1.0.8",
                "mandatory": False,
                "min_supported_version": "1.0.0",
                "published_at": "2026-06-08T00:00:00+00:00",
                "notes": "indexed",
                "package": {
                    "url": "automation-manual-studio/stable/custom/1.0.8/package.zip",
                    "size": len(b"indexed"),
                    "sha256": hashlib.sha256(b"indexed").hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )
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
                        "version": "1.0.8",
                        "manifest_url": "automation-manual-studio/stable/custom/1.0.8/latest.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))

    manifest, actual_manifest_path = service.get_remote_manifest_with_path(
        app_id="automation-manual-studio",
        version="1.0.8",
        channel="stable",
    )

    assert manifest.version == "1.0.8"
    assert (
        manifest.package.url
        == "automation-manual-studio/stable/custom/1.0.8/package.zip"
    )
    assert actual_manifest_path == manifest_path


def test_service_get_remote_manifest_prefers_versions_index_over_direct_path(
    tmp_path: Path,
) -> None:
    """验证历史版本选择以 versions.json.manifest_url 为权威路径。"""
    _write_manifest(tmp_path, version="1.0.8", content=b"direct")
    indexed_package = (
        tmp_path
        / "automation-manual-studio"
        / "stable"
        / "custom"
        / "1.0.8"
        / "package.zip"
    )
    indexed_manifest_path = indexed_package.parent / "latest.json"
    indexed_package.parent.mkdir(parents=True)
    indexed_package.write_bytes(b"indexed")
    indexed_manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "app_id": "automation-manual-studio",
                "channel": "stable",
                "version": "1.0.8",
                "mandatory": False,
                "min_supported_version": "1.0.0",
                "published_at": "2026-06-08T00:00:00+00:00",
                "notes": "indexed",
                "package": {
                    "url": "automation-manual-studio/stable/custom/1.0.8/package.zip",
                    "size": len(b"indexed"),
                    "sha256": hashlib.sha256(b"indexed").hexdigest(),
                },
            }
        ),
        encoding="utf-8",
    )
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
                        "version": "1.0.8",
                        "manifest_url": "automation-manual-studio/stable/custom/1.0.8/latest.json",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))

    manifest, actual_manifest_path = service.get_remote_manifest_with_path(
        app_id="automation-manual-studio",
        version="1.0.8",
        channel="stable",
    )

    assert manifest.notes == "indexed"
    assert (
        manifest.package.url
        == "automation-manual-studio/stable/custom/1.0.8/package.zip"
    )
    assert actual_manifest_path == indexed_manifest_path


def test_service_prepares_specific_remote_version(tmp_path: Path) -> None:
    """验证 SDK 可准备指定历史版本包。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    _write_manifest(tmp_path, version="1.0.4", content=b"rollback")
    service = UpdateService(UpdateToolSettings(nas_root=tmp_path))
    manifest = service.get_remote_manifest(
        app_id="automation-manual-studio", version="1.0.4"
    )

    prepared = service.prepare(manifest, tmp_path / "downloads")

    assert prepared.verified is True
    assert _read_test_package(prepared.package_path) == b"rollback"


def test_service_launch_uses_standard_updater_sidecar_and_forwards_runtime_options(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """验证低层 launch 只解析 UOT 标准 updater sidecar 并传递 runtime 参数。"""
    install_root = tmp_path / "install"
    updater_path = install_root / "updater" / "MyToolUpdater.exe"
    updater_path.parent.mkdir(parents=True)
    updater_path.write_text("updater", encoding="utf-8")
    package_path = tmp_path / "package.zip"
    package_path.write_bytes(b"release")
    _write_manifest(tmp_path, version="1.0.8", content=b"release")
    manifest = UpdateService(UpdateToolSettings(nas_root=tmp_path)).get_remote_manifest(
        app_id="automation-manual-studio",
        version="1.0.8",
        channel="stable",
    )
    captured: dict[str, object] = {}

    class FakeLauncher:
        """记录 launch 调用。"""

        def __init__(self, updater_executable: Path) -> None:
            captured["updater_executable"] = updater_executable

        def launch(
            self, *, pending_payload: dict[str, object], pending_manifest_path: Path
        ) -> LaunchResult:
            captured["pending_payload"] = pending_payload
            captured["pending_manifest_path"] = pending_manifest_path
            return LaunchResult(
                started=True,
                updater_pid=4242,
                pending_manifest_path=pending_manifest_path,
            )

    monkeypatch.setattr(launcher, "StandaloneUpdaterLauncher", FakeLauncher)
    service = UpdateService(
        UpdateToolSettings(
            nas_root=tmp_path,
            updater_executable_name="MyToolUpdater.exe",
        )
    )
    signature_key = tmp_path / "signing.pub"

    result = service.launch(
        package_path=package_path,
        manifest=manifest,
        install_root=install_root,
        old_pid=123,
        restart_executable="MyTool.exe",
        force=True,
        restart=False,
        wait_timeout=12.5,
        signature_key=signature_key,
    )

    pending_payload = captured["pending_payload"]
    assert isinstance(pending_payload, dict)
    assert result.updater_pid == 4242
    assert captured["updater_executable"] == updater_path
    assert captured["pending_manifest_path"] == install_root / "pending-update.json"
    assert pending_payload["force"] is True
    assert pending_payload["restart"] is False
    assert pending_payload["wait_timeout"] == 12.5
    assert pending_payload["signature_key"] == str(signature_key)


def test_service_launch_resolves_nested_updater_without_windows_suffix(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """验证配置为 .exe 时可解析 onedir 中无后缀的 updater 入口。"""
    install_root = tmp_path / "install"
    nested_updater = install_root / "updater" / "MyToolUpdater" / "MyToolUpdater"
    nested_updater.parent.mkdir(parents=True)
    nested_updater.write_text("updater", encoding="utf-8")
    package_path = tmp_path / "package.zip"
    package_path.write_bytes(b"release")
    _write_manifest(tmp_path, version="1.0.8", content=b"release")
    manifest = UpdateService(UpdateToolSettings(nas_root=tmp_path)).get_remote_manifest(
        app_id="automation-manual-studio",
        version="1.0.8",
        channel="stable",
    )
    captured: dict[str, Path] = {}

    class FakeLauncher:
        """记录 launch 调用。"""

        def __init__(self, updater_executable: Path) -> None:
            captured["updater_executable"] = updater_executable

        def launch(
            self, *, pending_payload: dict[str, object], pending_manifest_path: Path
        ) -> LaunchResult:
            return LaunchResult(
                started=True,
                updater_pid=4242,
                pending_manifest_path=pending_manifest_path,
            )

    monkeypatch.setattr(launcher, "StandaloneUpdaterLauncher", FakeLauncher)

    UpdateService(
        UpdateToolSettings(
            nas_root=tmp_path,
            updater_executable_name="MyToolUpdater.exe",
        )
    ).launch(
        package_path=package_path,
        manifest=manifest,
        install_root=install_root,
        old_pid=123,
        restart_executable="MyTool.exe",
    )

    assert captured["updater_executable"] == nested_updater
