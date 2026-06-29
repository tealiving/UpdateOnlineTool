"""安装根诊断报告。"""

from __future__ import annotations

import json
import os
import time
import zipfile
from pathlib import Path
from typing import Any

from update_online_tool.errors import UpdateError
from update_online_tool.installed import list_installed_versions

MAX_LOG_BYTES = 1024 * 1024


def collect_diagnostics(*, install_root: Path, entry_name: str = "") -> dict[str, Any]:
    """收集安装根诊断信息。"""
    root = Path(install_root)
    report: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": int(time.time()),
        "install_root": str(root),
        "exists": root.exists(),
        "path": _path_status(root),
        "files": _file_status(root),
        "current": _read_json_file(root / "current.json"),
        "update_result": _read_json_file(root / "update-result.json"),
        "update_status": _read_json_file(root / "update-status.json"),
        "pending_update": _pending_update_summary(root / "pending-update.json"),
        "update_lock": _read_text_file(root / "update.lock"),
        "installed_versions": _installed_versions_summary(root, entry_name),
        "logs": _log_summaries(root),
        "problems": [],
    }
    report["problems"] = _detect_problems(report)
    return report


def write_diagnostic_archive(*, report: dict[str, Any], install_root: Path, archive_path: Path) -> Path:
    """写入诊断 zip 包。"""
    root = Path(install_root)
    target = Path(archive_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("doctor-report.json", json.dumps(report, ensure_ascii=False, indent=2) + "\n")
        for path in _diagnostic_files(root):
            if not path.is_file():
                continue
            try:
                if path.stat().st_size > MAX_LOG_BYTES:
                    continue
                archive.write(path, arcname=str(path.relative_to(root)))
            except OSError:
                continue
    return target


def _file_status(root: Path) -> dict[str, bool]:
    """生成关键文件存在性状态。"""
    return {
        "current_json": (root / "current.json").is_file(),
        "update_result_json": (root / "update-result.json").is_file(),
        "update_status_json": (root / "update-status.json").is_file(),
        "pending_update_json": (root / "pending-update.json").is_file(),
        "update_lock": (root / "update.lock").is_file(),
        "releases_dir": (root / "releases").is_dir(),
        "logs_dir": (root / "logs").is_dir(),
    }


def _path_status(root: Path) -> dict[str, Any]:
    """生成安装根路径和写权限摘要。"""
    return {
        "absolute": str(root.resolve()) if root.exists() else str(root.absolute()),
        "is_absolute": root.is_absolute(),
        "is_unc_like": _is_unc_like(root),
        "write_probe": _write_probe(root),
        "hints": _path_hints(root),
    }


def _is_unc_like(root: Path) -> bool:
    """判断路径是否像 Windows UNC。"""
    return str(root).replace("/", "\\").startswith("\\\\")


def _path_hints(root: Path) -> list[str]:
    """返回路径相关提示。"""
    if _is_unc_like(root):
        return [
            "UNC path detected; in JSON settings escape backslashes as \\\\server\\\\share or generate settings with uot init",
            "manifest package.url must remain a forward-slash relative path, not a UNC path",
        ]
    return []


def _write_probe(root: Path) -> dict[str, Any]:
    """用短临时文件探测安装根是否可写。"""
    if not root.exists():
        return {"ok": False, "error": "install root does not exist"}
    if not root.is_dir():
        return {"ok": False, "error": "install root is not a directory"}
    probe_path = root / f".uot-doctor-write-test.{os.getpid()}.tmp"
    try:
        probe_path.write_text("ok", encoding="utf-8")
        probe_path.unlink()
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "error": ""}


def _read_json_file(path: Path) -> dict[str, Any]:
    """读取 JSON 文件，保留错误信息。"""
    if not path.is_file():
        return {"exists": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"exists": True, "error": str(exc)}
    if not isinstance(payload, dict):
        return {"exists": True, "error": "JSON root is not an object"}
    return {"exists": True, "payload": payload}


def _read_text_file(path: Path) -> dict[str, Any]:
    """读取短文本文件。"""
    if not path.is_file():
        return {"exists": False}
    try:
        text = path.read_text(encoding="utf-8")[:4096]
    except OSError as exc:
        return {"exists": True, "error": str(exc)}
    return {"exists": True, "text": text}


