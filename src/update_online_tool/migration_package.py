"""旧客户端迁移包模板与校验。"""

from __future__ import annotations

import json
import shlex
import shutil
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from update_online_tool.errors import UpdateError, UpdateErrorCode


@dataclass(frozen=True)
class MigrationPackageResult:
    """迁移包模板生成结果。"""

    output_dir: Path
    plan_path: Path
    readme_path: Path
    verify_script: Path
    copied_artifacts: list[Path]

    def to_payload(self) -> dict[str, object]:
        """转换为 JSON 负载。"""
        return {
            "output_dir": str(self.output_dir),
            "plan_path": str(self.plan_path),
            "readme_path": str(self.readme_path),
            "verify_script": str(self.verify_script),
            "copied_artifacts": [str(path) for path in self.copied_artifacts],
        }


@dataclass(frozen=True)
class MigrationPackageVerification:
    """迁移包模板校验结果。"""

    package_dir: Path
    valid: bool
    errors: list[str]
    warnings: list[str]

    def to_payload(self) -> dict[str, object]:
        """转换为 JSON 负载。"""
        return {
            "package_dir": str(self.package_dir),
            "valid": self.valid,
            "errors": self.errors,
            "warnings": self.warnings,
        }


def write_migration_package_template(
    *,
    output_dir: Path,
    app_id: str,
    version: str,
    entry_name: str,
    platform: str = "",
    updater_bundle: Path | None = None,
    settings_path: Path | None = None,
    endpoint_path: Path | None = None,
    force: bool = False,
) -> MigrationPackageResult:
    """生成旧客户端升级到标准安装根结构的迁移包模板。"""
    root = Path(output_dir)
    if root.exists():
        if not force:
            raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"output already exists: {root}")
        shutil.rmtree(root)
    root.mkdir(parents=True)

    copied_artifacts: list[Path] = []
    artifact_payload: dict[str, str] = {}
    if updater_bundle is not None:
        target = _copy_artifact(Path(updater_bundle), root / "updater")
        copied_artifacts.append(target)
        artifact_payload["updater"] = _relative_posix(target, root)
    if settings_path is not None:
        target = root / "config" / "settings.json"
        _copy_file(Path(settings_path), target, "settings")
        copied_artifacts.append(target)
        artifact_payload["settings"] = _relative_posix(target, root)
    if endpoint_path is not None:
        target = root / "update-endpoint.json"
        _copy_file(Path(endpoint_path), target, "endpoint")
        copied_artifacts.append(target)
        artifact_payload["endpoint"] = _relative_posix(target, root)

    plan_path = root / "migration.json"
    readme_path = root / "README.md"
    verify_script = root / "scripts" / "verify_migration_package.py"
    plan = _migration_plan_payload(
        app_id=app_id,
        version=version,
        entry_name=entry_name,
        platform=platform,
        artifacts=artifact_payload,
    )
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    readme_path.write_text(_migration_readme(plan), encoding="utf-8")
    verify_script.parent.mkdir(parents=True, exist_ok=True)
    verify_script.write_text(_verify_script_text(), encoding="utf-8")
    return MigrationPackageResult(
        output_dir=root,
        plan_path=plan_path,
        readme_path=readme_path,
        verify_script=verify_script,
        copied_artifacts=copied_artifacts,
    )


def verify_migration_package(*, package_dir: Path) -> MigrationPackageVerification:
    """校验迁移包模板结构是否完整。"""
    root = Path(package_dir)
    errors: list[str] = []
    warnings: list[str] = []
    if not root.is_dir():
        return MigrationPackageVerification(root, False, [f"package directory not found: {root}"], warnings)
    plan_path = root / "migration.json"
    readme_path = root / "README.md"
    verify_script = root / "scripts" / "verify_migration_package.py"
    plan = _read_plan(plan_path, errors)
    if plan:
        _validate_required_string(plan, "app_id", errors)
        _validate_required_string(plan, "version", errors)
        _validate_required_string(plan, "entry_name", errors)
        artifacts = plan.get("artifacts")
        if isinstance(artifacts, dict):
            for name, relative_path in artifacts.items():
                if not isinstance(relative_path, str) or not relative_path.strip():
                    errors.append(f"artifact path must be non-empty: {name}")
                    continue
                artifact_path = _safe_artifact_path(root, relative_path, errors)
                if artifact_path is None:
                    continue
                if not artifact_path.exists():
                    errors.append(f"artifact missing: {relative_path}")
        elif artifacts is not None:
            errors.append("artifacts must be an object")
    if not readme_path.is_file():
        warnings.append(f"README.md not found: {readme_path}")
    if not verify_script.is_file():
        errors.append(f"verify script not found: {verify_script}")
    return MigrationPackageVerification(root, not errors, errors, warnings)


