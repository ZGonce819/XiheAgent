"""ISO 时间戳与毫秒时间戳互转工具。

数据库存储的创建/更新时间使用 ISO 8601 格式，前端展示需要毫秒级时间戳。
集中在此模块转换，避免各调用点重复处理时区后缀和空值边界。
"""

from __future__ import annotations

from datetime import datetime


def iso_to_millis(value: str | None) -> int:
    """把 ISO 时间戳转换成毫秒时间戳。

    数据库中的时间字段可能带 ``Z`` 后缀（UTC），也可能带 ``+00:00`` 偏移。
    这里统一规范化后再解析，保证前端拿到的始终是整数毫秒。

    Args:
        value: ISO 8601 格式的时间字符串，例如 ``2025-07-01T12:00:00+00:00``。

    Returns:
        对应的毫秒时间戳。如果传入为空，则返回当前时间的毫秒时间戳。
    """

    if not value:
        return int(datetime.now().timestamp() * 1000)
    normalized = value.replace("Z", "+00:00")
    return int(datetime.fromisoformat(normalized).timestamp() * 1000)
