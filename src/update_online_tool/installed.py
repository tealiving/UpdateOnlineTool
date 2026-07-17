"""安装根版本状态管理。"""

from __future__ import annotations

import json
import os
import shutil
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.install_root import missing_current_json_message, normalize_install_root
from update_online_tool.locks import runtime_lock
from update_online_tool.release_contract import normalize_release_required_paths, validate_release_artifact
from update_online_tool.versioning import parse_version_tuple


@dataclass(frozen=True)
class InstalledVersion:
    """安装根中的一个 release 版本。

    :param version: release 版本号。
    :param release_dir: release 目录。
    :param entry_path: release 入口路径。
    :param entry_exists: release 入口是否存在。
    :param entry_kind: executable 或 app_bundle。
    :param is_current: 是否为当前激活版本。
    :return: None
    """

    version: str
    release_dir: Path
    entry_path: Path
    entry_exists: bool
    entry_kind: str
    is_current: bool
    release_valid: bool = True
    validation_error: str = ""


@dataclass(frozen=True)
class MigrationResult:
    """旧安装根迁移结果。"""

    version: str
    install_root: Path
    release_dir: Path
    entry_path: Path
    copied_items: list[str]
    current_json: Path
    dry_run: bool

    def to_payload(self) -> dict[str, object]:
        """转换为 JSON 负载。"""
        return {
            "version": self.version,
            "install_root": str(self.install_root),
            "release_dir": str(self.release_dir),
            "entry_path": str(self.entry_path),
            "copied_items": self.copied_items,
            "current_json": str(self.current_json),
            "dry_run": self.dry_run,
        }


def list_installed_versions(
    *,
    install_root: Path,
    entry_name: str = "",
    release_required_paths: tuple[str, ...] | list[str] = (),
) -> list[InstalledVersion]:
    """列出安装根已存在的 release 版本。

    :param install_root: 安装根目录。
    :param entry_name: 可选入口名；为空时从 current.json 推断。
    :param release_required_paths: 宿主要求每个 release 都存在的相对资源路径。
    :return: 已安装版本列表，按版本倒序排列；不完整 release 会保留在结果中并标记无效。
    """
    root = normalize_install_root(Path(install_root))
    releases_root = root / "releases"
    if not releases_root.is_dir():
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"releases directory not found: {releases_root}")
    current_payload = _read_current_json(root, required=False)
    current_version = str(current_payload.get("version", "")).strip() if current_payload else ""
    required_paths = normalize_release_required_paths(release_required_paths)
    current_app_id = str(current_payload.get("app_id", "")).strip() if current_payload else ""
    current_platform = _current_platform(current_payload) if current_payload else ""
    versions: list[InstalledVersion] = []
    for release_dir in releases_root.iterdir():
        if not release_dir.is_dir():
            continue
        version = release_dir.name
        resolved_entry_name = _resolve_release_entry_name(
            entry_name=entry_name,
            current_payload=current_payload,
            release_dir=release_dir,
        )
        entry_path = release_dir / resolved_entry_name
        entry_exists = _is_entry_path(entry_path)
        release_valid = entry_exists
        validation_error = ""
        if entry_exists:
            try:
                validate_release_artifact(
                    release_dir=release_dir,
                    version=version,
                    entry_path=resolved_entry_name,
                    app_id=current_app_id,
                    platform=current_platform,
                    required_paths=required_paths,
                )
            except UpdateError as exc:
                release_valid = False
                validation_error = exc.message
        else:
            validation_error = f"release entry not found: {entry_path}"
        versions.append(
            InstalledVersion(
                version=version,
                release_dir=release_dir,
                entry_path=entry_path,
                entry_exists=entry_exists,
                entry_kind=_entry_kind(resolved_entry_name),
                is_current=version == current_version,
                release_valid=release_valid,
                validation_error=validation_error,
            )
        )
    return sorted(versions, key=lambda item: (parse_version_tuple(item.version), item.version), reverse=True)


