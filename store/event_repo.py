"""run_events 表 CRUD 操作。

记录 Agent 运行过程中的简洁步骤事件，供 Dashboard 实时展示。
"""

from __future__ import annotations

import logging
from typing import Any

from database.mysql import ConnectionManager, utc_now

logger = logging.getLogger(__name__)


class RunEventRepo(ConnectionManager):
    """run_events 表数据访问层。"""

    def ensure_table(self) -> None:
        """创建 run_events 表（如不存在）并迁移列定义。

        表结构包含 event id 主键、thread_id 索引、事件类型、标题、状态、详情和时间戳。
        若已存在 ``id VARCHAR(64)`` 的旧表，自动扩容到 ``VARCHAR(256)``。
        """
        self.execute("""
            CREATE TABLE IF NOT EXISTS run_events (
                id VARCHAR(256) PRIMARY KEY,
                thread_id VARCHAR(64) NOT NULL,
                kind VARCHAR(64) NOT NULL,
                title VARCHAR(500) NOT NULL,
                status VARCHAR(32) NOT NULL,
                detail TEXT,
                created_at VARCHAR(32) NOT NULL,
                updated_at VARCHAR(32) NOT NULL,
                INDEX idx_thread_id (thread_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        # 旧表 id 列可能仍为 VARCHAR(64)，先查再改，避免不必要的 ALTER TABLE。
        col = self.fetch_one(
            "SELECT CHARACTER_MAXIMUM_LENGTH FROM INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'run_events' AND COLUMN_NAME = 'id'",
        )
        if col and col.get("CHARACTER_MAXIMUM_LENGTH", 256) < 256:
            try:
                self.execute("ALTER TABLE run_events MODIFY COLUMN id VARCHAR(256) NOT NULL")
            except Exception:
                logger.warning("run_events.id 列扩容失败，可手动执行："
                               "ALTER TABLE run_events MODIFY COLUMN id VARCHAR(256) NOT NULL",
                               exc_info=True)

    def add_run_event(
        self,
        *,
        event_id: str,
        thread_id: str,
        kind: str,
        title: str,
        status: str,
        detail: str | None = None,
    ) -> None:
        """记录或更新一条运行事件。
    
        相同 event_id 可以被更新，例如先写 in_progress，完成后改成 completed。
        这些事件只用于 Dashboard 实时展示“正在做什么”，不保存大段命令输出。
    
        Args:
            event_id: 事件唯一标识。
            thread_id: 所属会话 ID。
            kind: 事件类型，如 ``tool_call`` / ``step`` 等。
            title: 事件标题，用于前端展示。
            status: 事件状态，如 ``pending`` / ``in_progress`` / ``completed``。
            detail: 事件详情，可为 ``None``。
        """
        now = utc_now()
        self.execute(
            """
            INSERT INTO run_events
                (id, thread_id, kind, title, status, detail, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                kind = VALUES(kind),
                title = VALUES(title),
                status = VALUES(status),
                detail = VALUES(detail),
                updated_at = VALUES(updated_at)
            """,
            (event_id, thread_id, kind, title, status, detail, now, now),
        )

    def list_run_events(self, thread_id: str) -> list[dict[str, Any]]:
        """按创建顺序读取某个 thread 的运行步骤。
        
        Args:
            thread_id: 会话 ID。
        
        Returns:
            事件字典列表，按创建时间升序。
        """
        return self.fetch_all(
            """
            SELECT * FROM run_events
            WHERE thread_id = %s
            ORDER BY created_at ASC
            """,
            (thread_id,),
        )

    def clear_run_events(self, thread_id: str) -> int:
        """清空某个 thread 的临时过程事件。

        run_events 只服务当前/最近一次运行的过程展示；
        历史问答通过 LangGraph checkpoint 持久化，不依赖这里的临时事件。

        Args:
            thread_id: 会话 ID。

        Returns:
            被删除的记录行数。
        """
        return self.execute(
            "DELETE FROM run_events WHERE thread_id = %s", (thread_id,)
        )

    def finish_open_run_events(self, thread_id: str, *, status: str = "completed") -> int:
        """把仍处于运行中的展示事件收尾。
    
        Agent 的 tool_calls 流有时只发起始事件，真正完成状态由工具内部事件记录。
        为避免前端在任务结束后还显示“运行中...”，任务完成或失败时统一关闭残留事件。
    
        Args:
            thread_id: 会话 ID。
            status: 收尾目标状态，默认 ``completed``。
    
        Returns:
            被更新的记录行数。
        """
        now = utc_now()
        return self.execute(
            """
            UPDATE run_events
            SET status = %s, updated_at = %s
            WHERE thread_id = %s
              AND status IN ('pending', 'in_progress')
            """,
            (status, now, thread_id),
        )
