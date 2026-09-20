"""sandbox_registry 表 CRUD 操作。

记录 thread 与 OpenSandbox 沙箱实例的映射关系，支持：
- 同一 thread 复用存活的沙箱（30 分钟内）；
- 沙箱被 server 回收后自动重建；
- 超过空闲天数的记录自动清理；
- 单线程沙箱数量超限时淘汰最旧记录。
"""

from __future__ import annotations

from typing import Any

from database.mysql import ConnectionManager, utc_now


class SandboxRepo(ConnectionManager):
    """sandbox_registry 表数据访问层。

    继承 :class:`ConnectionManager` 获取 MySQL 连接管理能力，
    对外提供沙箱注册、查询、清理操作。
    """

    def ensure_table(self) -> None:
        """创建 sandbox_registry 表（如不存在）。

        Returns:
            无返回值。
        """
        self.execute("""
            CREATE TABLE IF NOT EXISTS sandbox_registry (
                id           BIGINT AUTO_INCREMENT PRIMARY KEY,
                sandbox_id   VARCHAR(64)  NOT NULL,
                thread_id    VARCHAR(64)  NOT NULL,
                status       VARCHAR(16)  NOT NULL DEFAULT 'active',
                created_at   VARCHAR(32)  NOT NULL,
                last_used_at VARCHAR(32)  NOT NULL,
                INDEX idx_thread (thread_id, status),
                INDEX idx_last_used (last_used_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

    def register(self, sandbox_id: str, thread_id: str) -> None:
        """注册一个新创建的沙箱。

        Args:
            sandbox_id: OpenSandbox 沙箱 ID。
            thread_id: 所属会话 ID。

        Returns:
            无返回值。
        """
        now = utc_now()
        self.execute(
            """
            INSERT INTO sandbox_registry (sandbox_id, thread_id, status, created_at, last_used_at)
            VALUES (%s, %s, 'active', %s, %s)
            """,
            (sandbox_id, thread_id, now, now),
        )

    def get_active_sandbox(self, thread_id: str) -> dict[str, Any] | None:
        """获取某个 thread 最新的 active 沙箱记录。

        Args:
            thread_id: 会话唯一标识。

        Returns:
            最新的 active 沙箱记录字典；不存在时返回 ``None``。
        """
        return self.fetch_one(
            """
            SELECT * FROM sandbox_registry
            WHERE thread_id = %s AND status = 'active'
            ORDER BY last_used_at DESC
            LIMIT 1
            """,
            (thread_id,),
        )

    def update_last_used(self, sandbox_id: str) -> None:
        """更新沙箱的最后使用时间。

        Args:
            sandbox_id: 沙箱 ID。

        Returns:
            无返回值。
        """
        self.execute(
            "UPDATE sandbox_registry SET last_used_at = %s WHERE sandbox_id = %s",
            (utc_now(), sandbox_id),
        )

    def mark_killed(self, sandbox_id: str) -> None:
        """将沙箱状态标记为 killed。

        Args:
            sandbox_id: 沙箱 ID。

        Returns:
            无返回值。
        """
        self.execute(
            "UPDATE sandbox_registry SET status = 'killed' WHERE sandbox_id = %s",
            (sandbox_id,),
        )

    def cleanup_idle(self, idle_days: int = 3) -> int:
        """清理超过指定天数未使用的沙箱记录。

        只删除 status 不是 'active' 的过期记录；active 记录先标记为 killed 再删除。

        Args:
            idle_days: 空闲天数阈值，默认 3。

        Returns:
            被清理的记录数。
        """
        # 先将超期的 active 记录标记为 killed
        self.execute(
            """
            UPDATE sandbox_registry
            SET status = 'killed'
            WHERE status = 'active'
              AND last_used_at < DATE_SUB(NOW(), INTERVAL %s DAY)
            """,
            (idle_days,),
        )
        # 删除所有超期记录
        return self.execute(
            """
            DELETE FROM sandbox_registry
            WHERE last_used_at < DATE_SUB(NOW(), INTERVAL %s DAY)
            """,
            (idle_days,),
        )

    def cleanup_per_thread(self, max_count: int = 3) -> int:
        """每个线程保留最多 max_count 条记录，淘汰最旧的。

        Args:
            max_count: 每个线程保留的最大记录数，默认 3。

        Returns:
            被清理的记录数。
        """
        # 找出超出上限的 thread 及其最旧记录 ID
        rows = self.fetch_all(
            """
            SELECT sr.id FROM sandbox_registry sr
            INNER JOIN (
                SELECT thread_id, COUNT(*) AS cnt
                FROM sandbox_registry
                GROUP BY thread_id
                HAVING cnt > %s
            ) excess ON sr.thread_id = excess.thread_id
            WHERE sr.last_used_at <= (
                SELECT sub.last_used_at FROM (
                    SELECT last_used_at FROM sandbox_registry
                    WHERE thread_id = sr.thread_id
                    ORDER BY last_used_at DESC
                    LIMIT 1 OFFSET %s
                ) sub
            )
            """,
            (max_count, max_count),
        )
        if not rows:
            return 0

        ids_to_delete = [row["id"] for row in rows]
        placeholders = ",".join(["%s"] * len(ids_to_delete))
        return self.execute(
            f"DELETE FROM sandbox_registry WHERE id IN ({placeholders})",
            tuple(ids_to_delete),
        )

    def delete_by_thread(self, thread_id: str) -> int:
        """删除某个 thread 的所有沙箱注册记录。

        调用方应在删除前自行终止远程沙箱。

        Args:
            thread_id: 会话唯一标识。

        Returns:
            被删除的记录数。
        """
        return self.execute(
            "DELETE FROM sandbox_registry WHERE thread_id = %s",
            (thread_id,),
        )
