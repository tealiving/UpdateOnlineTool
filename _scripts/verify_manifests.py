"""Verify UpdateOnlineTool manifests against local packages.

:param argv: Command line arguments.
:return: Process exit code.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIRED_MANIFEST_KEYS = {
    "schema_version",
    "app_id",
    "channel",
    "version",
    "mandatory",
    "min_supported_version",
    "published_at",
    "notes",
    "package",
}
REQUIRED_PACKAGE_KEYS = {"url", "size", "sha256", "filename"}


def sha256_of(path: Path) -> str:
    """Calculate SHA-256 for a package file.

    :param path: Package file path.
    :return: Hex digest.
    """
    digest = hashlib.sha256()
    with path.open("rb") as file_obj:
        for chunk in iter(lambda: file_obj.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_manifest(path: Path) -> dict[str, Any]:
    """Load a JSON manifest object.

    :param path: Manifest path.
    :return: Manifest payload.
    """
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path}: manifest must be a JSON object")
    return payload


def verify_manifest(path: Path) -> list[str]:
    """Verify one manifest against its package when the package is local.

    :param path: Manifest path.
    :return: Error messages.
    """
    errors: list[str] = []
    payload = load_manifest(path)
    missing_manifest_keys = sorted(REQUIRED_MANIFEST_KEYS.difference(payload))
    if missing_manifest_keys:
        errors.append(f"{path}: missing keys: {', '.join(missing_manifest_keys)}")
    package = payload.get("package")
    if not isinstance(package, dict):
        return errors + [f"{path}: package must be an object"]
    missing_package_keys = sorted(REQUIRED_PACKAGE_KEYS.difference(package))
    if missing_package_keys:
        errors.append(f"{path}: package missing keys: {', '.join(missing_package_keys)}")

    filename = str(package.get("filename", "")).strip()
    if not filename:
        return errors
    package_path = path.parent / filename
    if not package_path.exists() and path.parent.name in {"stable", "beta", "nightly"}:
        version = str(payload.get("version", "")).strip()
        package_path = path.parent.parent / f"v{version}" / filename
    if not package_path.exists():
        errors.append(f"{path}: local package not found: {filename}")
        return errors

    expected_size = package.get("size")
    actual_size = package_path.stat().st_size
    if expected_size != actual_size:
        errors.append(f"{path}: package.size {expected_size!r} != actual {actual_size}")

    expected_sha256 = str(package.get("sha256", "")).strip().lower()
    actual_sha256 = sha256_of(package_path)
    if expected_sha256 != actual_sha256:
        errors.append(f"{path}: package.sha256 {expected_sha256} != actual {actual_sha256}")
    return errors


def iter_manifest_paths(root: Path, app: str | None) -> list[Path]:
    """Find manifest files to verify.

    :param root: Repository root.
    :param app: Optional app id.
    :return: Manifest paths.
    """
    base = root / app if app else root
    return sorted(base.glob("**/latest.json"))


def main(argv: list[str] | None = None) -> int:
    """Run manifest verification.

    :param argv: Command line arguments.
    :return: Process exit code.
    """
    parser = argparse.ArgumentParser(description="Verify manifest size and SHA-256 fields.")
    parser.add_argument("--app", default="", help="Only verify one app directory.")
    args = parser.parse_args(argv)

    manifest_paths = iter_manifest_paths(REPO_ROOT, args.app or None)
    if not manifest_paths:
        print("No latest.json files found", file=sys.stderr)
        return 2

    all_errors: list[str] = []
    for manifest_path in manifest_paths:
        all_errors.extend(verify_manifest(manifest_path))
    if all_errors:
        for error in all_errors:
            print(error, file=sys.stderr)
        return 1
    print(f"Verified {len(manifest_paths)} manifest(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
