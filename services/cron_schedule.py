"""五段式 cron 表达式的解析与匹配。

前端「自动化任务」页面填的就是这种表达式（``0 9 * * 1-5``），本模块负责判断
某个时刻是否命中。

支持 ``*``、单值、区间 ``a-b``、列表 ``a,b,c``、步长 ``*/s`` 与 ``a-b/s``。
不引入第三方依赖：需要的语法只有这些，为它多带一个包进来不划算。

Note:
    字段顺序是「分 时 日 月 周」。**周字段 0 和 7 都表示周日。**

Note:
    日与周同时被限定时按「或」处理，这是 Vixie cron 的经典行为：
    ``0 0 1 * 1`` 表示每月 1 号**或**每周一，而不是两者都要满足。
    踩过这个坑的人很多，所以这里显式实现并写了用例。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

FIELD_NAMES = ("分钟", "小时", "日", "月", "星期")

MINUTE_RANGE = (0, 59)
HOUR_RANGE = (0, 23)
DAY_OF_MONTH_RANGE = (1, 31)
MONTH_RANGE = (1, 12)
DAY_OF_WEEK_RANGE = (0, 7)
"""星期允许写 7，解析后折算成 0（周日）。"""


@dataclass(frozen=True)
class CronField:
    """一段 cron 字段。"""

    values: frozenset[int]
    wildcard: bool
    """是否写成 ``*``。日与周的「或」判断需要区分「没限制」和「限制成了全部」。"""


@dataclass(frozen=True)
class CronSchedule:
    """一条解析好的五段式 cron 表达式。"""

    minute: CronField
    hour: CronField
    day_of_month: CronField
    month: CronField
    day_of_week: CronField
    source: str

    def matches(self, moment: datetime) -> bool:
        """判断某个时刻是否命中这条表达式。

        只精确到分钟：秒与微秒不参与判断。

        Args:
            moment: 待判断的时刻。

        Returns:
            命中返回 ``True``。
        """
        if moment.minute not in self.minute.values:
            return False
        if moment.hour not in self.hour.values:
            return False
        if moment.month not in self.month.values:
            return False

        # ``datetime.weekday()`` 是周一=0，cron 是周日=0，差一个位移。
        cron_weekday = (moment.weekday() + 1) % 7
        day_ok = moment.day in self.day_of_month.values
        week_ok = cron_weekday in self.day_of_week.values

        if self.day_of_month.wildcard:
            return week_ok
        if self.day_of_week.wildcard:
            return day_ok
        return day_ok or week_ok


def _to_int(text: str, *, name: str) -> int:
    """把一段文本转成整数。

    Args:
        text: 原始文本。
        name: 字段中文名，用于错误信息。

    Returns:
        解析出的整数。

    Raises:
        ValueError: 不是整数。
    """
    try:
        return int(text.strip())
    except ValueError as exc:
        raise ValueError(f"{name}必须是数字：{text.strip()!r}") from exc


def _parse_field(raw: str, *, name: str, minimum: int, maximum: int) -> CronField:
    """解析一段字段。

    Args:
        raw: 字段原文，例如 ``*/5``、``1-5``、``1,15``。
        name: 字段中文名，用于错误信息。
        minimum: 允许的下界。
        maximum: 允许的上界。

    Returns:
        解析出的字段。

    Raises:
        ValueError: 写法无法识别，或取值越界。
    """
    text = raw.strip()
    if not text:
        raise ValueError(f"{name}不能为空")

    wildcard = text == "*"
    values: set[int] = set()

    for piece in text.split(","):
        piece = piece.strip()
        if not piece:
            raise ValueError(f"{name}的写法不对：{raw!r}")

        step = 1
        if "/" in piece:
            base, _, step_text = piece.partition("/")
            step = _to_int(step_text, name=f"{name}的步长")
            if step <= 0:
                raise ValueError(f"{name}的步长必须大于 0：{piece!r}")
            piece = base.strip()

        if piece == "*":
            start, end = minimum, maximum
        elif "-" in piece:
            start_text, _, end_text = piece.partition("-")
            start = _to_int(start_text, name=name)
            end = _to_int(end_text, name=name)
        else:
            start = end = _to_int(piece, name=name)

        if start > end:
            raise ValueError(f"{name}的区间起点不能大于终点：{piece!r}")
        for value in range(start, end + 1, step):
            if not minimum <= value <= maximum:
                raise ValueError(f"{name}的取值需在 {minimum} 到 {maximum} 之间：{value}")
            values.add(value)

    if not values:
        raise ValueError(f"{name}没有匹配到任何取值")
    return CronField(frozenset(values), wildcard)


def parse_cron(expression: str) -> CronSchedule:
    """解析一条五段式 cron 表达式。

    Args:
        expression: 形如 ``0 9 * * 1-5`` 的表达式。

    Returns:
        解析后的表达式。

    Raises:
        ValueError: 段数不是 5，或某一段写法非法。错误信息里会指出是哪一段。
    """
    parts = str(expression or "").split()
    if len(parts) != 5:
        raise ValueError(f"执行计划需要 5 段（分 时 日 月 周），现在是 {len(parts)} 段")

    minute = _parse_field(parts[0], name=FIELD_NAMES[0], minimum=MINUTE_RANGE[0], maximum=MINUTE_RANGE[1])
    hour = _parse_field(parts[1], name=FIELD_NAMES[1], minimum=HOUR_RANGE[0], maximum=HOUR_RANGE[1])
    day_of_month = _parse_field(
        parts[2], name=FIELD_NAMES[2], minimum=DAY_OF_MONTH_RANGE[0], maximum=DAY_OF_MONTH_RANGE[1]
    )
    month = _parse_field(
        parts[3], name=FIELD_NAMES[3], minimum=MONTH_RANGE[0], maximum=MONTH_RANGE[1]
    )
    raw_week = _parse_field(
        parts[4], name=FIELD_NAMES[4], minimum=DAY_OF_WEEK_RANGE[0], maximum=DAY_OF_WEEK_RANGE[1]
    )
    day_of_week = CronField(
        frozenset(0 if value == 7 else value for value in raw_week.values), raw_week.wildcard
    )

    return CronSchedule(
        minute=minute,
        hour=hour,
        day_of_month=day_of_month,
        month=month,
        day_of_week=day_of_week,
        source=" ".join(parts),
    )
