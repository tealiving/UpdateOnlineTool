"""在线升级版本判断。"""

from __future__ import annotations

import re
from enum import Enum


class UpdateDecision(str, Enum):
    """升级决策。"""

    NOT_AVAILABLE = "not_available"
    OPTIONAL_UPDATE = "optional_update"
    MANDATORY_UPDATE = "mandatory_update"
    SKIPPED = "skipped"


def parse_version_tuple(version: str) -> tuple[int, int, int]:
    """解析主语义化版本。

    :param version: 版本字符串。
    :return: 三段整数版本。
    """
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)", version.strip())
    if not match:
        return (0, 0, 0)
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)))


def decide_update(
    *,
    current_version: str,
    latest_version: str,
    mandatory: bool,
    min_supported_version: str,
    skipped_version: str | None,
) -> UpdateDecision:
    """判断当前版本是否需要升级。

    :param current_version: 当前版本。
    :param latest_version: 远端最新版本。
    :param mandatory: manifest 是否声明强制升级。
    :param min_supported_version: 最低支持版本。
    :param skipped_version: 用户跳过版本。
    :return: 升级决策。
    """
    current = parse_version_tuple(current_version)
    latest = parse_version_tuple(latest_version)
    minimum = parse_version_tuple(min_supported_version)
    if current >= latest:
        return UpdateDecision.NOT_AVAILABLE
    if mandatory or current < minimum:
        return UpdateDecision.MANDATORY_UPDATE
    if skipped_version and skipped_version == latest_version:
        return UpdateDecision.SKIPPED
    return UpdateDecision.OPTIONAL_UPDATE
