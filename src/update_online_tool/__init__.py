"""UpdateOnlineTool 公共 API。"""

from __future__ import annotations

from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.downloader import CancellationToken, PreparedPackage
from update_online_tool.launcher import LaunchResult, StandaloneUpdaterLauncher
from update_online_tool.manifest import UpdateManifest, UpdatePackageInfo
from update_online_tool.nas import NasReleaseSource
from update_online_tool.service import CheckUpdateResult, UpdateService
from update_online_tool.settings import UpdateToolSettings
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
    "CheckUpdateResult",
    "UpdateService",
    "UpdateToolSettings",
    "UpdateDecision",
    "__version__",
]