def _migration_plan_payload(
    *,
    app_id: str,
    version: str,
    entry_name: str,
    platform: str,
    artifacts: dict[str, str],
) -> dict[str, object]:
    """构造 migration.json。"""
    normalized_app_id = _require_text(app_id, "app_id")
    normalized_version = _require_text(version, "version")
    normalized_entry = _require_text(entry_name, "entry_name")
    migrate_args = [
        "--install-root",
        "<legacy-install-root>",
        "--version",
        normalized_version,
        "--entry-name",
        normalized_entry,
        "--app",
        normalized_app_id,
    ]
    normalized_platform = str(platform or "").strip()
    if normalized_platform:
        migrate_args.extend(["--platform", normalized_platform])
    dry_run_args = ["uot", "migrate-install-root", *migrate_args, "--dry-run"]
    migrate_command_args = ["uot", "migrate-install-root", *migrate_args]
    verify_args = ["uot", "verify-migration-package", "--package-dir", "."]
    return {
        "schema_version": 1,
        "app_id": normalized_app_id,
        "version": normalized_version,
        "entry_name": normalized_entry,
        "platform": normalized_platform,
        "artifacts": artifacts,
        "commands": {
            "dry_run": _shell_join(dry_run_args),
            "migrate": _shell_join(migrate_command_args),
            "verify_package": _shell_join(verify_args),
        },
        "command_args": {
            "dry_run": dry_run_args,
            "migrate": migrate_command_args,
            "verify_package": verify_args,
        },
    }


def _migration_readme(plan: dict[str, object]) -> str:
    """生成迁移包 README。"""
    commands = plan.get("commands")
    command_payload = commands if isinstance(commands, dict) else {}
    return f"""# UOT Legacy Migration Package

This package helps migrate an existing flat install root to the UOT releases/current.json layout.

## Verify

```bash
{command_payload.get("verify_package", "uot verify-migration-package --package-dir .")}
```

## Dry Run

```bash
{command_payload.get("dry_run", "")}
```

## Migrate

```bash
{command_payload.get("migrate", "")}
```
"""


def _verify_script_text() -> str:
    """生成随迁移包携带的校验脚本。"""
    return """from __future__ import annotations

import json
from pathlib import Path

from update_online_tool.migration_package import verify_migration_package


if __name__ == "__main__":
    result = verify_migration_package(package_dir=Path(__file__).resolve().parents[1])
    print(json.dumps(result.to_payload(), ensure_ascii=False, indent=2))
    raise SystemExit(0 if result.valid else 1)
"""


def _copy_artifact(source: Path, target_root: Path) -> Path:
    """复制文件或目录 artifact。"""
    if not source.exists():
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"artifact not found: {source}")
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / source.name
    if source.is_dir():
        shutil.copytree(source, target)
    else:
        shutil.copy2(source, target)
    return target


def _copy_file(source: Path, target: Path, label: str) -> None:
    """复制单个文件。"""
    if not source.is_file():
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"{label} file not found: {source}")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _read_plan(plan_path: Path, errors: list[str]) -> dict[str, Any]:
    """读取 migration.json。"""
    if not plan_path.is_file():
        errors.append(f"migration.json not found: {plan_path}")
        return {}
    try:
        payload = json.loads(plan_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        errors.append(f"migration.json is not valid JSON: {exc}")
        return {}
    if not isinstance(payload, dict):
        errors.append("migration.json must be an object")
        return {}
    return payload


def _validate_required_string(payload: dict[str, Any], field_name: str, errors: list[str]) -> None:
    """校验必填字符串字段。"""
    value = payload.get(field_name)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field_name} must be a non-empty string")


def _safe_artifact_path(root: Path, relative_path: str, errors: list[str]) -> Path | None:
    """解析迁移包内 artifact 路径，拒绝绝对路径和目录穿越。"""
    normalized_path = str(relative_path or "").replace("\\", "/")
    pure_path = PurePosixPath(normalized_path)
    parts = tuple(part for part in pure_path.parts if part)
    if pure_path.is_absolute() or not parts or ".." in parts or ":" in parts[0]:
        errors.append(f"artifact path unsafe: {relative_path}")
        return None
    candidate = root.joinpath(*parts)
    try:
        candidate.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        errors.append(f"artifact path unsafe: {relative_path}")
        return None
    return candidate


def _require_text(value: str, field_name: str) -> str:
    """读取非空文本。"""
    text = str(value or "").strip()
    if not text:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"{field_name} must be non-empty")
    return text


def _shell_join(args: list[str]) -> str:
    """生成可复制执行的 POSIX shell 命令。"""
    return " ".join(_quote_command_arg(arg) for arg in args)


def _quote_command_arg(arg: str) -> str:
    """引用命令参数；保留文档占位符的可读形态。"""
    if arg.startswith("<") and arg.endswith(">"):
        return arg
    return shlex.quote(arg)


def _relative_posix(path: Path, root: Path) -> str:
    """返回相对 POSIX 路径。"""
    return path.relative_to(root).as_posix()
