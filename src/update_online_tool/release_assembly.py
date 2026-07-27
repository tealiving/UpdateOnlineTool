"""框架无关的桌面应用 release 装配。"""

from __future__ import annotations

import json
import os
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from uuid import uuid4

from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.release_contract import (
    ReleaseArtifactContract,
    read_release_contract,
    validate_release_artifact,
    write_release_contract,
)
from update_online_tool.release_identity import validate_release_platform
from update_online_tool.release_package import ReleasePackagePlan


@dataclass(frozen=True)
class ReleaseAssemblyAsset:
    """需要复制到 release 根目录的附加文件。"""

    source_path: Path
    target_path: str


@dataclass(frozen=True)
class ReleaseAssemblyConfig:
    """框架无关 release 装配配置。"""

    version: str
    app_id: str
    release_dir: Path
    launcher_dir: Path
    install_output: Path
    update_output: Path
    platform: str
    entry_path: str
    release_entry_path: str
    launcher_entry_path: str
    updater_bundle: Path | None = None
    updater_name: str = ""
    bootstrap_agent_mode: bool = False
    agent_bundle: Path | None = None
    agent_name: str = ""
    assets: tuple[ReleaseAssemblyAsset, ...] = ()
    force: bool = False


@dataclass(frozen=True)
class ReleaseAssemblyResult:
    """框架无关 release 装配结果。"""

    install_root: Path
    update_root: Path
    release_dir: Path
    launcher_entry: Path
    release_entry: Path
    platform: str
    updater_path: Path | None = None
    agent_path: Path | None = None


