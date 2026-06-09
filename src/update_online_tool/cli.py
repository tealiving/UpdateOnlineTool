"""UpdateOnlineTool 命令行入口。"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.manifest import UpdateManifest
from update_online_tool.nas import NasReleaseSource
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
    init.add_argument("--nas-root", default=None, type=Path, help="Optional NAS root. Writes user settings when set.")
    init.add_argument("--settings-output", default=None, type=Path, help="Optional settings output path.")
    init.add_argument("--updater-name", default="Updater.exe", help="Standalone updater executable name.")
    init.add_argument("--force", action="store_true", help="Overwrite existing output file.")

    publish = subparsers.add_parser("publish", help="Publish a zip package to the NAS release root.")
    _add_settings_arg(publish)
    publish.add_argument("--app", required=True, help="Application id.")
    publish.add_argument("--version", required=True, help="Release version.")
    publish.add_argument("--package", required=True, type=Path, help="Release zip path.")
    publish.add_argument("--channel", default="", help="Release channel. Defaults to settings.")
    publish.add_argument("--notes", default="", help="Release notes.")
    publish.add_argument("--min-supported-version", default="", help="Minimum supported current version.")
    publish.add_argument("--mandatory", action="store_true", help="Mark this update as mandatory.")
    publish.add_argument("--published-at", default="", help="ISO timestamp. Defaults to current UTC time.")

    verify = subparsers.add_parser("verify", help="Verify one app manifest and package.")
    _add_settings_arg(verify)
    verify.add_argument("--app", required=True, help="Application id.")
    verify.add_argument("--channel", default="", help="Release channel. Defaults to settings.")

    check = subparsers.add_parser("check", help="Check whether an app version has an update.")
    _add_settings_arg(check)
    check.add_argument("--app", required=True, help="Application id.")
    check.add_argument("--current-version", required=True, help="Current app version.")
    check.add_argument("--channel", default="", help="Release channel. Defaults to settings.")
    check.add_argument("--skipped-version", default="", help="Skipped version.")
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
        return user_settings_path(_resolve_init_app_id(args))
    return None


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
    min_supported_version = args.min_supported_version or settings.default_minimum_version
    source_package_path = Path(args.package)
    if not source_package_path.is_file():
        raise UpdateError(UpdateErrorCode.PACKAGE_NOT_FOUND, f"package not found: {source_package_path}")
    target_package_path = source.package_path(args.app, args.version, settings.package_filename)
    target_package_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_package_path, target_package_path)
    package_size = target_package_path.stat().st_size
    package_sha256 = _sha256_of(target_package_path)
    relative_package_url = f"{args.app}/v{args.version}/{settings.package_filename}"
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
    manifest = UpdateManifest.from_payload(payload)
    _write_json(source.version_dir(args.app, args.version) / "latest.json", manifest.to_payload())
    _write_json(source.manifest_path(args.app, channel), manifest.to_payload())
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
    manifest_path = source.manifest_path(args.app, channel)
    manifest = _load_manifest_file(manifest_path)
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
    result = UpdateService(settings).check(
        app_id=args.app,
        current_version=args.current_version,
        channel=channel,
        skipped_version=args.skipped_version or None,
    )
    print(f"{result.decision.value}: {result.manifest.version}")
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