def migrate_install_root(
    *,
    install_root: Path,
    version: str,
    entry_name: str,
    app_id: str,
    platform: str = "",
    force: bool = False,
    dry_run: bool = False,
) -> MigrationResult:
    """把旧版平铺安装根迁移为 releases/current.json 结构。"""
    root = normalize_install_root(Path(install_root))
    target_version = str(version or "").strip()
    if not target_version:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "version must be non-empty")
    normalized_entry = str(entry_name or "").strip()
    if not normalized_entry:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "entry_name must be non-empty")
    normalized_app_id = str(app_id or "").strip()
    if not normalized_app_id:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "app_id must be non-empty")
    source_entry = root / normalized_entry
    if not root.is_dir():
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"install root not found: {root}")
    if not _is_entry_path(source_entry):
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"entry not found in install root: {source_entry}")
    release_dir = root / "releases" / target_version
    if release_dir.exists() and not force:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"release already exists: {release_dir}")
    copied_items = _migration_source_items(root)
    if dry_run:
        return MigrationResult(
            version=target_version,
            install_root=root,
            release_dir=release_dir,
            entry_path=release_dir / normalized_entry,
            copied_items=[item.name for item in copied_items],
            current_json=root / "current.json",
            dry_run=True,
        )
    if release_dir.exists():
        shutil.rmtree(release_dir)
    release_dir.mkdir(parents=True)
    try:
        for item in copied_items:
            target = release_dir / item.name
            if item.is_dir():
                shutil.copytree(item, target, symlinks=True)
            else:
                shutil.copy2(item, target)
        _write_current_json_atomic(
            root / "current.json",
            {
                "app_id": normalized_app_id,
                "version": target_version,
                "release_dir": f"releases/{target_version}",
                "executable": normalized_entry,
                "entry": {
                    "kind": _entry_kind(normalized_entry),
                    "path": normalized_entry,
                    "platform": str(platform or "").strip(),
                },
            },
        )
    except Exception:
        if release_dir.exists():
            shutil.rmtree(release_dir)
        raise
    return MigrationResult(
        version=target_version,
        install_root=root,
        release_dir=release_dir,
        entry_path=release_dir / normalized_entry,
        copied_items=[item.name for item in copied_items],
        current_json=root / "current.json",
        dry_run=False,
    )


def switch_installed_version(
    *,
    install_root: Path,
    version: str,
    entry_name: str = "",
    app_id: str = "",
    platform: str = "",
    release_required_paths: tuple[str, ...] | list[str] = (),
    use_lock: bool = True,
) -> InstalledVersion:
    """切换安装根 current.json 到已安装版本。

    :param install_root: 安装根目录。
    :param version: 目标版本号。
    :param entry_name: 可选入口名；为空时从 current.json 推断。
    :param app_id: 可选应用标识；为空时沿用 current.json。
    :param platform: 可选平台；为空时沿用 current.json entry.platform。
    :param release_required_paths: 宿主要求目标 release 存在的相对资源路径。
    :param use_lock: 是否使用安装根 update.lock；runtime 已持锁时传 False。
    :return: 切换后的版本信息。
    """
    root = normalize_install_root(Path(install_root))
    target_version = str(version or "").strip()
    required_paths = normalize_release_required_paths(release_required_paths)
    if not target_version:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "version must be non-empty")
    lock = runtime_lock(root, action="switch_installed") if use_lock else nullcontext()
    with lock:
        current_payload = _read_current_json(root, required=True)
        release_dir = root / "releases" / target_version
        resolved_entry_name = _resolve_release_entry_name(
            entry_name=entry_name,
            current_payload=current_payload,
            release_dir=release_dir,
        )
        entry_path = release_dir / resolved_entry_name
        if not release_dir.is_dir():
            raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"release directory not found: {release_dir}")
        if not _is_entry_path(entry_path):
            raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"release entry not found: {entry_path}")
        resolved_app_id = str(app_id or current_payload.get("app_id") or "").strip()
        if not resolved_app_id:
            raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "app_id must be provided or present in current.json")
        resolved_platform = str(platform or _current_platform(current_payload)).strip()
        validate_release_artifact(
            release_dir=release_dir,
            version=target_version,
            entry_path=resolved_entry_name,
            app_id=resolved_app_id,
            platform=resolved_platform,
            required_paths=required_paths,
        )
        previous_version = str(current_payload.get("version", "")).strip()
        payload: dict[str, object] = dict(current_payload)
        entry_payload = current_payload.get("entry")
        entry = dict(entry_payload) if isinstance(entry_payload, dict) else {}
        entry.update(
            {
                "kind": _entry_kind(resolved_entry_name),
                "path": resolved_entry_name,
                "platform": resolved_platform,
            }
        )
        payload.update(
            {
                "app_id": resolved_app_id,
                "version": target_version,
                "release_dir": f"releases/{target_version}",
                "executable": resolved_entry_name,
                "entry": entry,
            }
        )
        if previous_version and previous_version != target_version:
            payload["previous_version"] = previous_version
        _write_current_json_atomic(root / "current.json", payload)
        return InstalledVersion(
            version=target_version,
            release_dir=release_dir,
            entry_path=entry_path,
            entry_exists=True,
            entry_kind=_entry_kind(resolved_entry_name),
            is_current=True,
            release_valid=True,
        )


