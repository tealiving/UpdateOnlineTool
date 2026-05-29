"""UpdateOnlineTool release publisher.

:param argv: Command line arguments.
:return: Process exit code.
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
    """Calculate a file SHA-256 digest.

    :param path: File path.
    :return: Hex digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_package_url(*, url_prefix: str, app: str, version: str, filename: str, package_path: Path) -> str:
    """Build the package URL written to manifest.

    :param url_prefix: Optional HTTP/file URL prefix.
    :param app: Application id.
    :param version: Release version.
    :param filename: Package filename.
    :param package_path: Local copied package path.
    :return: Package URL.
    """
    if url_prefix:
        return f"{url_prefix.rstrip('/')}/{app}/v{version}/{filename}"
    return package_path.resolve().as_uri()


def write_manifest(path: Path, payload: dict[str, object]) -> None:
    """Write a JSON manifest with stable formatting.

    :param path: Target manifest path.
    :param payload: Manifest payload.
    :return: None
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    """Publish a package and update latest.json.

    :return: None
    """
    parser = argparse.ArgumentParser(description="Publish an update package to UpdateOnlineTool.")
    parser.add_argument("--app", required=True, help="Application id.")
    parser.add_argument("--version", required=True, help="Version, for example 1.0.5.")
    parser.add_argument("--channel", default="stable", choices=["stable", "beta", "nightly"])
    parser.add_argument("--package", required=True, help="Release zip path.")
    parser.add_argument("--notes", default="", help="Release notes.")
    parser.add_argument("--min-supported-version", default="1.0.0", help="Minimum supported current version.")
    parser.add_argument("--mandatory", action="store_true", help="Mark this update as mandatory.")
    parser.add_argument("--url-prefix", default="", help="Download URL prefix. Defaults to local file:// URL.")
    parser.add_argument("--published-at", default="", help="ISO timestamp. Defaults to current UTC time.")
    args = parser.parse_args()

    source_package_path = Path(args.package).resolve()
    if not source_package_path.is_file():
        raise FileNotFoundError(source_package_path)

    app_dir = REPO_ROOT / args.app
    version_dir = app_dir / f"v{args.version}"
    channel_dir = app_dir / args.channel
    version_dir.mkdir(parents=True, exist_ok=True)
    channel_dir.mkdir(parents=True, exist_ok=True)

    copied_package_path = version_dir / source_package_path.name
    if source_package_path != copied_package_path.resolve():
        shutil.copy2(source_package_path, copied_package_path)

    file_sha256 = sha256_of(copied_package_path)
    file_size = copied_package_path.stat().st_size
    package_url = build_package_url(
        url_prefix=str(args.url_prefix),
        app=str(args.app),
        version=str(args.version),
        filename=source_package_path.name,
        package_path=copied_package_path,
    )
    published_at = str(args.published_at).strip() or datetime.now(timezone.utc).isoformat()

    manifest: dict[str, object] = {
        "schema_version": 2,
        "app_id": args.app,
        "channel": args.channel,
        "version": args.version,
        "mandatory": bool(args.mandatory),
        "min_supported_version": args.min_supported_version,
        "published_at": published_at,
        "notes": args.notes or f"v{args.version} release",
        "package": {
            "url": package_url,
            "size": file_size,
            "sha256": file_sha256,
            "filename": source_package_path.name,
        },
    }

    write_manifest(version_dir / "latest.json", manifest)
    write_manifest(channel_dir / "latest.json", manifest)

    print(f"Published: {args.app} v{args.version} -> {channel_dir / 'latest.json'}")
    print(f"  SHA-256: {file_sha256}")
    print(f"  Size: {file_size:,} bytes")
    print(f"  URL: {package_url}")


if __name__ == "__main__":
    main()
