"""review_findings 表 CRUD 操作。

管理 Reviewer Agent 发现的代码审查问题。
"""

from __future__ import annotations

from typing import Any

from database.mysql import ConnectionManager, utc_now


class FindingRepo(ConnectionManager):
    """review_findings 表数据访问层。"""

    def ensure_table(self) -> None:
        """创建 review_findings 表（如不存在）。

        表结构包含 finding id 主键、thread_id 索引、文件路径、行号、
        严重级别、标题、描述、状态和时间戳。
        """
        self.execute("""
            CREATE TABLE IF NOT EXISTS review_findings (
                id VARCHAR(64) PRIMARY KEY,
                thread_id VARCHAR(64) NOT NULL,
                file VARCHAR(500) NOT NULL,
                line INT,
                severity VARCHAR(32) NOT NULL,
                title VARCHAR(500) NOT NULL,
                description TEXT NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'open',
                created_at VARCHAR(32) NOT NULL,
                updated_at VARCHAR(32) NOT NULL,
                INDEX idx_thread_id (thread_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

    def add_finding(
        self,
        *,
        finding_id: str,
        thread_id: str,
        file: str,
        line: int | None,
        severity: str,
        title: str,
        description: str,
        status: str = "open",
    ) -> None:
        """记录或更新一条审查发现。

        Args:
            finding_id: 发现唯一 ID。
            thread_id: 所属会话 ID。
            file: 关联的文件路径。
            line: 可选的行号。
            severity: 严重程度，例如 ``error``、``warning``。
            title: 发现标题。
            description: 详细描述。
            status: 状态，默认 ``open``。
        """
        now = utc_now()
        self.execute(
            """
            INSERT INTO review_findings
                (id, thread_id, file, line, severity, title, description, status,
                 created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                file = VALUES(file),
                line = VALUES(line),
                severity = VALUES(severity),
                title = VALUES(title),
                description = VALUES(description),
                status = VALUES(status),
                updated_at = VALUES(updated_at)
            """,
            (
                finding_id, thread_id, file, line, severity, title, description,
                status, now, now,
            ),
        )

    def list_findings(self, thread_id: str) -> list[dict[str, Any]]:
        """按创建顺序读取某个 thread 的审查发现。

        Args:
            thread_id: 会话 ID。

        Returns:
            审查发现字典列表，按创建时间升序。
        """
        return self.fetch_all(
            """
            SELECT * FROM review_findings
            WHERE thread_id = %s
            ORDER BY created_at ASC
            """,
            (thread_id,),
        )

    def delete_findings_by_thread(self, thread_id: str) -> int:
        """删除某个 thread 下的所有审查发现。

        Args:
            thread_id: 会话 ID。

        Returns:
            被删除的行数。
        """
        return self.execute(
            "DELETE FROM review_findings WHERE thread_id = %s", (thread_id,)
        )
