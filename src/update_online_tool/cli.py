"""UpdateOnlineTool 命令行入口。"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.manifest import UpdateManifest
from update_online_tool.nas import NasReleaseSource
from update_online_tool.pyinstaller_assembly import assemble_pyinstaller_release, default_pyinstaller_assembly_config
from update_online_tool.service import UpdateService
from update_online_tool.settings import UpdateToolSettings, user_settings_path


def main(argv: list[str] | None = None) -> int:
    """运行 CLI。

    :param argv: 命令行参数；为空时读取 sys.argv。
    :return: 进程退出码。
    """
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "init":
            return _init(args)
        if args.command == "publish":
            return _publish(args)
        if args.command == "verify":
            return _verify(args)
        if args.command == "check":
            return _check(args)
        if args.command == "assemble-pyinstaller":
            return _assemble_pyinstaller(args)
    except UpdateError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    parser.print_help()
    return 2


def _build_parser() -> argparse.ArgumentParser:
    """构建参数解析器。

    :return: 参数解析器。
    """
    parser = argparse.ArgumentParser(prog="uot", description="NAS online updater CLI.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Generate a project update-endpoint.json.")
    init.add_argument("--app", default="", help="Application id. Defaults to current directory name.")
    init.add_argument("--channel", default="stable", help="Release channel.")
    init.add_argument("--output", default="update-endpoint.json", type=Path, help="Output endpoint JSON path.")
    init.add_argument("--source-name", default="local-nas", help="Manifest source name.")
    init.add_argument("--installer-mode", default="custom_updater", help="Installer mode.")
    init.add_argument("--package-url-prefix", default="uot-nas://nas", help="Package URL prefix.")
    init.add_argument("--auth-provider", default="update_online_tool", help="Auth provider.")
    init.add_argument("--priority", default=10, type=int, help="Manifest source priority.")
    init.add_argument("--nas-root", default=None, type=Path, help="Optional NAS root. Writes project settings when set.")
    init.add_argument("--settings-output", default=None, type=Path, help="Optional settings output path.")
    init.add_argument("--user-settings", action="store_true", help="Write settings to the OS user config directory.")
    init.add_argument("--updater-name", default="Updater.exe", help="Standalone updater executable name.")
    init.add_argument("--skip-nas-check", action="store_true", help="Skip NAS read/write validation during init.")
    init.add_argument("--force", action="store_true", help="Overwrite existing output file.")

    publish = subparsers.add_parser("publish", help="Publish a zip package to the NAS release root.")
    _add_settings_arg(publish)
    publish.add_argument("--app", required=True, help="Application id.")
    publish.add_argument("--version", required=True, help="Release version.")
    publish.add_argument("--package", required=True, type=Path, help="Release zip path.")
    publish.add_argument("--channel", default="", help="Release channel. Defaults to settings.")
    publish.add_argument("--platform", default="", help="Optional target platform: windows, macos, or linux.")
    publish.add_argument("--notes", default="", help="Release notes.")
    publish.add_argument("--min-supported-version", default="", help="Minimum supported current version.")
    publish.add_argument("--mandatory", action="store_true", help="Mark this update as mandatory.")
    publish.add_argument("--published-at", default="", help="ISO timestamp. Defaults to current UTC time.")

    verify = subparsers.add_parser("verify", help="Verify one app manifest and package.")
    _add_settings_arg(verify)
    verify.add_argument("--app", required=True, help="Application id.")
    verify.add_argument("--channel", default="", help="Release channel. Defaults to settings.")
    verify.add_argument("--platform", default="", help="Optional target platform: windows, macos, or linux.")

    check = subparsers.add_parser("check", help="Check whether an app version has an update.")
    _add_settings_arg(check)
    check.add_argument("--app", required=True, help="Application id.")
    check.add_argument("--current-version", required=True, help="Current app version.")
    check.add_argument("--channel", default="", help="Release channel. Defaults to settings.")
    check.add_argument("--platform", default="", help="Optional target platform: windows, macos, or linux.")
    check.add_argument("--skipped-version", default="", help="Skipped version.")

    assemble = subparsers.add_parser("assemble-pyinstaller", help="Assemble PyInstaller GUI and launcher bundles.")
    assemble.add_argument("--version", required=True, help="Release version.")
    assemble.add_argument("--dist-dir", default="dist", type=Path, help="PyInstaller dist directory.")
    assemble.add_argument("--app", default="", help="Application id. Defaults to product name.")
    assemble.add_argument("--product-name", required=True, help="Product name used for default bundle names.")
    assemble.add_argument("--platform", default="windows", choices=["windows", "macos", "linux"], help="Target platform.")
    assemble.add_argument("--entry-name", default="", help="Final stable entry name. Defaults to product name plus platform suffix.")
    assemble.add_argument("--release-entry-name", default="", help="Source GUI entry name inside the release bundle.")
    assemble.add_argument("--launcher-entry-name", default="", help="Source launcher entry name inside the launcher bundle.")
    assemble.add_argument("--settings", default=None, type=Path, help="Project settings.json to bundle.")
    assemble.add_argument("--force", action="store_true", help="Overwrite existing output directories.")
    return parser


def _add_settings_arg(parser: argparse.ArgumentParser) -> None:
    """添加通用 settings 参数。

    :param parser: 子命令解析器。
    :return: None
    """
    parser.add_argument("--settings", default="", type=Path, help="settings.json path.")


def _init(args: argparse.Namespace) -> int:
    """生成接入方项目 update-endpoint.json。

    :param args: 命令参数。
    :return: 进程退出码。
    """
    app_id = _resolve_init_app_id(args)
    output_path = Path(args.output)
    if output_path.exists() and not args.force:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"output already exists: {output_path}")
    settings_path = _resolve_init_settings_output(args)
    if settings_path is not None and settings_path.exists() and not args.force:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"settings output already exists: {settings_path}")
    for log_line in _check_init_nas_root(args):
        print(log_line)
    channel = str(args.channel or "stable").strip()
    payload = {
        "channel": channel,
        "installer_mode": str(args.installer_mode or "custom_updater").strip(),
        "manifest_sources": [
            {
                "name": str(args.source_name or "local-nas").strip(),
                "manifest_url": f"uot-nas://{app_id}/{channel}",
                "package_url_prefix": str(args.package_url_prefix or "uot-nas://nas").strip(),
                "auth_provider": str(args.auth_provider or "update_online_tool").strip(),
                "priority": int(args.priority),
            }
        ],
    }
    _write_json(output_path, payload)
    if settings_path is not None:
        _write_json(settings_path, _build_init_settings_payload(args))
    print(f"Generated {output_path}")
    if settings_path is not None:
        print(f"Generated {settings_path}")
    return 0


def _resolve_init_settings_output(args: argparse.Namespace) -> Path | None:
    """解析 init 命令 settings 输出路径。

    :param args: 命令参数。
    :return: settings 输出路径；未请求生成时返回 None。
    """
    if args.settings_output is not None:
        return Path(args.settings_output)
    if args.nas_root is not None:
        if bool(args.user_settings):
            return user_settings_path(_resolve_init_app_id(args))
        return Path(args.output).parent / "config" / "settings.json"
    return None


def _check_init_nas_root(args: argparse.Namespace) -> list[str]:
    """检查 init 命令传入的 NAS 根目录。

    :param args: 命令参数。
    :return: 检查日志。
    """
    if args.nas_root is None:
        return []
    if bool(args.skip_nas_check):
        return [f"NAS check skipped: root={Path(args.nas_root)}"]
    return _probe_nas_root(Path(args.nas_root))


def _probe_nas_root(nas_root: Path) -> list[str]:
    """探测 NAS 根目录是否可读写。

    :param nas_root: NAS 根目录。
    :return: 检查日志。
    """
    root = Path(nas_root)
    if not root.is_dir():
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"NAS root is not available: {root}")
    try:
        next(root.iterdir(), None)
    except OSError as exc:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"NAS root is not readable: {root}") from exc
    probe_path = root / f".uot-write-test-{os.getpid()}-{uuid4().hex}.tmp"
    probe_text = "update-online-tool nas check\n"
    try:
        probe_path.write_text(probe_text, encoding="utf-8")
        if probe_path.read_text(encoding="utf-8") != probe_text:
            raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"NAS root write probe mismatch: {root}")
        probe_path.unlink()
    except UpdateError:
        raise
    except OSError as exc:
        _cleanup_probe_file(probe_path)
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"NAS root is not writable: {root}") from exc
    return [
        f"NAS check ok: root={root}",
        "NAS check ok: readable",
        "NAS check ok: writable",
    ]


def _cleanup_probe_file(path: Path) -> None:
    """清理 NAS 探测临时文件。

    :param path: 临时文件路径。
    :return: None
    """
    try:
        if path.exists():
            path.unlink()
    except OSError:
        return


def _resolve_init_app_id(args: argparse.Namespace) -> str:
    """解析 init 命令应用标识。

    :param args: 命令参数。
    :return: 应用标识。
    """
    app_id = str(args.app or "").strip()
    if not app_id:
        app_id = Path.cwd().name.strip()
    if not app_id:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "app id is required outside a named project directory")
    return app_id


def _build_init_settings_payload(args: argparse.Namespace) -> dict[str, object]:
    """构建 init 命令 settings payload。

    :param args: 命令参数。
    :return: settings JSON 字典。
    """
    if args.nas_root is None:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "--nas-root is required when writing settings")
    return {
        "nas": {
            "root": str(Path(args.nas_root)),
        },
        "publish": {
            "default_channel": str(args.channel or "stable").strip(),
            "default_minimum_version": "1.0.0",
            "package_filename": "package.zip",
        },
        "updater": {
            "executable_name": str(args.updater_name or "Updater.exe").strip(),
        },
    }


def _publish(args: argparse.Namespace) -> int:
    """发布包到 NAS。

    :param args: 命令参数。
    :return: 进程退出码。
    """
    settings = _load_settings_arg(args)
    source = NasReleaseSource(settings.nas_root)
    channel = args.channel or settings.default_channel
    platform = _normalize_optional_platform(args.platform)
    min_supported_version = args.min_supported_version or settings.default_minimum_version
    source_package_path = Path(args.package)
    if not source_package_path.is_file():
        raise UpdateError(UpdateErrorCode.PACKAGE_NOT_FOUND, f"package not found: {source_package_path}")
    target_package_path = source.package_path(args.app, args.version, settings.package_filename, platform)
    target_package_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_package_path, target_package_path)
    package_size = target_package_path.stat().st_size
    package_sha256 = _sha256_of(target_package_path)
    relative_package_url = _package_url(args.app, args.version, settings.package_filename, platform)
    published_at = args.published_at.strip() or datetime.now(timezone.utc).isoformat()
    payload: dict[str, object] = {
        "schema_version": 2,
        "app_id": args.app,
        "channel": channel,
        "version": args.version,
        "mandatory": bool(args.mandatory),
        "min_supported_version": min_supported_version,
        "published_at": published_at,
        "notes": args.notes or f"v{args.version} release",
        "package": {
            "url": relative_package_url,
            "size": package_size,
            "sha256": package_sha256,
        },
    }
    if platform:
        payload["platform"] = platform
    manifest = UpdateManifest.from_payload(payload)
    _write_json(source.version_dir(args.app, args.version, platform) / "latest.json", manifest.to_payload())
    _write_json(source.manifest_path(args.app, channel, platform), manifest.to_payload())
    print(f"Published {args.app} v{args.version} to {settings.nas_root}")
    return 0


def _verify(args: argparse.Namespace) -> int:
    """校验 NAS 发布内容。

    :param args: 命令参数。
    :return: 进程退出码。
    """
    settings = _load_settings_arg(args)
    source = NasReleaseSource(settings.nas_root)
    source.ensure_available()
    channel = args.channel or settings.default_channel
    platform = _normalize_optional_platform(args.platform)
    manifest_path = source.manifest_path(args.app, channel, platform)
    manifest = _load_manifest_file(manifest_path)
    if platform and manifest.platform and manifest.platform != platform:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"manifest platform mismatch: {manifest.platform}")
    package_path = source.resolve_package_path(manifest.package.url)
    if not package_path.is_file():
        raise UpdateError(UpdateErrorCode.PACKAGE_NOT_FOUND, f"package not found: {package_path}")
    actual_size = package_path.stat().st_size
    if actual_size != manifest.package.size:
        raise UpdateError(
            UpdateErrorCode.PACKAGE_SIZE_MISMATCH,
            f"package.size {manifest.package.size} != actual {actual_size}",
        )
    actual_sha256 = _sha256_of(package_path)
    if actual_sha256 != manifest.package.sha256.lower():
        raise UpdateError(
            UpdateErrorCode.PACKAGE_HASH_MISMATCH,
            f"package.sha256 {manifest.package.sha256.lower()} != actual {actual_sha256}",
        )
    print(f"Verified {manifest_path}")
    return 0


def _check(args: argparse.Namespace) -> int:
    """检查是否存在升级。

    :param args: 命令参数。
    :return: 进程退出码。
    """
    settings = _load_settings_arg(args)
    channel = args.channel or settings.default_channel
    platform = _normalize_optional_platform(args.platform)
    result = UpdateService(settings).check(
        app_id=args.app,
        current_version=args.current_version,
        channel=channel,
        platform=platform,
        skipped_version=args.skipped_version or None,
    )
    print(f"{result.decision.value}: {result.manifest.version}")
    return 0


def _assemble_pyinstaller(args: argparse.Namespace) -> int:
    """装配 PyInstaller 发布目录。

    :param args: 命令参数。
    :return: 进程退出码。
    """
    config = default_pyinstaller_assembly_config(
        version=args.version,
        app_id=args.app,
        dist_dir=Path(args.dist_dir),
        product_name=args.product_name,
        platform=args.platform,
        entry_name=args.entry_name,
        release_entry_name=args.release_entry_name,
        launcher_entry_name=args.launcher_entry_name,
        settings_path=args.settings,
        force=bool(args.force),
    )
    result = assemble_pyinstaller_release(config)
    print(f"Assembled install root: {result.install_root}")
    print(f"Assembled update root: {result.update_root}")
    print(f"Launcher executable: {result.launcher_executable}")
    print(f"Release executable: {result.release_executable}")
    return 0


def _load_settings_arg(args: argparse.Namespace) -> UpdateToolSettings:
    """按命令参数读取设置。

    :param args: 命令参数。
    :return: 设置模型。
    """
    return UpdateToolSettings.load(args.settings or None)


def _load_manifest_file(path: Path) -> UpdateManifest:
    """读取 manifest 文件。

    :param path: manifest 路径。
    :return: manifest 模型。
    """
    if not path.is_file():
        raise UpdateError(UpdateErrorCode.MANIFEST_NOT_FOUND, f"manifest not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "manifest must be a JSON object")
    return UpdateManifest.from_payload(payload)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    """写入稳定格式 JSON。

    :param path: 目标路径。
    :param payload: JSON 字典。
    :return: None
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _package_url(app_id: str, version: str, package_filename: str, platform: str) -> str:
    """生成 manifest package.url。

    :param app_id: 应用标识。
    :param version: 版本号。
    :param package_filename: 包文件名。
    :param platform: 可选平台。
    :return: NAS 根目录下的相对包路径。
    """
    if platform:
        return f"{app_id}/v{version}/{platform}/{package_filename}"
    return f"{app_id}/v{version}/{package_filename}"


def _normalize_optional_platform(platform: str) -> str:
    """规范化可选平台参数。

    :param platform: 原始平台名。
    :return: windows、macos、linux 或空字符串。
    """
    normalized = str(platform or "").strip().lower()
    if not normalized:
        return ""
    aliases = {
        "win": "windows",
        "win32": "windows",
        "darwin": "macos",
        "mac": "macos",
        "osx": "macos",
    }
    normalized = aliases.get(normalized, normalized)
    if normalized not in {"windows", "macos", "linux"}:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"unsupported platform: {platform}")
    return normalized


def _sha256_of(path: Path) -> str:
    """计算文件 SHA-256。

    :param path: 文件路径。
    :return: 十六进制摘要。
    """
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