def create_release_package(
    source_dir: Path, output_path: Path, *, force: bool = False
) -> Path:
    """创建可由 UOT 安全解包的 release zip。

    使用 Python ``zipfile`` 写入 UTF-8 文件名，避免 macOS ``zip`` 在中文 `.app`
    路径上产生 CP437 乱码；同时排除 AppleDouble 和 Finder 元数据。
    """
    source = Path(source_dir)
    output = Path(output_path)
    _ensure_directory(source, "release package source directory")
    if output.exists() and not force:
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID,
            f"release package already exists: {output}",
        )
    if _paths_overlap(source.resolve(), output.resolve()):
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID, "release package output overlaps source"
        )
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        with zipfile.ZipFile(
            temporary, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as archive:
            for path in sorted(source.rglob("*")):
                if _is_packaging_metadata(path):
                    continue
                archive_name = path.relative_to(source).as_posix()
                if path.is_symlink():
                    member = zipfile.ZipInfo(archive_name)
                    member.create_system = 3
                    member.compress_type = zipfile.ZIP_DEFLATED
                    member.external_attr = (path.lstat().st_mode & 0xFFFF) << 16
                    archive.writestr(member, os.readlink(path).encode("utf-8"))
                    continue
                if path.is_file():
                    archive.write(path, archive_name)
        ReleasePackagePlan.from_zip(temporary)
        temporary.replace(output)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise
    return output


def assemble_release_layout(config: ReleaseAssemblyConfig) -> ReleaseAssemblyResult:
    """装配标准 UOT 安装根和升级目录。

    输入 release 与 launcher 只需要满足目录和入口契约，因此可由 PyInstaller、
    Electron、Tauri 或其他桌面构建工具提供。
    """
    version = _normalize_version(config.version)
    app_id = _require_text(config.app_id, "app_id")
    platform = _normalize_platform(config.platform)
    entry_path = _relative_path(config.entry_path, "entry_path")
    release_entry_path = _relative_path(config.release_entry_path, "release_entry_path")
    launcher_entry_path = _relative_path(
        config.launcher_entry_path, "launcher_entry_path"
    )
    release_dir = Path(config.release_dir)
    launcher_dir = Path(config.launcher_dir)
    _ensure_directory(release_dir, "release directory")
    _ensure_directory(launcher_dir, "launcher directory")
    if not _is_entry_path(release_dir / release_entry_path):
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID,
            f"release entry not found: {release_dir / release_entry_path}",
        )
    if not _is_entry_path(launcher_dir / launcher_entry_path):
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID,
            f"launcher entry not found: {launcher_dir / launcher_entry_path}",
        )
    if config.bootstrap_agent_mode and config.agent_bundle is None:
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID,
            "bootstrap_agent_mode requires agent_bundle",
        )
    if config.bootstrap_agent_mode and config.updater_bundle is not None:
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID,
            "bootstrap_agent_mode does not support updater_bundle",
        )
    source_contract = read_release_contract(release_dir)
    if source_contract is not None:
        validate_release_artifact(
            release_dir=release_dir,
            version=version,
            entry_path=entry_path.as_posix(),
            app_id=app_id,
            platform=platform,
        )

    install_root = Path(config.install_output)
    update_root = Path(config.update_output)
    _ensure_output_paths_are_disjoint(
        install_root=install_root,
        update_root=update_root,
        source_paths=(
            release_dir,
            launcher_dir,
            *(() if config.updater_bundle is None else (Path(config.updater_bundle),)),
            *(() if config.agent_bundle is None else (Path(config.agent_bundle),)),
            *(Path(asset.source_path) for asset in config.assets),
        ),
    )
    _prepare_output_dir(install_root, force=config.force)
    _prepare_output_dir(update_root, force=config.force)

    install_release_dir = install_root / "releases" / version
    shutil.copytree(release_dir, install_release_dir, symlinks=True)
    _rename_entry(
        install_release_dir, source_path=release_entry_path, target_path=entry_path
    )
    shutil.copytree(launcher_dir, install_root, dirs_exist_ok=True, symlinks=True)
    # Bootstrap/Agent 模式的稳定入口不能伪装成 release 入口。尤其在 macOS，
    # current.json 必须继续指向 releases/<version>/<Product>.app，而安装根只
    # 保留独立的 uot-bootstrap。legacy launcher 模式仍保留原来的同名入口布局。
    if config.bootstrap_agent_mode:
        launcher_entry = install_root / launcher_entry_path
    else:
        _rename_entry(
            install_root, source_path=launcher_entry_path, target_path=entry_path
        )
        launcher_entry = install_root / entry_path
    _write_current_json(
        install_root / "current.json",
        app_id=app_id,
        version=version,
        entry_path=entry_path,
        platform=platform,
    )
    _copy_assets(config.assets, install_release_dir)
    contract = source_contract or ReleaseArtifactContract(
        app_id=app_id,
        version=version,
        platform=platform,
        entry_path=entry_path.as_posix(),
        required_paths=tuple(
            _asset_target_path(asset.target_path).as_posix() for asset in config.assets
        ),
    )
    write_release_contract(install_release_dir, contract)
    updater_path = None
    agent_path = None
    if config.bootstrap_agent_mode:
        agent_path = _copy_bundle(
            config.agent_bundle, install_root / "agent", config.agent_name
        )
    else:
        updater_path = _copy_bundle(
            config.updater_bundle, install_root / "updater", config.updater_name
        )

    shutil.copytree(release_dir, update_root, dirs_exist_ok=True, symlinks=True)
    _rename_entry(update_root, source_path=release_entry_path, target_path=entry_path)
    _copy_assets(config.assets, update_root)
    write_release_contract(update_root, contract)
    if not config.bootstrap_agent_mode:
        update_launcher_dir = update_root / "_launcher"
        shutil.copytree(launcher_dir, update_launcher_dir, symlinks=True)
        _rename_entry(
            update_launcher_dir, source_path=launcher_entry_path, target_path=entry_path
        )
        _copy_bundle(
            config.updater_bundle, update_root / "updater", config.updater_name
        )

    return ReleaseAssemblyResult(
        install_root=install_root,
        update_root=update_root,
        release_dir=install_release_dir,
        launcher_entry=launcher_entry,
        release_entry=install_release_dir / entry_path,
        platform=platform,
        updater_path=updater_path,
        agent_path=agent_path,
    )


def _normalize_version(version: str) -> str:
    """规范化版本号。"""
    value = _require_text(version, "version")
    return value[1:] if value.lower().startswith("v") else value


def _normalize_platform(platform: str) -> str:
    """规范化目标平台。"""
    return validate_release_platform(
        platform or "windows",
        allow_aliases=True,
    )


def _require_text(value: str, field_name: str) -> str:
    """返回非空文本。"""
    text = str(value or "").strip()
    if not text:
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID, f"{field_name} must be non-empty"
        )
    return text


def _relative_path(value: str, field_name: str) -> Path:
    """校验并返回安全的 POSIX 相对路径。"""
    text = _require_text(value, field_name)
    if "\\" in text:
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID, f"{field_name} must use forward slashes"
        )
    candidate = PurePosixPath(text)
    if candidate.is_absolute() or any(
        part in {"", ".", ".."} for part in candidate.parts
    ):
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID, f"{field_name} must be relative"
        )
    return Path(*candidate.parts)


def _ensure_directory(path: Path, label: str) -> None:
    """确认构建产物目录存在。"""
    if not path.is_dir():
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID, f"{label} not found: {path}"
        )


