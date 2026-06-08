"""兼容入口：校验 NAS-only UpdateOnlineTool manifests。"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.is_dir() and str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from update_online_tool.errors import UpdateError, UpdateErrorCode  # noqa: E402
from update_online_tool.manifest import UpdateManifest  # noqa: E402
from update_online_tool.nas import NasReleaseSource  # noqa: E402


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


def verify_manifest(path: Path, *, root: Path = REPO_ROOT) -> list[str]:
    """Verify one NAS-only manifest against its local package.

    :param path: Manifest path.
    :param root: Repository or NAS root used to resolve relative package URLs.
    :return: Error messages.
    """
    errors: list[str] = []
    try:
        manifest = UpdateManifest.from_payload(load_manifest(path))
    except (UpdateError, ValueError, json.JSONDecodeError) as exc:
        return [f"{path}: {exc}"]

    source = NasReleaseSource(root)
    try:
        package_path = source.resolve_package_path(manifest.package.url)
    except UpdateError as exc:
        return [f"{path}: {exc}"]

    if not package_path.exists():
        return [f"{path}: local package not found: {package_path}"]

    actual_size = package_path.stat().st_size
    if manifest.package.size != actual_size:
        errors.append(f"{path}: package.size {manifest.package.size!r} != actual {actual_size}")

    actual_sha256 = sha256_of(package_path)
    if manifest.package.sha256.lower() != actual_sha256:
        errors.append(f"{path}: package.sha256 {manifest.package.sha256.lower()} != actual {actual_sha256}")
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
    parser = argparse.ArgumentParser(description="Verify NAS-only manifest size and SHA-256 fields.")
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
