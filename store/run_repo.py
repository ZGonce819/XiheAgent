"""runs 表 CRUD 操作。

记录每次 Agent 运行的开始、结束、状态和错误信息。
每次 Agent 启动对应一条 run 记录，Dashboard 摘要展示依赖此表。
"""

from __future__ import annotations

from typing import Any

from database.mysql import ConnectionManager, utc_now


class RunRepo(ConnectionManager):
    """runs 表数据访问层。

    继承 :class:`ConnectionManager` 获取 MySQL 连接管理能力，
    对外提供运行记录的增删查操作。
    """

    def ensure_table(self) -> None:
        """创建 runs 表（如不存在）。

        Returns:
            无返回值。
        """
        self.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id VARCHAR(64) PRIMARY KEY,
                thread_id VARCHAR(64) NOT NULL,
                status VARCHAR(32) NOT NULL,
                started_at VARCHAR(32) NOT NULL,
                finished_at VARCHAR(32),
                error TEXT,
                base_commit VARCHAR(64),
                head_commit VARCHAR(64),
                INDEX idx_thread_id (thread_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        for column in ("base_commit", "head_commit"):
            try:
                self.execute(f"ALTER TABLE runs ADD COLUMN {column} VARCHAR(64) NULL")
            except Exception:
                # Existing installations already have the column.
                pass

    def record_run(
        self,
        *,
        run_id: str,
        thread_id: str,
        status: str,
        error: str | None = None,
        finished: bool = False,
        base_commit: str | None = None,
        head_commit: str | None = None,
    ) -> None:
        """记录或更新一次运行。

        finished=True 时写入 finished_at，否则只记录 started_at。

        Args:
            run_id: 运行唯一标识。
            thread_id: 所属会话 ID。
            status: 运行状态。
            error: 可选的错误信息。
            finished: 是否已结束，默认 ``False``。

        Returns:
            无返回值。
        """
        now = utc_now()
        finished_at = now if finished else None
        self.execute(
            """
            INSERT INTO runs (run_id, thread_id, status, started_at, finished_at, error, base_commit, head_commit)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                status = VALUES(status),
                finished_at = VALUES(finished_at),
                error = VALUES(error),
                base_commit = COALESCE(VALUES(base_commit), base_commit),
                head_commit = COALESCE(VALUES(head_commit), head_commit)
            """,
            (run_id, thread_id, status, now, finished_at, error, base_commit, head_commit),
        )

    def get_latest_run(self, thread_id: str) -> dict[str, Any] | None:
        """读取某个 thread 最近一次运行记录。

        Dashboard 摘要需要展示失败原因，帮助用户了解是模型、Git 还是权限问题。

        Args:
            thread_id: 所属会话 ID。

        Returns:
            最近的运行记录字典；不存在时返回 ``None``。
        """
        return self.fetch_one(
            """
            SELECT * FROM runs
            WHERE thread_id = %s
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (thread_id,),
        )

    def count_runs(self, thread_id: str) -> int:
        """统计某个 thread 的运行次数。"""
        row = self.fetch_one(
            "SELECT COUNT(*) AS cnt FROM runs WHERE thread_id = %s",
            (thread_id,),
        )
        return int(row["cnt"]) if row else 0

    def delete_runs_by_thread(self, thread_id: str) -> int:
        """删除某个 thread 下的所有运行记录。

        Args:
            thread_id: 所属会话 ID。

        Returns:
            被删除的记录数。
        """
        return self.execute(
            "DELETE FROM runs WHERE thread_id = %s", (thread_id,)
        )
