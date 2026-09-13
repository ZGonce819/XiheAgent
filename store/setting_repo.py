"""settings 表 CRUD 操作。

提供键值存储，用于保存少量配置项。
值以 JSON 格式序列化存储，读取时自动反序列化。
"""

from __future__ import annotations

import json
from typing import Any

from database.mysql import ConnectionManager, utc_now


class SettingRepo(ConnectionManager):
    """settings 表数据访问层。

    继承 :class:`ConnectionManager` 获取 MySQL 连接管理能力，
    对外提供键值配置的读写操作。
    """

    def ensure_table(self) -> None:
        """创建 settings 表（如不存在）。

        Returns:
            无返回值。
        """
        self.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                `key` VARCHAR(128) PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at VARCHAR(32) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

    def set_setting(self, key: str, value: Any) -> None:
        """写入或更新一个配置项。

        值会被 JSON 序列化后存储。

        Args:
            key: 配置项键名。
            value: 配置项值，支持 JSON 可序列化的任意类型。

        Returns:
            无返回值。
        """
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
        """读取一个配置项，不存在时返回默认值。

        Args:
            key: 配置项键名。
            default: 键不存在或反序列化失败时的回退值。

        Returns:
            反序列化后的配置值；不存在时返回 ``default``。
        """
        row = self.fetch_one(
            "SELECT value FROM settings WHERE `key` = %s", (key,)
        )
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except (json.JSONDecodeError, TypeError):
            return default
