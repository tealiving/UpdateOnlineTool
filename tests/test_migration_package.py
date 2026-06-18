"""旧客户端迁移包模板测试。"""

from __future__ import annotations

import json
from pathlib import Path

from update_online_tool.migration_package import verify_migration_package, write_migration_package_template


def test_write_migration_package_template_copies_artifacts(tmp_path: Path) -> None:
    """验证迁移包模板包含计划、说明、验证脚本和可选 artifact。"""
    settings_path = tmp_path / "settings.json"
    endpoint_path = tmp_path / "update-endpoint.json"
    updater_bundle = tmp_path / "MyToolUpdater"
    settings_path.write_text('{"nas":{"root":"/mnt/nas"}}', encoding="utf-8")
    endpoint_path.write_text('{"channel":"stable"}', encoding="utf-8")
    updater_bundle.mkdir()
    (updater_bundle / "MyToolUpdater.exe").write_text("updater", encoding="utf-8")

    result = write_migration_package_template(
        output_dir=tmp_path / "migration",
        app_id="my-tool",
        version="1.0.0",
        entry_name="MyTool.exe",
        platform="windows",
        updater_bundle=updater_bundle,
        settings_path=settings_path,
        endpoint_path=endpoint_path,
    )

    plan = json.loads(result.plan_path.read_text(encoding="utf-8"))
    verification = verify_migration_package(package_dir=result.output_dir)
    assert plan["app_id"] == "my-tool"
    assert plan["artifacts"]["updater"] == "updater/MyToolUpdater"
    assert (result.output_dir / "updater" / "MyToolUpdater" / "MyToolUpdater.exe").is_file()
    assert (result.output_dir / "config" / "settings.json").is_file()
    assert (result.output_dir / "update-endpoint.json").is_file()
    assert verification.valid is True
    assert verification.errors == []


def test_write_migration_package_template_quotes_command_arguments(tmp_path: Path) -> None:
    """验证迁移包命令示例能处理带空格的入口名。"""
    result = write_migration_package_template(
        output_dir=tmp_path / "migration",
        app_id="my tool",
        version="1.0.0",
        entry_name="My Tool.exe",
    )

    plan = json.loads(result.plan_path.read_text(encoding="utf-8"))

    assert "--entry-name 'My Tool.exe'" in plan["commands"]["migrate"]
    assert "--app 'my tool'" in plan["commands"]["migrate"]
    assert plan["command_args"]["migrate"] == [
        "uot",
        "migrate-install-root",
        "--install-root",
        "<legacy-install-root>",
        "--version",
        "1.0.0",
        "--entry-name",
        "My Tool.exe",
        "--app",
        "my tool",
    ]


def test_verify_migration_package_reports_missing_artifact(tmp_path: Path) -> None:
    """验证迁移包校验会报告缺失 artifact。"""
    output_dir = tmp_path / "migration"
    write_migration_package_template(
        output_dir=output_dir,
        app_id="my-tool",
        version="1.0.0",
        entry_name="MyTool.exe",
    )
    plan_path = output_dir / "migration.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["artifacts"] = {"updater": "updater/MissingUpdater"}
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    verification = verify_migration_package(package_dir=output_dir)

    assert verification.valid is False
    assert verification.errors == ["artifact missing: updater/MissingUpdater"]


def test_verify_migration_package_rejects_unsafe_artifact_path(tmp_path: Path) -> None:
    """验证迁移包校验拒绝 artifact 路径穿越。"""
    output_dir = tmp_path / "migration"
    write_migration_package_template(
        output_dir=output_dir,
        app_id="my-tool",
        version="1.0.0",
        entry_name="MyTool.exe",
    )
    plan_path = output_dir / "migration.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["artifacts"] = {"updater": "../outside"}
    plan_path.write_text(json.dumps(plan), encoding="utf-8")

    verification = verify_migration_package(package_dir=output_dir)

    assert verification.valid is False
    assert verification.errors == ["artifact path unsafe: ../outside"]
