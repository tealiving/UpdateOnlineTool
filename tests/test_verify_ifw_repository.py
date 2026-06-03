"""UpdateOnlineTool Qt IFW repository 校验脚本测试。"""

from __future__ import annotations

from pathlib import Path

from _scripts.verify_ifw_repository import verify_repository


def test_verify_repository_accepts_core_package(tmp_path: Path) -> None:
    """验证包含 core 组件的 IFW 仓库可通过校验。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "Updates.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<Updates>
  <PackageUpdate>
    <Name>com.tealiving.automationmanual.core</Name>
    <Version>1.0.6</Version>
  </PackageUpdate>
</Updates>
""",
        encoding="utf-8",
    )

    assert verify_repository(repository) == []


def test_verify_repository_rejects_missing_core_package(tmp_path: Path) -> None:
    """验证缺少 core 组件的 IFW 仓库会被拒绝。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    repository = tmp_path / "repository"
    repository.mkdir()
    (repository / "Updates.xml").write_text(
        """<?xml version="1.0" encoding="UTF-8"?>
<Updates>
  <PackageUpdate>
    <Name>com.tealiving.automationmanual.docs</Name>
    <Version>1.0.6</Version>
  </PackageUpdate>
</Updates>
""",
        encoding="utf-8",
    )

    assert verify_repository(repository) == ["core package missing from Updates.xml"]


def test_verify_repository_rejects_missing_updates_xml(tmp_path: Path) -> None:
    """验证缺少 Updates.xml 的 IFW 仓库会被拒绝。

    :param tmp_path: pytest 临时目录。
    :return: None
    """
    repository = tmp_path / "repository"
    repository.mkdir()

    errors = verify_repository(repository)

    assert len(errors) == 1
    assert errors[0].startswith("Updates.xml not found:")
