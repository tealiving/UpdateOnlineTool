"""UpdateOnlineTool 公共 API。"""

from __future__ import annotations

from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.downloader import CancellationToken, PreparedPackage
from update_online_tool.launcher import LaunchResult, StandaloneUpdaterLauncher
from update_online_tool.manifest import UpdateManifest, UpdatePackageInfo
from update_online_tool.nas import NasReleaseSource
from update_online_tool.pyinstaller_assembly import (
    PyInstallerAssemblyConfig,
    PyInstallerAssemblyResult,
    assemble_pyinstaller_release,
    default_pyinstaller_assembly_config,
)
from update_online_tool.service import CheckUpdateResult, UpdateService
from update_online_tool.settings import UPDATE_SETTINGS_FILE_ENV, UpdateToolSettings, resolve_settings_path, user_settings_path
from update_online_tool.versioning import UpdateDecision

__version__ = "0.1.0"

__all__ = [
    "UpdateError",
    "UpdateErrorCode",
    "CancellationToken",
    "PreparedPackage",
    "LaunchResult",
    "StandaloneUpdaterLauncher",
    "UpdateManifest",
    "UpdatePackageInfo",
    "NasReleaseSource",
    "PyInstallerAssemblyConfig",
    "PyInstallerAssemblyResult",
    "assemble_pyinstaller_release",
    "default_pyinstaller_assembly_config",
    "CheckUpdateResult",
    "UpdateService",
    "UpdateToolSettings",
    "UPDATE_SETTINGS_FILE_ENV",
    "resolve_settings_path",
    "user_settings_path",
    "UpdateDecision",
    "__version__",
]
