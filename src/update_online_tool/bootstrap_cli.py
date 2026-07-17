"""稳定 Bootstrap 的窄 CLI。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from update_online_tool.errors import UpdateError
from update_online_tool.runtime import launch_current


def main(argv: list[str] | None = None) -> int:
    """按 current.json 启动当前 release。"""
    parser = argparse.ArgumentParser(prog="uot-bootstrap", description="Launch the current UOT release through a stable entry.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    launch = subparsers.add_parser("launch", help="Launch the release selected by current.json.")
    launch.add_argument("--install-root", required=True, type=Path, help="UOT install root.")
    args = parser.parse_args(argv)
    try:
        process = launch_current(install_root=args.install_root)
    except UpdateError as exc:
        _write_json({"ok": False, "error": {"code": exc.code.value, "message": exc.message}}, stream=sys.stderr)
        return 1
    _write_json({"ok": True, "release_pid": process.pid})
    return 0


def _write_json(payload: dict[str, object], *, stream: object | None = None) -> None:
    """写入供 Bootstrap 宿主解析的单行 JSON。"""
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=stream or sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
