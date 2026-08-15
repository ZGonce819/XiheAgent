"""settings 表 CRUD 操作。

提供键值存储，用于保存少量配置项。
"""

from __future__ import annotations

import json
from typing import Any

from store._connection import ConnectionManager, utc_now


class SettingRepo(ConnectionManager):
    """settings 表数据访问层。"""

    def ensure_table(self) -> None:
        """创建 settings 表（如不存在）。"""
        self.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                `key` VARCHAR(128) PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at VARCHAR(32) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

    def set_setting(self, key: str, value: Any) -> None:
        """写入或更新一个配置项。"""
        self.execute(
            """
            INSERT INTO settings (`key`, value, updated_at)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                value = VALUES(value),
                updated_at = VALUES(updated_at)
            """,
            (key, json.dumps(value, ensure_ascii=False), utc_now()),
        )

    def get_setting(self, key: str, default: Any = None) -> Any:
        """读取一个配置项，不存在时返回默认值。"""
        row = self.fetch_one(
            "SELECT value FROM settings WHERE `key` = %s", (key,)
        )
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return default
