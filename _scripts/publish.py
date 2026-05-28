"""UpdateOnlineTool 发布脚本 — 生成 latest.json 并复制发布包。

用法:
    python publish.py --app <app-id> --version <x.y.z> --channel <stable|beta> --package <zip-path>

:param app: 项目标识（子目录名）
:param version: 语义化版本号
:param channel: 发布通道
:param package: 发布包 zip 文件路径
:return: None
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def sha256_of(path: Path) -> str:
    """计算文件 SHA-256。

    :param path: 文件路径。
    :return: 十六进制哈希值。
    """
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    """脚本入口。

    :return: None
    """
    parser = argparse.ArgumentParser(description="发布更新包到 UpdateOnlineTool 仓库")
    parser.add_argument("--app", required=True, help="项目标识")
    parser.add_argument("--version", required=True, help="版本号")
    parser.add_argument("--channel", default="stable", choices=["stable", "beta", "nightly"])
    parser.add_argument("--package", required=True, help="发布包 zip 路径")
    parser.add_argument("--notes", default="", help="版本变更说明")
    parser.add_argument("--minimum-version", default="1.0.0", help="可增量升级最低版本")
    parser.add_argument("--url-prefix", default="", help="下载 URL 前缀，默认 file:// 本地路径")
    args = parser.parse_args()

    package_path = Path(args.package).resolve()
    if not package_path.is_file():
        raise FileNotFoundError(package_path)

    app_dir = REPO_ROOT / args.app
    version_dir = app_dir / f"v{args.version}"
    channel_dir = app_dir / args.channel
    version_dir.mkdir(parents=True, exist_ok=True)
    channel_dir.mkdir(parents=True, exist_ok=True)

    shutil.copy2(package_path, version_dir / package_path.name)

    file_sha256 = sha256_of(package_path)
    file_size = package_path.stat().st_size

    if args.url_prefix:
        package_url = f"{args.url_prefix.rstrip('/')}/{args.app}/v{args.version}/{package_path.name}"
    else:
        package_url = f"file:///{str(version_dir / package_path.name).replace(':', '').replace('\\', '/')}"

    manifest = {
        "app_id": args.app,
        "channel": args.channel,
        "version": args.version,
        "release_notes": args.notes or f"v{args.version} 发布",
        "minimum_version": args.minimum_version,
        "released_at": datetime.now(timezone.utc).isoformat(),
        "package": {
            "url": package_url,
            "size": file_size,
            "sha256": file_sha256,
            "filename": package_path.name,
        },
        "signature": {},
    }

    manifest_path = channel_dir / "latest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(f"已发布: {args.app} v{args.version} -> {channel_dir / 'latest.json'}")
    print(f"  SHA-256: {file_sha256}")
    print(f"  大小: {file_size:,} bytes")


if __name__ == "__main__":
    main()
