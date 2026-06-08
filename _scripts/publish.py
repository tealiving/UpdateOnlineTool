"""兼容入口：委托到 NAS-only `uot publish`。"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src"
if SRC_ROOT.is_dir() and str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from update_online_tool.cli import main as uot_main  # noqa: E402


def main() -> None:
    """发布升级包。

    :return: None
    """
    parser = argparse.ArgumentParser(description="Publish a NAS-only update package to UpdateOnlineTool.")
    parser.add_argument("--settings", default="", help="settings.json path.")
    parser.add_argument("--app", required=True, help="Application id.")
    parser.add_argument("--version", required=True, help="Version, for example 1.0.6.")
    parser.add_argument("--channel", default="", help="Release channel.")
    parser.add_argument("--package", required=True, help="Release zip path.")
    parser.add_argument("--notes", default="", help="Release notes.")
    parser.add_argument("--min-supported-version", default="", help="Minimum supported current version.")
    parser.add_argument("--mandatory", action="store_true", help="Mark this update as mandatory.")
    parser.add_argument("--published-at", default="", help="ISO timestamp. Defaults to current UTC time.")
    parser.add_argument(
        "--url-prefix",
        default="",
        help="Deprecated and unsupported in NAS-only mode. Must be empty.",
    )
    args = parser.parse_args()

    if args.url_prefix:
        raise SystemExit("--url-prefix is not supported in NAS-only mode")

    cli_args = [
        "publish",
        "--app",
        args.app,
        "--version",
        args.version,
        "--package",
        args.package,
    ]
    if args.settings:
        cli_args.extend(["--settings", args.settings])
    if args.channel:
        cli_args.extend(["--channel", args.channel])
    if args.notes:
        cli_args.extend(["--notes", args.notes])
    if args.min_supported_version:
        cli_args.extend(["--min-supported-version", args.min_supported_version])
    if args.mandatory:
        cli_args.append("--mandatory")
    if args.published_at:
        cli_args.extend(["--published-at", args.published_at])
    raise SystemExit(uot_main(cli_args))


if __name__ == "__main__":
    main()
