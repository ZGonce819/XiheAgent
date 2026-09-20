"""cron 表达式解析与匹配的测试。

除了常规写法，重点守两条容易搞错的语义：

1. 周字段 **0 和 7 都是周日**；
2. 日与周同时被限定时按**或**处理（``0 0 1 * 1`` 是「1 号**或**周一」）。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from services.cron_schedule import parse_cron


def moment(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    """造一个 UTC 时刻。

    Returns:
        带时区的 ``datetime``。
    """
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def test_parses_a_weekday_morning_schedule() -> None:
    """常见写法能解析，且记住原文。"""
    schedule = parse_cron("0 9 * * 1-5")
    assert schedule.source == "0 9 * * 1-5"
    assert schedule.minute.values == frozenset({0})
    assert schedule.hour.values == frozenset({9})
    assert schedule.day_of_week.values == frozenset({1, 2, 3, 4, 5})


def test_matches_weekday_morning_but_not_other_minutes() -> None:
    """工作日早九点整命中，差一分钟不命中。"""
    schedule = parse_cron("0 9 * * 1-5")
    assert schedule.matches(moment(2026, 9, 18, 9, 0)) is True
    assert schedule.matches(moment(2026, 9, 18, 9, 1)) is False
    assert schedule.matches(moment(2026, 9, 18, 10, 0)) is False


def test_weekend_does_not_match_weekday_schedule() -> None:
    """周六不在 1-5 里。"""
    schedule = parse_cron("0 9 * * 1-5")
    assert schedule.matches(moment(2026, 9, 19, 9, 0)) is False


def test_sunday_is_both_zero_and_seven() -> None:
    """周字段 0 和 7 都指周日，两种写法要等价。"""
    sunday = moment(2026, 9, 20, 12, 0)
    assert parse_cron("0 12 * * 0").matches(sunday) is True
    assert parse_cron("0 12 * * 7").matches(sunday) is True
    assert parse_cron("0 12 * * 1").matches(sunday) is False


def test_day_of_month_and_weekday_are_combined_with_or() -> None:
    """日与周都被限定时是「或」：1 号或周一，不是两者都要满足。"""
    schedule = parse_cron("0 0 1 * 1")
    assert schedule.matches(moment(2026, 9, 1, 0, 0)) is True
    assert schedule.matches(moment(2026, 9, 21, 0, 0)) is True
    assert schedule.matches(moment(2026, 9, 22, 0, 0)) is False


def test_wildcard_day_of_month_defers_to_the_weekday() -> None:
    """日不限、周限定时只看周。"""
    schedule = parse_cron("0 0 * * 1")
    assert schedule.matches(moment(2026, 9, 21, 0, 0)) is True
    assert schedule.matches(moment(2026, 9, 22, 0, 0)) is False


def test_wildcard_weekday_defers_to_the_day_of_month() -> None:
    """周不限、日限定时只看日。"""
    schedule = parse_cron("0 0 15 * *")
    assert schedule.matches(moment(2026, 9, 15, 0, 0)) is True
    assert schedule.matches(moment(2026, 9, 16, 0, 0)) is False


def test_step_values() -> None:
    """``*/15`` 与 ``1-5/2`` 都要按步长展开。"""
    assert parse_cron("*/15 * * * *").minute.values == frozenset({0, 15, 30, 45})
    assert parse_cron("1-5/2 * * * *").minute.values == frozenset({1, 3, 5})


def test_comma_separated_values() -> None:
    """列表写法。"""
    assert parse_cron("0 1,13 * * *").hour.values == frozenset({1, 13})


def test_month_field_is_checked() -> None:
    """月份字段真的参与判断。"""
    schedule = parse_cron("0 0 1 6 *")
    assert schedule.matches(moment(2026, 6, 1, 0, 0)) is True
    assert schedule.matches(moment(2026, 7, 1, 0, 0)) is False


def test_rejects_wrong_number_of_fields() -> None:
    """段数不是 5 就报错，并说明现在有几段。"""
    with pytest.raises(ValueError, match="需要 5 段"):
        parse_cron("0 9 * *")
    with pytest.raises(ValueError, match="需要 5 段"):
        parse_cron("")


def test_rejects_out_of_range_values() -> None:
    """越界要报错，并指出是哪一段。"""
    with pytest.raises(ValueError, match="分钟"):
        parse_cron("60 9 * * *")
    with pytest.raises(ValueError, match="小时"):
        parse_cron("0 24 * * *")
    with pytest.raises(ValueError, match="星期"):
        parse_cron("0 9 * * 8")


def test_rejects_zero_and_non_numeric_steps() -> None:
    """步长必须是正整数。"""
    with pytest.raises(ValueError, match="步长必须大于 0"):
        parse_cron("*/0 * * * *")
    with pytest.raises(ValueError, match="步长必须是数字"):
        parse_cron("*/x * * * *")


def test_rejects_reversed_range() -> None:
    """区间起点大于终点要报错。"""
    with pytest.raises(ValueError, match="起点不能大于终点"):
        parse_cron("5-1 * * * *")


def test_rejects_non_numeric_value() -> None:
    """非数字要报错。"""
    with pytest.raises(ValueError, match="必须是数字"):
        parse_cron("a * * * *")
