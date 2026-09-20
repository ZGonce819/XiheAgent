"""模型层共享辅助函数。

提供时间戳标准化等通用逻辑，供各实体模型的 ``from_row`` / ``to_row`` 方法复用。
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

logger = logging.getLogger(__name__)


def _to_iso(value: str | datetime | None) -> str | None:
    """将时间值统一转换为 ISO 8601 字符串。

    支持已经是字符串的情况（MySQL 返回 VARCHAR 时间戳），
    也支持 ``datetime`` 对象（测试或内存构造场景）。
    ``None`` 直接返回 ``None``，不做强转。

    Args:
        value: 待转换的时间值，可以是字符串、``datetime`` 或 ``None``。

    Returns:
        ISO 8601 格式字符串，输入为 ``None`` 时返回 ``None``。
    """

    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    return str(value)
