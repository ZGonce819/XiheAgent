"""threads 表 CRUD 操作。

负责会话（Thread）的创建、查询、更新和级联删除。
"""

from __future__ import annotations

from typing import Any

from database.mysql import ConnectionManager, utc_now


class ThreadRepo(ConnectionManager):
    """threads 表数据访问层。"""

    def ensure_table(self) -> None:
        """创建 threads 表（如不存在）。"""
        self.execute("""
            CREATE TABLE IF NOT EXISTS threads (
                thread_id VARCHAR(64) PRIMARY KEY,
                title VARCHAR(500) NOT NULL,
                user_prompt TEXT,
                repo_url VARCHAR(500),
                repo_owner VARCHAR(128),
                repo_name VARCHAR(128),
                branch_name VARCHAR(128),
                pr_url VARCHAR(500),
                latest_run_status VARCHAR(32) NOT NULL DEFAULT 'pending',
                created_at VARCHAR(32) NOT NULL,
                updated_at VARCHAR(32) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

    def upsert_thread(
        self,
        *,
        thread_id: str,
        title: str,
        repo_url: str | None = None,
        repo_owner: str | None = None,
        repo_name: str | None = None,
        branch_name: str | None = None,
        pr_url: str | None = None,
        user_prompt: str | None = None,
        latest_run_status: str = "pending",
    ) -> None:
        """创建或更新一条 thread 记录。

        已存在时保留原始 created_at，只更新允许变更的字段。
        """
        existing = self.get_thread(thread_id)
        now = utc_now()
        created_at = existing["created_at"] if existing else now

        self.execute(
            """
            INSERT INTO threads
                (thread_id, title, user_prompt, repo_url, repo_owner, repo_name,
                 branch_name, pr_url, latest_run_status, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                user_prompt = COALESCE(VALUES(user_prompt), user_prompt),
                repo_url = COALESCE(VALUES(repo_url), repo_url),
                repo_owner = COALESCE(VALUES(repo_owner), repo_owner),
                repo_name = COALESCE(VALUES(repo_name), repo_name),
                branch_name = COALESCE(VALUES(branch_name), branch_name),
                pr_url = COALESCE(VALUES(pr_url), pr_url),
                latest_run_status = VALUES(latest_run_status),
                updated_at = VALUES(updated_at)
            """,
            (
                thread_id, title, user_prompt, repo_url, repo_owner, repo_name,
                branch_name, pr_url, latest_run_status, created_at, now,
            ),
        )

    def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        """按 thread_id 查询单条记录。"""
        return self.fetch_one(
            "SELECT * FROM threads WHERE thread_id = %s", (thread_id,)
        )

    def list_threads(self, limit: int = 50) -> list[dict[str, Any]]:
        """按更新时间倒序返回 thread 列表。"""
        return self.fetch_all(
            "SELECT * FROM threads ORDER BY updated_at DESC LIMIT %s", (limit,)
        )

    def update_thread_status(
        self,
        thread_id: str,
        status: str,
        *,
        pr_url: str | None = None,
        branch_name: str | None = None,
    ) -> None:
        """更新 thread 的运行状态及可选的 PR / 分支信息。"""
        self.execute(
            """
            UPDATE threads
            SET latest_run_status = %s,
                pr_url = COALESCE(%s, pr_url),
                branch_name = COALESCE(%s, branch_name),
                updated_at = %s
            WHERE thread_id = %s
            """,
            (status, pr_url, branch_name, utc_now(), thread_id),
        )

    def delete_thread(self, thread_id: str) -> bool:
        """删除 thread 主记录（级联清理由 LocalMySQLStore 协调）。"""
        affected = self.execute(
            "DELETE FROM threads WHERE thread_id = %s", (thread_id,)
        )
        return affected > 0