def _pending_update_summary(path: Path) -> dict[str, Any]:
    """读取 pending-update.json 摘要。"""
    data = _read_json_file(path)
    payload = data.get("payload")
    if not isinstance(payload, dict):
        return data
    package_path = payload.get("package_path")
    manifest = payload.get("manifest")
    summary: dict[str, Any] = {
        "exists": True,
        "package_path": package_path if isinstance(package_path, str) else "",
        "package_exists": Path(package_path).is_file() if isinstance(package_path, str) else False,
        "manifest": {},
    }
    if isinstance(manifest, dict):
        signature = manifest.get("signature")
        summary["manifest"] = {
            "app_id": manifest.get("app_id", ""),
            "version": manifest.get("version", ""),
            "channel": manifest.get("channel", ""),
            "platform": manifest.get("platform", ""),
            "signature_algorithm": signature.get("algorithm", "") if isinstance(signature, dict) else "",
            "signature_key_id": signature.get("key_id", "") if isinstance(signature, dict) else "",
        }
    return summary


def _installed_versions_summary(root: Path, entry_name: str) -> dict[str, Any]:
    """列出已安装版本摘要。"""
    try:
        versions = list_installed_versions(install_root=root, entry_name=entry_name)
    except UpdateError as exc:
        return {"error": str(exc), "versions": []}
    return {
        "versions": [
            {
                "version": item.version,
                "release_dir": str(item.release_dir),
                "entry_path": str(item.entry_path),
                "entry_exists": item.entry_exists,
                "entry_kind": item.entry_kind,
                "is_current": item.is_current,
            }
            for item in versions
        ]
    }


def _log_summaries(root: Path) -> list[dict[str, Any]]:
    """收集日志文件摘要。"""
    summaries: list[dict[str, Any]] = []
    for path in _log_files(root):
        try:
            stat = path.stat()
        except OSError:
            continue
        summaries.append(
            {
                "path": str(path),
                "relative_path": str(path.relative_to(root)),
                "size": stat.st_size,
                "included_in_archive": stat.st_size <= MAX_LOG_BYTES,
            }
        )
    return summaries


def _detect_problems(report: dict[str, Any]) -> list[str]:
    """根据诊断报告生成常见问题。"""
    problems: list[str] = []
    files = report.get("files")
    path = report.get("path")
    if not report.get("exists"):
        problems.append("install root does not exist")
    if isinstance(path, dict):
        write_probe = path.get("write_probe")
        if isinstance(write_probe, dict) and write_probe.get("ok") is False:
            problems.append(f"install root is not writable: {write_probe.get('error', '')}")
    if isinstance(files, dict):
        if not files.get("current_json"):
            problems.append("current.json is missing")
        if not files.get("releases_dir"):
            problems.append("releases directory is missing")
        if files.get("update_lock"):
            problems.append("update.lock exists; an update may be running or stale")
    update_result = report.get("update_result")
    if isinstance(update_result, dict):
        payload = update_result.get("payload")
        if isinstance(payload, dict) and payload.get("success") is False:
            problems.append(f"last update failed: {payload.get('message', '')}")
    update_status = report.get("update_status")
    if isinstance(update_status, dict):
        payload = update_status.get("payload")
        if isinstance(payload, dict) and payload.get("phase") == "failed":
            problems.append(f"last update status failed: {payload.get('message', '')}")
    installed = report.get("installed_versions")
    if isinstance(installed, dict):
        for item in installed.get("versions", []):
            if isinstance(item, dict) and item.get("is_current") and not item.get("entry_exists"):
                problems.append(f"current release entry is missing: {item.get('entry_path', '')}")
    pending = report.get("pending_update")
    if isinstance(pending, dict) and pending.get("exists") and not pending.get("package_exists", True):
        problems.append(f"pending package is missing: {pending.get('package_path', '')}")
    return problems


def _diagnostic_files(root: Path) -> list[Path]:
    """返回允许放入诊断包的文件。"""
    files = [
        root / "current.json",
        root / "update-result.json",
        root / "update-status.json",
        root / "pending-update.json",
        root / "update.lock",
    ]
    files.extend(_log_files(root))
    return files


def _log_files(root: Path) -> list[Path]:
    """列出安装根日志文件。"""
    paths: list[Path] = []
    logs_dir = root / "logs"
    if logs_dir.is_dir():
        paths.extend(path for path in logs_dir.rglob("*") if path.is_file())
    paths.extend(path for path in root.glob("*.log") if path.is_file())
    return sorted(set(paths), key=lambda item: str(item))