def _ensure_output_paths_are_disjoint(
    *,
    install_root: Path,
    update_root: Path,
    source_paths: tuple[Path, ...],
) -> None:
    """拒绝会覆盖构建输入或彼此嵌套的输出目录。"""
    output_paths = (install_root.resolve(), update_root.resolve())
    if _paths_overlap(*output_paths):
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID, "assembly outputs must not overlap"
        )
    for output_path in output_paths:
        for source_path in source_paths:
            if _paths_overlap(output_path, source_path.resolve()):
                raise UpdateError(
                    UpdateErrorCode.SETTINGS_INVALID, "assembly output overlaps source"
                )


def _paths_overlap(first: Path, second: Path) -> bool:
    """判断两个路径是否相等或存在包含关系。"""
    return first == second or first in second.parents or second in first.parents


def _prepare_output_dir(path: Path, *, force: bool) -> None:
    """准备输出目录。"""
    if path.exists():
        if not force:
            raise UpdateError(
                UpdateErrorCode.SETTINGS_INVALID, f"output already exists: {path}"
            )
        _remove_output_dir(path)
    path.mkdir(parents=True)


def _remove_output_dir(path: Path) -> None:
    """删除重建目录，容忍外置盘 AppleDouble 文件在遍历期间消失。"""

    def ignore_vanished_metadata(
        _function: object, target: str, error_info: tuple[object, object, object]
    ) -> None:
        error = error_info[1]
        if isinstance(error, FileNotFoundError):
            return
        raise error

    try:
        shutil.rmtree(path, onerror=ignore_vanished_metadata)
    except FileNotFoundError:
        return


def _rename_entry(root: Path, *, source_path: Path, target_path: Path) -> None:
    """将构建入口归一化为 UOT 约定入口。"""
    source = root / source_path
    target = root / target_path
    if not _is_entry_path(source):
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID, f"entry not found: {source}"
        )
    if source == target:
        return
    if target.exists():
        if target.is_dir():
            shutil.rmtree(target)
        else:
            target.unlink()
    target.parent.mkdir(parents=True, exist_ok=True)
    source.rename(target)


def _copy_assets(assets: tuple[ReleaseAssemblyAsset, ...], release_root: Path) -> None:
    """复制 release 附加文件。"""
    for asset in assets:
        source = Path(asset.source_path)
        if not source.is_file():
            raise UpdateError(
                UpdateErrorCode.SETTINGS_INVALID, f"release asset not found: {source}"
            )
        target = release_root / _asset_target_path(asset.target_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def _asset_target_path(value: str) -> Path:
    """校验附加资源目标路径。"""
    try:
        return _relative_path(value, "asset target path")
    except UpdateError as exc:
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID, "asset target path must be relative"
        ) from exc


def _copy_bundle(
    bundle: Path | None, target_root: Path, target_name: str
) -> Path | None:
    """复制独立 updater 或 Agent bundle 到稳定安装根。"""
    if bundle is None:
        return None
    source = Path(bundle)
    if not source.exists():
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID, f"runtime bundle not found: {source}"
        )
    target_root.mkdir(parents=True, exist_ok=True)
    target = target_root / (str(target_name or "").strip() or source.name)
    if source.is_dir():
        shutil.copytree(source, target, symlinks=True)
    else:
        shutil.copy2(source, target)
    return target


def _write_current_json(
    path: Path, *, app_id: str, version: str, entry_path: Path, platform: str
) -> None:
    """原子写入安装根 current.json。"""
    entry = entry_path.as_posix()
    payload = {
        "app_id": app_id,
        "version": version,
        "release_dir": f"releases/{version}",
        "executable": entry,
        "entry": {
            "kind": "app_bundle" if entry.endswith(".app") else "executable",
            "path": entry,
            "platform": platform,
        },
    }
    temporary = path.with_name(f"{path.name}.{os.getpid()}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
    except Exception:
        if temporary.exists():
            temporary.unlink()
        raise


def _is_entry_path(path: Path) -> bool:
    """判断路径是否可作为 release 或 launcher 入口。"""
    return path.is_file() or _is_macos_app_bundle(path)


def _is_packaging_metadata(path: Path) -> bool:
    """过滤 macOS/ExFAT 自动生成且不属于应用 release 的文件。"""
    return path.name == ".DS_Store" or path.name.startswith("._")


def _is_macos_app_bundle(path: Path) -> bool:
    """判断路径是否为可启动 macOS app bundle。"""
    if not path.is_dir() or path.suffix != ".app":
        return False
    macos_dir = path / "Contents" / "MacOS"
    return macos_dir.is_dir() and any(
        candidate.is_file() for candidate in macos_dir.iterdir()
    )
