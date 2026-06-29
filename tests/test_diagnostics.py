"""诊断报告测试。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from update_online_tool.diagnostics import collect_diagnostics, write_diagnostic_archive


def test_collect_diagnostics_reports_versions_and_failed_update(tmp_path: Path) -> None:
    """验证诊断报告包含版本、失败结果和问题摘要。"""
    install_root = _write_install_root(tmp_path, current_version="1.0.0", entry_name="MyTool.exe")
    (install_root / "update-result.json").write_text(
        json.dumps(
            {
                "success": False,
                "action": "install_prepared",
                "version": "1.1.0",
                "message": "package hash mismatch",
            }
        ),
        encoding="utf-8",
    )
    (install_root / "update-status.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "phase": "failed",
                "percent": 100,
                "message": "package hash mismatch",
                "version": "1.1.0",
                "previous_version": "1.0.0",
                "action": "install_prepared",
            }
        ),
        encoding="utf-8",
    )
    (install_root / "update.lock").write_text('{"pid": 123}\n', encoding="utf-8")

    report = collect_diagnostics(install_root=install_root)

    assert report["files"]["current_json"] is True
    assert report["path"]["write_probe"]["ok"] is True
    assert report["files"]["update_status_json"] is True
    assert report["update_status"]["payload"]["phase"] == "failed"
    assert report["files"]["update_lock"] is True
    assert report["installed_versions"]["versions"][0]["version"] == "1.0.0"
    assert "update.lock exists; an update may be running or stale" in report["problems"]
    assert "last update failed: package hash mismatch" in report["problems"]
    assert "last update status failed: package hash mismatch" in report["problems"]


def test_collect_diagnostics_reports_missing_current_entry(tmp_path: Path) -> None:
    """验证当前版本入口缺失会进入问题摘要。"""
    install_root = _write_install_root(tmp_path, current_version="1.0.0", entry_name="MyTool.exe")
    (install_root / "releases" / "1.0.0" / "MyTool.exe").unlink()

    report = collect_diagnostics(install_root=install_root)

    assert any("current release entry is missing" in problem for problem in report["problems"])


def test_collect_diagnostics_reports_unc_hints() -> None:
    """验证 UNC-like 安装根会输出路径提示。"""
    report = collect_diagnostics(install_root=Path("//server/share/MyTool"))

    assert report["path"]["is_unc_like"] is True
    assert any("UNC path detected" in hint for hint in report["path"]["hints"])
    assert any("install root is not writable" in problem for problem in report["problems"])


def test_write_diagnostic_archive_includes_allowed_files(tmp_path: Path) -> None:
    """验证诊断包包含报告、运行态 JSON 和日志。"""
    install_root = _write_install_root(tmp_path, current_version="1.0.0", entry_name="MyTool.exe")
    logs_dir = install_root / "logs"
    logs_dir.mkdir()
    (logs_dir / "updater.log").write_text("log", encoding="utf-8")
    (install_root / "update-status.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "phase": "success",
                "percent": 100,
                "message": "installed",
                "version": "1.1.0",
                "previous_version": "1.0.0",
                "action": "install_prepared",
            }
        ),
        encoding="utf-8",
    )
    report = collect_diagnostics(install_root=install_root)

    archive_path = write_diagnostic_archive(
        report=report,
        install_root=install_root,
        archive_path=tmp_path / "doctor.zip",
    )

    with zipfile.ZipFile(archive_path) as archive:
        names = set(archive.namelist())
    assert "doctor-report.json" in names
    assert "current.json" in names
    assert "update-status.json" in names
    assert "logs/updater.log" in names


def _write_install_root(tmp_path: Path, *, current_version: str, entry_name: str) -> Path:
    """写入测试安装根。"""
    install_root = tmp_path / "install"
    release_dir = install_root / "releases" / current_version
    release_dir.mkdir(parents=True)
    (release_dir / entry_name).write_text("current", encoding="utf-8")
    (install_root / "current.json").write_text(
        json.dumps(
            {
                "app_id": "my-tool",
                "version": current_version,
                "release_dir": f"releases/{current_version}",
                "executable": entry_name,
                "entry": {
                    "kind": "executable",
                    "path": entry_name,
                    "platform": "windows",
                },
            }
        ),
        encoding="utf-8",
    )
    return install_root
