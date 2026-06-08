"""版本判断测试。"""

from __future__ import annotations

from update_online_tool.versioning import UpdateDecision, decide_update, parse_version_tuple


def test_parse_version_tuple_ignores_suffix() -> None:
    """验证语义化版本后缀不影响主版本比较。

    :return: None
    """
    assert parse_version_tuple("1.2.3-beta.1") == (1, 2, 3)


def test_decide_update_returns_available_for_newer_version() -> None:
    """验证远端版本更新时返回可升级。

    :return: None
    """
    result = decide_update(
        current_version="1.0.5",
        latest_version="1.0.6",
        mandatory=False,
        min_supported_version="1.0.0",
        skipped_version=None,
    )

    assert result is UpdateDecision.OPTIONAL_UPDATE


def test_decide_update_honors_skipped_version() -> None:
    """验证已跳过版本不会在自动检查时提示。

    :return: None
    """
    result = decide_update(
        current_version="1.0.5",
        latest_version="1.0.6",
        mandatory=False,
        min_supported_version="1.0.0",
        skipped_version="1.0.6",
    )

    assert result is UpdateDecision.SKIPPED


def test_decide_update_returns_mandatory_when_below_minimum() -> None:
    """验证当前版本低于最低支持版本时强制升级。

    :return: None
    """
    result = decide_update(
        current_version="1.0.0",
        latest_version="1.0.6",
        mandatory=False,
        min_supported_version="1.0.5",
        skipped_version="1.0.6",
    )

    assert result is UpdateDecision.MANDATORY_UPDATE
