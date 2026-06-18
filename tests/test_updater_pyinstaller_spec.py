"""uot-updater PyInstaller spec 生成测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from update_online_tool.errors import UpdateError, UpdateErrorCode
from update_online_tool.pyinstaller_assembly import write_updater_pyinstaller_spec


def test_write_updater_pyinstaller_spec_generates_onedir_files(tmp_path: Path) -> None:
    """验证可生成默认 onedir updater spec。"""
    result = write_updater_pyinstaller_spec(output_dir=tmp_path / "updater", name="MyToolUpdater")

    entry_text = result.entry_script.read_text(encoding="utf-8")
    spec_text = result.spec_path.read_text(encoding="utf-8")
    assert result.pyinstaller_command == ["python", "-m", "PyInstaller", "--noconfirm", str(result.spec_path)]
    assert "update_online_tool.updater_cli" in entry_text
    assert "collect_submodules(\"cryptography\")" in spec_text
    assert "COLLECT(" in spec_text
    assert "name='MyToolUpdater'" in spec_text


def test_write_updater_pyinstaller_spec_can_generate_onefile(tmp_path: Path) -> None:
    """验证可生成 onefile spec。"""
    result = write_updater_pyinstaller_spec(
        output_dir=tmp_path / "updater",
        name="MyToolUpdater",
        onefile=True,
        console=False,
    )

    spec_text = result.spec_path.read_text(encoding="utf-8")
    assert "COLLECT(" not in spec_text
    assert "console=False" in spec_text


def test_write_updater_pyinstaller_spec_refuses_overwrite_without_force(tmp_path: Path) -> None:
    """验证默认不覆盖已有 spec 文件。"""
    write_updater_pyinstaller_spec(output_dir=tmp_path / "updater", name="MyToolUpdater")

    with pytest.raises(UpdateError) as error:
        write_updater_pyinstaller_spec(output_dir=tmp_path / "updater", name="MyToolUpdater")

    assert error.value.code is UpdateErrorCode.SETTINGS_INVALID
