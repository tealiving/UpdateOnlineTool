"""设置解析测试。"""

from __future__ import annotations

import json
from pathlib import Path

from update_online_tool.settings import UpdateToolSettings


def test_settings_loads_nas_root(tmp_path: Path) -> None:
    """验证 settings.json 可解析 NAS 根路径。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "nas": {"root": str(tmp_path / "nas")},
                "publish": {
                    "default_channel": "stable",
                    "default_minimum_version": "1.0.0",
                    "package_filename": "package.zip",
                },
                "updater": {"executable_name": "AutomationManualUpdater.exe"},
            }
        ),
        encoding="utf-8",
    )

    settings = UpdateToolSettings.load(settings_path)

    assert settings.nas_root == tmp_path / "nas"
    assert settings.default_channel == "stable"
    assert settings.package_filename == "package.zip"
