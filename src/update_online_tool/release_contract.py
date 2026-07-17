"""Release artifact contract and validation helpers."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

from update_online_tool.errors import UpdateError, UpdateErrorCode

RELEASE_CONTRACT_FILENAME = "uot-release.json"
RELEASE_CONTRACT_SCHEMA_VERSION = 1
_CONTRACT_KEYS = {"schema_version", "app_id", "version", "platform", "entry_path", "required_paths"}


@dataclass(frozen=True)
class ReleaseArtifactContract:
    """可随 release 一起分发的完整性契约。

    契约可选，以兼容历史 release；一旦存在，UOT 会在安装、切换、回滚和
    发布校验时强制验证版本、入口、平台和必需运行时文件。
    """

    app_id: str
    version: str
    platform: str
    entry_path: str
    required_paths: tuple[str, ...] = ()

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "ReleaseArtifactContract":
        """从 JSON 负载读取并校验契约。"""
        extra = sorted(set(payload).difference(_CONTRACT_KEYS))
        if extra:
            raise UpdateError(
                UpdateErrorCode.MANIFEST_INVALID,
                f"release contract contains unsupported keys: {', '.join(extra)}",
            )
        if payload.get("schema_version") != RELEASE_CONTRACT_SCHEMA_VERSION:
            raise UpdateError(
                UpdateErrorCode.MANIFEST_INVALID,
                f"release contract schema_version must be {RELEASE_CONTRACT_SCHEMA_VERSION}",
            )
        required_payload = payload.get("required_paths", [])
        if not isinstance(required_payload, list):
            raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "release contract required_paths must be an array")
        return cls(
            app_id=_require_text(payload.get("app_id"), "release contract app_id"),
            version=_normalize_version(_require_text(payload.get("version"), "release contract version")),
            platform=_require_text(payload.get("platform"), "release contract platform"),
            entry_path=_normalize_relative_path(_require_text(payload.get("entry_path"), "release contract entry_path")),
            required_paths=_normalize_required_paths(required_payload, error_code=UpdateErrorCode.MANIFEST_INVALID),
        )

    def to_payload(self) -> dict[str, object]:
        """转换为稳定 JSON 负载。"""
        return {
            "schema_version": RELEASE_CONTRACT_SCHEMA_VERSION,
            "app_id": self.app_id,
            "version": self.version,
            "platform": self.platform,
            "entry_path": self.entry_path,
            "required_paths": list(self.required_paths),
        }


def write_release_contract(release_dir: Path, contract: ReleaseArtifactContract) -> Path:
    """原子写入 release 自描述完整性契约。"""
    root = Path(release_dir)
    if not root.is_dir():
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"release directory not found: {root}")
    validate_release_artifact(
        release_dir=root,
        version=contract.version,
        entry_path=contract.entry_path,
        app_id=contract.app_id,
        platform=contract.platform,
        required_paths=contract.required_paths,
        read_contract=False,
    )
    target = root / RELEASE_CONTRACT_FILENAME
    temp = target.with_name(f"{target.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        temp.write_text(json.dumps(contract.to_payload(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(target)
    except OSError as exc:
        if temp.exists():
            temp.unlink()
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"cannot write release contract: {target}") from exc
    return target


def read_release_contract(release_dir: Path) -> ReleaseArtifactContract | None:
    """读取可选 release 契约；历史 release 缺失契约时返回 ``None``。"""
    path = Path(release_dir) / RELEASE_CONTRACT_FILENAME
    if not path.exists():
        return None
    if not path.is_file():
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"release contract is not a file: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"cannot read release contract: {path}") from exc
    except json.JSONDecodeError as exc:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"release contract is not valid JSON: {path}") from exc
    if not isinstance(payload, dict):
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, "release contract must be a JSON object")
    return ReleaseArtifactContract.from_payload(payload)


def validate_release_artifact(
    *,
    release_dir: Path,
    version: str,
    entry_path: str,
    app_id: str = "",
    platform: str = "",
    required_paths: tuple[str, ...] | list[str] = (),
    read_contract: bool = True,
) -> ReleaseArtifactContract | None:
    """验证 release 入口、宿主必需资源与可选自描述契约。

    ``required_paths`` 由宿主 bridge 传入，适合在不修改历史包的前提下拒绝
    缺失 settings 或 bridge 的旧 release。新的 release 额外携带契约，以绑定
    app、版本、平台和入口，避免构建版本与 UOT 发布版本分裂。
    """
    root = Path(release_dir)
    normalized_version = _normalize_version(_require_text(version, "release version"))
    normalized_entry = _normalize_relative_path(_require_text(entry_path, "release entry_path"))
    if not root.is_dir():
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"release directory not found: {root}")
    if not _is_entry_path(root / normalized_entry):
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"release entry not found: {root / normalized_entry}")

    caller_required = _normalize_required_paths(required_paths, error_code=UpdateErrorCode.SETTINGS_INVALID)
    contract = read_release_contract(root) if read_contract else None
    contract_required: tuple[str, ...] = ()
    if contract is not None:
        _validate_contract_identity(
            contract,
            version=normalized_version,
            entry_path=normalized_entry,
            app_id=app_id,
            platform=platform,
        )
        contract_required = contract.required_paths
    for required_path in dict.fromkeys((*caller_required, *contract_required)):
        _ensure_required_path(root, required_path)
    return contract


def normalize_release_required_paths(value: object) -> tuple[str, ...]:
    """解析 bridge、pending 或 Agent request 中的必需 release 路径。"""
    if value is None:
        return ()
    if not isinstance(value, (list, tuple)):
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "release_required_paths must be an array")
    return _normalize_required_paths(value, error_code=UpdateErrorCode.SETTINGS_INVALID)


def _validate_contract_identity(
    contract: ReleaseArtifactContract,
    *,
    version: str,
    entry_path: str,
    app_id: str,
    platform: str,
) -> None:
    """校验契约身份与 UOT 当前事务完全一致。"""
    if contract.version != version:
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID,
            f"release contract version {contract.version} does not match release version {version}",
        )
    if contract.entry_path != entry_path:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "release contract entry_path does not match active entry")
    if app_id and contract.app_id != app_id.strip():
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "release contract app_id does not match active application")
    if platform and contract.platform != platform.strip():
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, "release contract platform does not match active platform")


def _ensure_required_path(release_dir: Path, relative_path: str) -> None:
    """确认必需文件未逃逸 release 根目录。"""
    target = release_dir / relative_path
    if not target.exists() or not target.is_file():
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"required release path not found: {target}")
    try:
        resolved_root = release_dir.resolve(strict=True)
        target.resolve(strict=True).relative_to(resolved_root)
    except (OSError, RuntimeError, ValueError) as exc:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"required release path escapes release root: {target}") from exc


def _normalize_required_paths(value: object, *, error_code: UpdateErrorCode) -> tuple[str, ...]:
    """验证并去重必需资源相对路径。"""
    if not isinstance(value, (list, tuple)):
        raise UpdateError(error_code, "release required paths must be an array")
    normalized: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise UpdateError(error_code, "release required paths must contain non-empty strings")
        path = _normalize_relative_path(item.strip(), error_code=error_code)
        if path not in normalized:
            normalized.append(path)
    return tuple(normalized)


def _normalize_relative_path(value: str, *, error_code: UpdateErrorCode = UpdateErrorCode.MANIFEST_INVALID) -> str:
    """将 POSIX 相对路径规范化为跨平台文件系统路径文本。"""
    if not value or "\\" in value:
        raise UpdateError(error_code, "release paths must be non-empty forward-slash relative paths")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise UpdateError(error_code, "release paths must stay inside the release directory")
    return path.as_posix()


def _normalize_version(value: str) -> str:
    """去除可选 v 前缀并保留版本文本。"""
    normalized = str(value).strip()
    return normalized[1:] if normalized.lower().startswith("v") else normalized


def _require_text(value: object, label: str) -> str:
    """读取非空文本。"""
    text = value.strip() if isinstance(value, str) else ""
    if not text:
        raise UpdateError(UpdateErrorCode.MANIFEST_INVALID, f"{label} must be a non-empty string")
    return text


def _is_entry_path(path: Path) -> bool:
    """确认入口是普通文件或可启动的 macOS app bundle。"""
    if path.is_file():
        return True
    if not path.is_dir() or path.suffix != ".app":
        return False
    executable_dir = path / "Contents" / "MacOS"
    return executable_dir.is_dir() and any(candidate.is_file() for candidate in executable_dir.iterdir())
