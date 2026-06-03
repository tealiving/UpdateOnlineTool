"""Qt IFW repository 校验命令。"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


REQUIRED_CORE_PACKAGE = "com.tealiving.automationmanual.core"


def verify_repository(repository_dir: Path) -> list[str]:
    """验证 Qt IFW 仓库结构。

    :param repository_dir: IFW repogen 输出目录。
    :return: 错误消息列表，空列表表示校验通过。
    """
    updates_xml = repository_dir / "Updates.xml"
    if not updates_xml.exists():
        return [f"Updates.xml not found: {updates_xml}"]

    root = ET.parse(updates_xml).getroot()
    packages = root.findall(".//PackageUpdate")
    if not packages:
        return ["Updates.xml has no PackageUpdate entries"]

    names = {node.findtext("Name", default="").strip() for node in packages}
    if REQUIRED_CORE_PACKAGE not in names:
        return ["core package missing from Updates.xml"]

    return []


def main(argv: list[str] | None = None) -> int:
    """运行 IFW 仓库校验命令。

    :param argv: 命令行参数；为 None 时读取 sys.argv。
    :return: 进程退出码，0 表示成功。
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("repository_dir", type=Path)
    args = parser.parse_args(argv)

    errors = verify_repository(args.repository_dir)
    if errors:
        for error in errors:
            print(error, file=sys.stderr)
        return 1

    print(f"Verified IFW repository: {args.repository_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
