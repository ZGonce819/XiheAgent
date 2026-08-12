"""Setting 业务实体。

对应 ``settings`` 表，提供键值存储，用于保存少量配置项。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from models._helpers import _to_iso


@dataclass(frozen=True)
class Setting:
    """settings 表实体模型。

    Attributes:
        key: 配置项名称（主键）。
        value: 配置项值（Python 对象，存储时序列化为 JSON）。
        updated_at: 最后更新时间（ISO 8601）。
    """

    key: str
    value: Any
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Setting:
        """从数据库查询结果构造实体。

        ``value`` 列在数据库中为 JSON 字符串，读取时反序列化。
        """

        raw_value = row["value"]
        try:
            parsed = json.loads(raw_value)
        except (json.JSONDecodeError, TypeError):
            parsed = raw_value

        return cls(
            key=row["key"],
            value=parsed,
            updated_at=_to_iso(row.get("updated_at")),
        )

    def to_row(self) -> dict[str, Any]:
        """将实体转换为可用于数据库写入的字典。

        ``value`` 序列化为 JSON 字符串后写入 TEXT 列。
        """

        return {
            "key": self.key,
            "value": json.dumps(self.value, ensure_ascii=False),
            "updated_at": self.updated_at,
        }
