"""thread_messages 表 CRUD 操作。

保存 Dashboard 会话中用户可见的问答正文。
"""

from __future__ import annotations

import json
from typing import Any

from database.mysql import ConnectionManager, utc_now


class MessageRepo(ConnectionManager):
    """thread_messages 表数据访问层。"""

    def ensure_table(self) -> None:
        """创建 thread_messages 表（如不存在）。"""
        self.execute("""
            CREATE TABLE IF NOT EXISTS thread_messages (
                message_id VARCHAR(64) PRIMARY KEY,
                thread_id VARCHAR(64) NOT NULL,
                run_id VARCHAR(64),
                author VARCHAR(32) NOT NULL,
                content TEXT NOT NULL,
                metadata TEXT,
                created_at VARCHAR(32) NOT NULL,
                INDEX idx_thread_id (thread_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

    def add_thread_message(
        self,
        *,
        message_id: str,
        thread_id: str,
        author: str,
        content: str,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """追加保存一条 Dashboard 会话消息。

        thread_messages 保存的是用户能看到的问答正文，和 run_events 的过程步骤分开。
        继续输入新问题时不会覆盖上一轮问题和回答。
        对用户消息做去重，避免重复提交相同内容。
        """
        normalized_content = content.strip()

        if author == "user" and normalized_content:
            latest_user = self.fetch_one(
                """
                SELECT content FROM thread_messages
                WHERE thread_id = %s AND author = 'user'
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (thread_id,),
            )
            if latest_user is not None:
                existing_content = str(latest_user["content"]).strip()
                if existing_content == normalized_content:
                    return

        self.execute(
            """
            INSERT INTO thread_messages
                (message_id, thread_id, run_id, author, content, metadata, created_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                content = VALUES(content),
                metadata = VALUES(metadata)
            """,
            (
                message_id, thread_id, run_id, author, normalized_content,
                json.dumps(metadata, ensure_ascii=False) if metadata is not None else None, utc_now(),
            ),
        )

    def list_thread_messages(self, thread_id: str) -> list[dict[str, Any]]:
        """按写入顺序读取某个 thread 的会话消息。"""
        return self.fetch_all(
            """
            SELECT * FROM thread_messages
            WHERE thread_id = %s
            ORDER BY created_at ASC
            """,
            (thread_id,),
        )

    def delete_messages_by_thread(self, thread_id: str) -> int:
        """删除某个 thread 下的所有消息。"""
        return self.execute(
            "DELETE FROM thread_messages WHERE thread_id = %s", (thread_id,)
        )
