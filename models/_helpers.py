"""模型层共享辅助函数。

提供 JSON 解析、布尔转换和时间戳标准化等通用逻辑，
供各实体模型的 ``from_row`` / ``to_row`` 方法复用。
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any

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


def _parse_json(value: str | dict | None) -> dict[str, Any] | None:
    """安全解析 JSON 字符串为字典。

    MySQL TEXT 列存储的 JSON 在查询时仍然是字符串，需要反序列化；
    如果传入的已经是字典（例如测试场景），直接返回。
    解析失败时返回 ``None``，不抛异常，避免单条脏数据阻断列表查询。

    Args:
        value: 待解析的 JSON 字符串、字典或 ``None``。

    Returns:
        解析后的字典，输入为 ``None`` 或解析失败时返回 ``None``。
    """

    if value is None:
        return None
    if isinstance(value, dict):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        logger.warning("JSON 解析失败，返回 None：%r", value[:200] if isinstance(value, str) else value)
        return None


def _parse_bool(value: Any) -> bool:
    """将数据库中的布尔值字段转换为 Python bool。

    MySQL 没有原生 BOOLEAN 类型，通常用 ``TINYINT(1)`` 或 ``VARCHAR``
    存储 ``'true'`` / ``'false'``。这里统一处理常见表示。

    Args:
        value: 待转换的值，可以是 ``bool``、``int``、``str`` 等。

    Returns:
        转换后的布尔值。
    """

    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.lower() in {"true", "1", "yes"}
    return False
