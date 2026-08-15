"""thread_plans 表 CRUD 操作。

管理编码前技术方案的存储、查询和确认状态。
"""

from __future__ import annotations

from typing import Any

from store._connection import ConnectionManager, utc_now


class PlanRepo(ConnectionManager):
    """thread_plans 表数据访问层。"""

    def ensure_table(self) -> None:
        """创建 thread_plans 表（如不存在）。"""
        self.execute("""
            CREATE TABLE IF NOT EXISTS thread_plans (
                plan_id VARCHAR(64) PRIMARY KEY,
                thread_id VARCHAR(64) NOT NULL,
                run_id VARCHAR(64),
                status VARCHAR(32) NOT NULL DEFAULT 'pending',
                prompt TEXT NOT NULL,
                plan_text TEXT NOT NULL,
                plan_path VARCHAR(500) NOT NULL,
                created_at VARCHAR(32) NOT NULL,
                approved_at VARCHAR(32),
                INDEX idx_thread_id (thread_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

    def add_thread_plan(
        self,
        *,
        plan_id: str,
        thread_id: str,
        prompt: str,
        plan_text: str,
        plan_path: str,
        run_id: str | None = None,
        status: str = "pending",
    ) -> None:
        """保存一份编码前技术方案。

        plan_text 用于前端快速展示；plan_path 指向 data/plans 下的 Markdown 文件，
        方便讲课时直接打开，也方便后续让 Agent 读取已确认方案。
        """
        now = utc_now()
        self.execute(
            """
            INSERT INTO thread_plans
                (plan_id, thread_id, run_id, status, prompt, plan_text, plan_path,
                 created_at, approved_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, NULL)
            ON DUPLICATE KEY UPDATE
                status = VALUES(status),
                prompt = VALUES(prompt),
                plan_text = VALUES(plan_text),
                plan_path = VALUES(plan_path)
            """,
            (
                plan_id, thread_id, run_id, status,
                prompt.strip(), plan_text.strip(), plan_path, now,
            ),
        )

    def get_latest_thread_plan(
        self,
        thread_id: str,
        *,
        status: str | None = None,
    ) -> dict[str, Any] | None:
        """读取某个 thread 最新一份技术方案。

        可选按 status 过滤，例如只查已确认的方案。
        """
        if status is None:
            return self.fetch_one(
                """
                SELECT * FROM thread_plans
                WHERE thread_id = %s
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (thread_id,),
            )
        return self.fetch_one(
            """
            SELECT * FROM thread_plans
            WHERE thread_id = %s AND status = %s
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (thread_id, status),
        )

    def list_thread_plans(self, thread_id: str) -> list[dict[str, Any]]:
        """按创建顺序读取某个 thread 的方案历史。"""
        return self.fetch_all(
            """
            SELECT * FROM thread_plans
            WHERE thread_id = %s
            ORDER BY created_at ASC
            """,
            (thread_id,),
        )

    def approve_thread_plan(self, plan_id: str) -> dict[str, Any] | None:
        """把指定方案标记为已确认，并返回确认后的方案记录。"""
        now = utc_now()
        self.execute(
            """
            UPDATE thread_plans
            SET status = 'approved', approved_at = %s
            WHERE plan_id = %s
            """,
            (now, plan_id),
        )
        return self.fetch_one(
            "SELECT * FROM thread_plans WHERE plan_id = %s", (plan_id,)
        )

    def delete_plans_by_thread(self, thread_id: str) -> int:
        """删除某个 thread 下的所有方案。"""
        return self.execute(
            "DELETE FROM thread_plans WHERE thread_id = %s", (thread_id,)
        )