def _read_current_json(install_root: Path, *, required: bool) -> dict[str, Any]:
    """读取安装根 current.json。"""
    path = Path(install_root) / "current.json"
    if not path.is_file():
        if required:
            raise UpdateError(UpdateErrorCode.MANIFEST_NOT_FOUND, missing_current_json_message(Path(install_root)))
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"current.json is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "current.json must be a JSON object")
    return payload


def _resolve_entry_name(entry_name: str, current_payload: dict[str, Any]) -> str:
    """解析 release 入口名。"""
    explicit = str(entry_name or "").strip()
    if explicit:
        return explicit
    entry_payload = current_payload.get("entry")
    if isinstance(entry_payload, dict):
        entry_path = entry_payload.get("path")
        if isinstance(entry_path, str) and entry_path.strip():
            return entry_path.strip()
    executable = current_payload.get("executable")
    if isinstance(executable, str) and executable.strip():
        return executable.strip()
    raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "entry_name is required when current.json has no entry")


def _resolve_release_entry_name(*, entry_name: str, current_payload: dict[str, Any], release_dir: Path) -> str:
    """按目标 release 解析入口名，兼容裸可执行文件与 .app 互切。"""
    resolved = _resolve_entry_name(entry_name, current_payload)
    if str(entry_name or "").strip() or _is_entry_path(Path(release_dir) / resolved):
        return resolved
    for candidate in _compatible_entry_names(resolved):
        if _is_entry_path(Path(release_dir) / candidate):
            return candidate
    return resolved


def _compatible_entry_names(entry_name: str) -> tuple[str, ...]:
    """返回入口形态兼容候选。"""
    if entry_name.endswith(".app"):
        return (entry_name[:-4],)
    return (f"{entry_name}.app",)


def _current_platform(current_payload: dict[str, Any]) -> str:
    """从 current.json 读取平台。"""
    entry_payload = current_payload.get("entry")
    if isinstance(entry_payload, dict):
        platform = entry_payload.get("platform")
        if isinstance(platform, str):
            return platform
    return ""


def _write_current_json_atomic(path: Path, payload: dict[str, object]) -> None:
    """原子写 current.json。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp_path.replace(path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise


def _is_entry_path(path: Path) -> bool:
    """判断入口路径是否存在。"""
    return path.is_file() or _is_macos_app_bundle(path)


def _is_macos_app_bundle(path: Path) -> bool:
    """判断路径是否为 macOS .app bundle。"""
    if not path.is_dir() or path.suffix != ".app":
        return False
    macos_dir = path / "Contents" / "MacOS"
    return macos_dir.is_dir() and any(candidate.is_file() for candidate in macos_dir.iterdir())


def _entry_kind(entry_name: str) -> str:
    """生成 current.json entry.kind。"""
    return "app_bundle" if entry_name.endswith(".app") else "executable"


def _migration_source_items(root: Path) -> list[Path]:
    """列出旧安装根中需要复制进 release 的条目。"""
    excluded = {
        "releases",
        "current.json",
        "pending-update.json",
        "update-result.json",
        "update.lock",
        "logs",
    }
    return sorted(
        [item for item in root.iterdir() if item.name not in excluded],
        key=lambda item: item.name,
    )
