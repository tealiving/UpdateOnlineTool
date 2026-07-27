"""PyInstaller 发布产物装配。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.release_assembly import (
    ReleaseAssemblyAsset,
    ReleaseAssemblyConfig,
    assemble_release_layout,
)
from update_online_tool.release_identity import validate_release_platform


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
    :param platform: 目标平台：windows、macos 或 linux。
    :param entry_name: 标准入口文件名。
    :param release_entry_name: release 源入口文件名。
    :param launcher_entry_name: launcher 源入口文件名。
    :param settings_path: 需要内置到 release 的 settings 路径。
    :param updater_bundle: 已构建的 uot-updater onefile 或 onedir 产物。
    :param updater_name: 复制到 install_root/updater/ 下的目标名称。
    :param bootstrap_agent_mode: 是否使用稳定 Bootstrap/Agent 运行时布局。
    :param agent_bundle: 已构建的 uot-agent 产物；稳定模式必填。
    :param agent_name: 复制到 install_root/agent/ 下的目标名称。
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
    platform: str = "windows"
    entry_name: str = ""
    release_entry_name: str = ""
    launcher_entry_name: str = ""
    settings_path: Path | None = None
    updater_bundle: Path | None = None
    updater_name: str = ""
    bootstrap_agent_mode: bool = False
    agent_bundle: Path | None = None
    agent_name: str = ""
    force: bool = False


@dataclass(frozen=True)
class PyInstallerAssemblyResult:
    """PyInstaller 装配结果。

    :param install_root: 完整安装目录。
    :param update_root: 升级包目录。
    :param launcher_executable: 安装根目录稳定入口。
    :param release_executable: release 内 GUI 入口。
    :param platform: 装配目标平台。
    :return: None
    """

    install_root: Path
    update_root: Path
    launcher_executable: Path
    release_executable: Path
    platform: str
    updater_path: Path | None = None
    agent_path: Path | None = None


@dataclass(frozen=True)
class UpdaterPyInstallerSpecResult:
    """标准 updater PyInstaller spec 生成结果。"""

    output_dir: Path
    spec_path: Path
    entry_script: Path
    pyinstaller_command: list[str]

    def to_payload(self) -> dict[str, object]:
        """转换为 JSON 负载。"""
        return {
            "output_dir": str(self.output_dir),
            "spec_path": str(self.spec_path),
            "entry_script": str(self.entry_script),
            "pyinstaller_command": self.pyinstaller_command,
        }


def default_pyinstaller_assembly_config(
    *,
    version: str,
    app_id: str = "",
    dist_dir: Path,
    product_name: str,
    platform: str = "windows",
    entry_name: str = "",
    release_entry_name: str = "",
    launcher_entry_name: str = "",
    settings_path: Path | None = None,
    updater_bundle: Path | None = None,
    updater_name: str = "",
    bootstrap_agent_mode: bool = False,
    agent_bundle: Path | None = None,
    agent_name: str = "",
    force: bool = False,
) -> PyInstallerAssemblyConfig:
    """生成默认 PyInstaller 装配配置。

    :param version: 发布版本。
    :param app_id: 应用标识；为空时使用 product_name。
    :param dist_dir: PyInstaller 输出根目录。
    :param product_name: 最终产品可执行文件名，不含扩展名。
    :param platform: 目标平台。
    :param entry_name: 标准入口文件名；为空时按平台生成。
    :param release_entry_name: release 源入口文件名；为空时自动查找。
    :param launcher_entry_name: launcher 源入口文件名；为空时自动查找。
    :param settings_path: settings 路径。
    :param updater_bundle: 标准 updater PyInstaller 产物路径。
    :param updater_name: updater 复制后的名称。
    :param bootstrap_agent_mode: 是否使用稳定 Bootstrap/Agent 运行时布局。
    :param agent_bundle: 标准 Agent 产物路径。
    :param agent_name: Agent 复制后的名称。
    :param force: 是否覆盖已有输出目录。
    :return: 装配配置。
    """
    dist_root = Path(dist_dir)
    normalized_version = _normalize_version(version)
    normalized_product_name = _require_text(product_name, "product_name")
    normalized_platform = _normalize_platform(platform)
    return PyInstallerAssemblyConfig(
        version=normalized_version,
        app_id=_require_text(app_id or normalized_product_name, "app_id"),
        dist_dir=dist_root,
        product_name=normalized_product_name,
        release_dir=dist_root
        / f"{normalized_product_name}_release_v{normalized_version}",
        launcher_dir=dist_root / f"{normalized_product_name}_launcher",
        install_output=dist_root
        / f"{normalized_product_name}_install_v{normalized_version}",
        update_output=dist_root
        / f"{normalized_product_name}_update_v{normalized_version}",
        platform=normalized_platform,
        entry_name=_optional_text(entry_name),
        release_entry_name=_optional_text(release_entry_name),
        launcher_entry_name=_optional_text(launcher_entry_name),
        settings_path=Path(settings_path) if settings_path is not None else None,
        updater_bundle=Path(updater_bundle) if updater_bundle is not None else None,
        updater_name=_optional_text(updater_name),
        bootstrap_agent_mode=bool(bootstrap_agent_mode),
        agent_bundle=Path(agent_bundle) if agent_bundle is not None else None,
        agent_name=_optional_text(agent_name),
        force=force,
    )


def write_updater_pyinstaller_spec(
    *,
    output_dir: Path,
    name: str = "uot-updater",
    onefile: bool = False,
    console: bool = True,
    force: bool = False,
) -> UpdaterPyInstallerSpecResult:
    """生成标准 uot-updater PyInstaller 入口脚本和 spec。

    :param output_dir: 输出目录。
    :param name: PyInstaller 产物名称。
    :param onefile: 是否生成 onefile spec；默认 onedir。
    :param console: 是否保留 console。
    :param force: 是否覆盖已有文件。
    :return: spec 生成结果。
    """
    return _write_runtime_pyinstaller_spec(
        output_dir=output_dir,
        name=name,
        entry_filename="uot_updater_entry.py",
        entry_module="update_online_tool.updater_cli",
        onefile=onefile,
        console=console,
        force=force,
    )


def write_agent_pyinstaller_spec(
    *,
    output_dir: Path,
    name: str = "uot-agent",
    onefile: bool = False,
    console: bool = True,
    force: bool = False,
) -> UpdaterPyInstallerSpecResult:
    """生成独立 Update Agent 的 PyInstaller 入口脚本和 spec。"""
    return _write_runtime_pyinstaller_spec(
        output_dir=output_dir,
        name=name,
        entry_filename="uot_agent_entry.py",
        entry_module="update_online_tool.agent_cli",
        onefile=onefile,
        console=console,
        force=force,
    )


def write_bootstrap_pyinstaller_spec(
    *,
    output_dir: Path,
    name: str = "uot-bootstrap",
    onefile: bool = False,
    console: bool = True,
    force: bool = False,
) -> UpdaterPyInstallerSpecResult:
    """生成稳定 Bootstrap 的 PyInstaller 入口脚本和 spec。"""
    return _write_runtime_pyinstaller_spec(
        output_dir=output_dir,
        name=name,
        entry_filename="uot_bootstrap_entry.py",
        entry_module="update_online_tool.bootstrap_cli",
        onefile=onefile,
        console=console,
        force=force,
    )


def write_bridge_pyinstaller_spec(
    *,
    output_dir: Path,
    name: str = "uot-bridge",
    onefile: bool = False,
    console: bool = True,
    force: bool = False,
) -> UpdaterPyInstallerSpecResult:
    """生成供 Electron/Tauri 调用的独立 JSON bridge 打包入口。"""
    return _write_runtime_pyinstaller_spec(
        output_dir=output_dir,
        name=name,
        entry_filename="uot_bridge_entry.py",
        entry_module="update_online_tool.bridge_cli",
        onefile=onefile,
        console=console,
        force=force,
    )


def _write_runtime_pyinstaller_spec(
    *,
    output_dir: Path,
    name: str,
    entry_filename: str,
    entry_module: str,
    onefile: bool,
    console: bool,
    force: bool,
) -> UpdaterPyInstallerSpecResult:
    """生成一个 UOT 稳定运行时的可打包入口和 spec。"""
    root = Path(output_dir)
    product_name = _require_text(name, "name")
    root.mkdir(parents=True, exist_ok=True)
    entry_script = root / entry_filename
    spec_path = root / f"{product_name}.spec"
    for path in (entry_script, spec_path):
        if path.exists() and not force:
            raise UpdateError(
                UpdateErrorCode.SETTINGS_INVALID, f"output already exists: {path}"
            )
    entry_script.write_text(_runtime_entry_script(entry_module), encoding="utf-8")
    spec_path.write_text(
        _runtime_spec_text(
            entry_script=entry_script,
            spec_dir=root,
            name=product_name,
            entry_module=entry_module,
            onefile=onefile,
            console=console,
        ),
        encoding="utf-8",
    )
    return UpdaterPyInstallerSpecResult(
        output_dir=root,
        spec_path=spec_path,
        entry_script=entry_script,
        pyinstaller_command=[
            "python",
            "-m",
            "PyInstaller",
            "--noconfirm",
            str(spec_path),
        ],
    )


def assemble_pyinstaller_release(
    config: PyInstallerAssemblyConfig,
) -> PyInstallerAssemblyResult:
    """装配 PyInstaller GUI release 与稳定 launcher。

    :param config: 装配配置。
    :return: 装配结果。
    """
    platform = _normalize_platform(config.platform)
    desired_entry = config.entry_name or _default_entry_name(
        config.product_name, platform
    )
    _ensure_runtime(config.release_dir, platform)
    _ensure_runtime(config.launcher_dir, platform)
    release_source_exe = _resolve_release_executable(
        config.release_dir,
        desired_entry,
        config.product_name,
        platform,
        config.release_entry_name,
    )
    launcher_source_exe = _resolve_launcher_executable(
        config.launcher_dir,
        desired_entry,
        config.product_name,
        platform,
        config.launcher_entry_name,
    )
    assets = _pyinstaller_assets(
        settings_path=config.settings_path,
        release_entry=release_source_exe,
        desired_entry=desired_entry,
        platform=platform,
    )
    assembled = assemble_release_layout(
        ReleaseAssemblyConfig(
            version=config.version,
            app_id=config.app_id,
            release_dir=config.release_dir,
            launcher_dir=config.launcher_dir,
            install_output=config.install_output,
            update_output=config.update_output,
            platform=platform,
            entry_path=desired_entry,
            release_entry_path=release_source_exe.name,
            launcher_entry_path=launcher_source_exe.name,
            updater_bundle=config.updater_bundle,
            updater_name=config.updater_name,
            bootstrap_agent_mode=config.bootstrap_agent_mode,
            agent_bundle=config.agent_bundle,
            agent_name=config.agent_name,
            assets=assets,
            force=config.force,
        )
    )

    return PyInstallerAssemblyResult(
        install_root=assembled.install_root,
        update_root=assembled.update_root,
        launcher_executable=assembled.launcher_entry,
        release_executable=assembled.release_entry,
        platform=platform,
        updater_path=assembled.updater_path,
        agent_path=assembled.agent_path,
    )


def _pyinstaller_assets(
    *,
    settings_path: Path | None,
    release_entry: Path,
    desired_entry: str,
    platform: str,
) -> tuple[ReleaseAssemblyAsset, ...]:
    """将 PyInstaller 内置 settings 映射为通用 release 资源。"""
    if settings_path is None:
        return ()
    if platform == "macos" and release_entry.suffix == ".app":
        target = f"{desired_entry}/Contents/Resources/config/settings.json"
    else:
        target = "_internal/config/settings.json"
    return (ReleaseAssemblyAsset(Path(settings_path), target),)


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
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID, f"{field_name} must be non-empty"
        )
    return text


def _optional_text(value: str) -> str:
    """读取可选文本。

    :param value: 原始文本。
    :return: 去空格后的文本，空值返回空字符串。
    """
    return str(value or "").strip()


def _normalize_platform(platform: str) -> str:
    """规范化目标平台。

    :param platform: 原始平台名。
    :return: windows、macos 或 linux。
    """
    return validate_release_platform(
        platform or "windows",
        allow_aliases=True,
    )


def _default_entry_name(product_name: str, platform: str) -> str:
    """生成平台默认入口文件名。

    :param product_name: 产品名。
    :param platform: 目标平台。
    :return: 入口文件名。
    """
    if platform == "windows":
        return f"{product_name}.exe"
    return product_name


def _platform_suffix(platform: str) -> str:
    """返回平台可执行文件后缀。"""
    return ".exe" if platform == "windows" else ""


def _ensure_runtime(bundle_dir: Path, platform: str) -> None:
    """检查 PyInstaller onedir 运行时目录。

    :param bundle_dir: PyInstaller 输出目录。
    :param platform: 目标平台。
    :return: None
    """
    if not bundle_dir.is_dir():
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID,
            f"PyInstaller bundle not found: {bundle_dir}",
        )
    if platform == "macos" and _has_macos_app_bundle(bundle_dir):
        return
    internal_dir = bundle_dir / "_internal"
    if not internal_dir.is_dir():
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID,
            f"PyInstaller _internal not found: {internal_dir}",
        )
    if platform == "windows" and not any(internal_dir.glob("python*.dll")):
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID,
            f"PyInstaller runtime dll not found: {internal_dir}",
        )
    if platform != "windows" and not any(internal_dir.iterdir()):
        raise UpdateError(
            UpdateErrorCode.SETTINGS_INVALID,
            f"PyInstaller runtime files not found: {internal_dir}",
        )


def _resolve_release_executable(
    bundle_dir: Path,
    desired_entry: str,
    product_name: str,
    platform: str,
    explicit_entry: str = "",
) -> Path:
    """解析 GUI release 可执行文件。

    :param bundle_dir: release 目录。
    :param desired_entry: 目标入口名称。
    :param product_name: 产品名称。
    :param platform: 目标平台。
    :param explicit_entry: 显式 release 源入口名称。
    :return: 源 exe 路径。
    """
    suffix = _platform_suffix(platform)
    candidates = [
        bundle_dir / explicit_entry if explicit_entry else None,
        bundle_dir / desired_entry,
        bundle_dir / f"{product_name}Gui.app" if platform == "macos" else None,
        bundle_dir / f"{product_name}.app" if platform == "macos" else None,
        bundle_dir / f"{product_name}Gui{suffix}",
        bundle_dir / f"{product_name}{suffix}",
    ]
    return _first_existing(
        candidates,
        _entry_fallbacks(bundle_dir, "Gui", suffix),
        f"release executable not found in {bundle_dir}",
    )


def _resolve_launcher_executable(
    bundle_dir: Path,
    desired_entry: str,
    product_name: str,
    platform: str,
    explicit_entry: str = "",
) -> Path:
    """解析 launcher 可执行文件。

    :param bundle_dir: launcher 目录。
    :param desired_entry: 目标入口名称。
    :param product_name: 产品名称。
    :param platform: 目标平台。
    :param explicit_entry: 显式 launcher 源入口名称。
    :return: 源 exe 路径。
    """
    suffix = _platform_suffix(platform)
    candidates = [
        bundle_dir / explicit_entry if explicit_entry else None,
        bundle_dir / desired_entry,
        bundle_dir / f"{product_name}Launcher.app" if platform == "macos" else None,
        bundle_dir / f"{product_name}.app" if platform == "macos" else None,
        bundle_dir / f"{product_name}Launcher{suffix}",
    ]
    return _first_existing(
        candidates,
        _entry_fallbacks(bundle_dir, "Launcher", suffix),
        f"launcher executable not found in {bundle_dir}",
    )


def _entry_fallbacks(bundle_dir: Path, marker: str, suffix: str) -> list[Path]:
    """生成入口候选兜底列表。"""
    candidates = sorted(
        path for path in bundle_dir.glob(f"*{marker}{suffix}") if _is_entry_path(path)
    )
    if suffix == "":
        candidates.extend(
            sorted(
                path
                for path in bundle_dir.glob(f"*{marker}.app")
                if _is_entry_path(path)
            )
        )
    return candidates


def _first_existing(
    candidates: list[Path | None], fallback: object, message: str
) -> Path:
    """获取第一个存在的候选文件。

    :param candidates: 固定候选路径。
    :param fallback: 额外候选迭代器。
    :param message: 未找到时的错误消息。
    :return: 候选文件。
    """
    for candidate in candidates:
        if candidate is None:
            continue
        if _is_entry_path(candidate):
            return candidate
    for candidate in fallback:  # type: ignore[assignment]
        if _is_entry_path(Path(candidate)):
            return Path(candidate)
    raise UpdateError(UpdateErrorCode.SETTINGS_INVALID, message)


def _has_macos_app_bundle(bundle_dir: Path) -> bool:
    """判断目录是否包含 macOS .app bundle。"""
    if _is_macos_app_bundle(bundle_dir):
        return True
    return any(_is_macos_app_bundle(path) for path in bundle_dir.glob("*.app"))


def _is_entry_path(path: Path) -> bool:
    """判断路径是否可作为入口。"""
    return path.is_file() or _is_macos_app_bundle(path)


def _is_macos_app_bundle(path: Path) -> bool:
    """判断路径是否为 macOS .app bundle。"""
    if not path.is_dir() or path.suffix != ".app":
        return False
    macos_dir = path / "Contents" / "MacOS"
    return macos_dir.is_dir() and any(
        candidate.is_file() for candidate in macos_dir.iterdir()
    )


def _runtime_entry_script(entry_module: str) -> str:
    """生成 UOT 稳定运行时 PyInstaller 入口脚本。"""
    return (
        f"from {entry_module} import main\n\n"
        "\n"
        'if __name__ == "__main__":\n'
        "    raise SystemExit(main())\n"
    )


def _runtime_spec_text(
    *,
    entry_script: Path,
    spec_dir: Path,
    name: str,
    entry_module: str,
    onefile: bool,
    console: bool,
) -> str:
    """生成 UOT 稳定运行时 PyInstaller spec 文本。"""
    exe_block = (
        _updater_onefile_exe_block(name=name, console=console)
        if onefile
        else _updater_onedir_exe_block(
            name=name,
            console=console,
        )
    )
    return f"""# -*- mode: python ; coding: utf-8 -*-

hiddenimports = [
    {entry_module!r},
    "update_online_tool.signature",
    "cryptography.hazmat.primitives.asymmetric.ed25519",
    "cryptography.hazmat.primitives.serialization",
]

a = Analysis(
    [{entry_script.name!r}],
    pathex=[{str(spec_dir)!r}],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={{}},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
{exe_block}"""


def _updater_onefile_exe_block(*, name: str, console: bool) -> str:
    """生成 onefile EXE block。"""
    return f"""exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name={name!r},
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console={console!r},
)
"""


def _updater_onedir_exe_block(*, name: str, console: bool) -> str:
    """生成 onedir EXE/COLLECT block。"""
    return f"""exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name={name!r},
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console={console!r},
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    name={name!r},
)
"""
