"""供打包后的独立 Update Agent 调用的窄 CLI。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from update_online_tool.agent import UpdateAgent, read_agent_request
from update_online_tool.errors import UpdateError, UpdateErrorCode


def main(argv: list[str] | None = None) -> int:
    """执行 Agent 请求并输出单行 JSON 结果。"""
    parser = argparse.ArgumentParser(prog="uot-agent", description="Run one durable UOT update-agent request.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command, help_text in (
        ("apply", "Install one prepared remote update request."),
        ("switch", "Switch one installed release request."),
        ("rollback", "Rollback one installed release request."),
    ):
        item = subparsers.add_parser(command, help=help_text)
        item.add_argument("--request", required=True, type=Path, help="Agent request JSON path.")
    args = parser.parse_args(argv)
    try:
        request = read_agent_request(args.request)
        if request.action != args.command:
            raise UpdateError(
                UpdateErrorCode.SETTINGS_INVALID,
                f"agent command {args.command} does not match request action {request.action}",
            )
        result = UpdateAgent().run_request(args.request)
    except UpdateError as exc:
        _write_json({"ok": False, "error": {"code": exc.code.value, "message": exc.message}}, stream=sys.stderr)
        return 1
    _write_json(
        {
            "ok": True,
            "operation_id": result.operation_id,
            "version": result.runtime_result.version,
            "bootstrap_pid": result.bootstrap_pid,
        }
    )
    return 0


def _write_json(payload: dict[str, object], *, stream: object | None = None) -> None:
    """写入供宿主解析的单行 JSON。"""
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=stream or sys.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
