"""PyInstaller 发布产物装配。"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from pathlib import Path

from update_online_tool.errors import UpdateError, UpdateErrorCode


@dataclass(frozen=True)
class PyInstallerAssemblyConfig:
    """PyInstaller 装配配置。

    :param version: 发布版本。
    :param app_id: 应用标识。
    :param dist_dir: PyInstaller 输出根目录。
    :param product_name: 最终产品可执行文件名，不含扩展名。
    :param release_dir: GUI release 源目录。
    :param launcher_dir: launcher 源目录。
    :param install_output: 完整安装目录输出路径。
    :param update_output: 升级包目录输出路径。
    :param settings_path: 需要内置到 release 的 settings 路径。
    :param force: 是否覆盖已有输出目录。
    :return: None
    """

    version: str
    app_id: str
    dist_dir: Path
    product_name: str
    release_dir: Path
    launcher_dir: Path
    install_output: Path
    update_output: Path
    settings_path: Path | None = None
    force: bool = False


@dataclass(frozen=True)
class PyInstallerAssemblyResult:
    """PyInstaller 装配结果。

    :param install_root: 完整安装目录。
    :param update_root: 升级包目录。
    :param launcher_executable: 安装根目录稳定入口。
    :param release_executable: release 内 GUI 入口。
    :return: None
    """

    install_root: Path
    update_root: Path
    launcher_executable: Path
    release_executable: Path


def default_pyinstaller_assembly_config(
    *,
    version: str,
    app_id: str = "",
    dist_dir: Path,
    product_name: str,
    settings_path: Path | None = None,
    force: bool = False,
) -> PyInstallerAssemblyConfig:
    """生成默认 PyInstaller 装配配置。

    :param version: 发布版本。
    :param app_id: 应用标识；为空时使用 product_name。
    :param dist_dir: PyInstaller 输出根目录。
    :param product_name: 最终产品可执行文件名，不含扩展名。
    :param settings_path: settings 路径。
    :param force: 是否覆盖已有输出目录。
    :return: 装配配置。
    """
    dist_root = Path(dist_dir)
    normalized_version = _normalize_version(version)
    normalized_product_name = _require_text(product_name, "product_name")
    return PyInstallerAssemblyConfig(
        version=normalized_version,
        app_id=_require_text(app_id or normalized_product_name, "app_id"),
        dist_dir=dist_root,
        product_name=normalized_product_name,
        release_dir=dist_root / f"{normalized_product_name}_release_v{normalized_version}",
        launcher_dir=dist_root / f"{normalized_product_name}_launcher",
        install_output=dist_root / f"{normalized_product_name}_install_v{normalized_version}",
        update_output=dist_root / f"{normalized_product_name}_update_v{normalized_version}",
        settings_path=Path(settings_path) if settings_path is not None else None,
        force=force,
    )


def assemble_pyinstaller_release(config: PyInstallerAssemblyConfig) -> PyInstallerAssemblyResult:
    """装配 PyInstaller GUI release 与稳定 launcher。

    :param config: 装配配置。
    :return: 装配结果。
    """
    desired_exe = f"{config.product_name}.exe"
    _ensure_runtime(config.release_dir)
    _ensure_runtime(config.launcher_dir)
    release_source_exe = _resolve_release_executable(config.release_dir, desired_exe, config.product_name)
    launcher_source_exe = _resolve_launcher_executable(config.launcher_dir, desired_exe, config.product_name)
    _prepare_output_dir(config.install_output, force=config.force)
    _prepare_output_dir(config.update_output, force=config.force)

    install_release_dir = config.install_output / "releases" / config.version
    shutil.copytree(config.release_dir, install_release_dir)
    _normalize_executable(install_release_dir, release_source_exe.name, desired_exe)
    shutil.copytree(config.launcher_dir, config.install_output, dirs_exist_ok=True)
    _normalize_executable(config.install_output, launcher_source_exe.name, desired_exe)
    _write_current_json(config.install_output / "current.json", config.app_id, config.version, desired_exe)
    _copy_settings_if_requested(config.settings_path, install_release_dir)

    shutil.copytree(config.release_dir, config.update_output, dirs_exist_ok=True)
    _normalize_executable(config.update_output, release_source_exe.name, desired_exe)
    update_launcher_dir = config.update_output / "_launcher"
    shutil.copytree(config.launcher_dir, update_launcher_dir)
    _normalize_executable(update_launcher_dir, launcher_source_exe.name, desired_exe)
    _copy_settings_if_requested(config.settings_path, config.update_output)

    return PyInstallerAssemblyResult(
        install_root=config.install_output,
        update_root=config.update_output,
        launcher_executable=config.install_output / desired_exe,
        release_executable=install_release_dir / desired_exe,
    )


def _normalize_version(version: str) -> str:
    """规范化版本号。

    :param version: 原始版本号。
    :return: 不带前缀 v 的版本号。
    """
    value = _require_text(version, "version")
    return value[1:] if value.lower().startswith("v") else value


def _require_text(value: str, field_name: str) -> str:
    """读取非空文本。

    :param value: 原始文本。
    :param field_name: 字段名。
    :return: 去空格后的文本。
    """
    text = str(value or "").strip()
    if not text:
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"{field_name} must be non-empty")
    return text


def _ensure_runtime(bundle_dir: Path) -> None:
    """检查 PyInstaller onedir 运行时目录。

    :param bundle_dir: PyInstaller 输出目录。
    :return: None
    """
    if not bundle_dir.is_dir():
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"PyInstaller bundle not found: {bundle_dir}")
    internal_dir = bundle_dir / "_internal"
    if not internal_dir.is_dir():
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"PyInstaller _internal not found: {internal_dir}")
    if not any(internal_dir.glob("python*.dll")):
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"PyInstaller runtime dll not found: {internal_dir}")


def _resolve_release_executable(bundle_dir: Path, desired_exe: str, product_name: str) -> Path:
    """解析 GUI release 可执行文件。

    :param bundle_dir: release 目录。
    :param desired_exe: 目标 exe 名称。
    :param product_name: 产品名称。
    :return: 源 exe 路径。
    """
    candidates = [
        bundle_dir / desired_exe,
        bundle_dir / f"{product_name}Gui.exe",
    ]
    return _first_existing(candidates, bundle_dir.glob("*Gui.exe"), f"release executable not found in {bundle_dir}")


def _resolve_launcher_executable(bundle_dir: Path, desired_exe: str, product_name: str) -> Path:
    """解析 launcher 可执行文件。

    :param bundle_dir: launcher 目录。
    :param desired_exe: 目标 exe 名称。
    :param product_name: 产品名称。
    :return: 源 exe 路径。
    """
    candidates = [
        bundle_dir / desired_exe,
        bundle_dir / f"{product_name}Launcher.exe",
        bundle_dir / "AutomationManualLauncher.exe",
    ]
    return _first_existing(candidates, bundle_dir.glob("*Launcher.exe"), f"launcher executable not found in {bundle_dir}")


def _first_existing(candidates: list[Path], fallback: object, message: str) -> Path:
    """获取第一个存在的候选文件。

    :param candidates: 固定候选路径。
    :param fallback: 额外候选迭代器。
    :param message: 未找到时的错误消息。
    :return: 候选文件。
    """
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    for candidate in fallback:  # type: ignore[assignment]
        if Path(candidate).is_file():
            return Path(candidate)
    raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, message)


def _prepare_output_dir(path: Path, *, force: bool) -> None:
    """准备输出目录。

    :param path: 输出目录。
    :param force: 是否覆盖已有目录。
    :return: None
    """
    if path.exists():
        if not force:
            raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"output already exists: {path}")
        shutil.rmtree(path)
    path.mkdir(parents=True)


def _normalize_executable(root: Path, source_name: str, desired_name: str) -> None:
    """把内部构建 exe 名称归一化为产品入口名称。

    :param root: 归一化目录。
    :param source_name: 当前 exe 名称。
    :param desired_name: 目标 exe 名称。
    :return: None
    """
    source_path = root / source_name
    desired_path = root / desired_name
    if not source_path.is_file():
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"executable not found: {source_path}")
    if source_path == desired_path:
        return
    if desired_path.exists():
        desired_path.unlink()
    source_path.rename(desired_path)


def _write_current_json(path: Path, app_id: str, version: str, desired_exe: str) -> None:
    """写入标准 current.json。

    :param path: current.json 路径。
    :param app_id: 应用标识。
    :param version: 当前版本。
    :param desired_exe: release GUI exe 名称。
    :return: None
    """
    payload = {
        "app_id": app_id,
        "version": version,
        "release_dir": f"releases/{version}",
        "executable": desired_exe,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _copy_settings_if_requested(settings_path: Path | None, bundle_root: Path) -> None:
    """按需复制 settings 到 PyInstaller 内置配置目录。

    :param settings_path: settings 源路径。
    :param bundle_root: PyInstaller bundle 根目录。
    :return: None
    """
    if settings_path is None:
        return
    if not settings_path.is_file():
        raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, f"settings not found: {settings_path}")
    target = bundle_root / "_internal" / "config" / "settings.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(settings_path, target)
