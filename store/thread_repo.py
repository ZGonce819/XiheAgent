"""threads 表 CRUD 操作。

负责会话（Thread）的创建、查询、更新和级联删除。
每条 thread 对应前端 Dashboard 上的一个对话卡片，关联仓库、分支、PR 等信息。
"""

from __future__ import annotations

from typing import Any

from database.mysql import ConnectionManager, utc_now


class ThreadRepo(ConnectionManager):
    """threads 表数据访问层。

    继承 :class:`ConnectionManager` 获取 MySQL 连接管理能力，
    对外提供 thread 记录的增删改查操作。
    """

    def ensure_table(self) -> None:
        """创建 threads 表（如不存在）。

        Returns:
            无返回值。
        """
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
                backend_type VARCHAR(16) NOT NULL DEFAULT 'local',
                project_name VARCHAR(256),
                local_work_dir VARCHAR(1000),
                source VARCHAR(32) NOT NULL DEFAULT 'dashboard',
                created_at VARCHAR(32) NOT NULL,
                updated_at VARCHAR(32) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        # 兼容已有表：补充新增列
        for ddl in (
            "ALTER TABLE runs ADD COLUMN base_commit VARCHAR(64) NULL",
            "ALTER TABLE runs ADD COLUMN head_commit VARCHAR(64) NULL",
            "ALTER TABLE threads ADD COLUMN backend_type VARCHAR(16) NOT NULL DEFAULT 'local'",
            "ALTER TABLE threads ADD COLUMN project_name VARCHAR(256) NULL",
            "ALTER TABLE threads ADD COLUMN local_work_dir VARCHAR(1000) NULL",
            "ALTER TABLE threads ADD COLUMN source VARCHAR(32) NOT NULL DEFAULT 'dashboard'",
        ):
            try:
                self.execute(ddl)
            except Exception:
                pass

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
        backend_type: str | None = None,
        project_name: str | None = None,
        local_work_dir: str | None = None,
        source: str | None = None,
    ) -> None:
        """创建或更新一条 thread 记录。

        已存在时保留原始 created_at，只更新允许变更的字段。

        Args:
            thread_id: 会话唯一标识。
            title: 会话标题。
            repo_url: 仓库克隆地址。
            repo_owner: 仓库所有者。
            repo_name: 仓库名称。
            branch_name: 工作分支名。
            pr_url: Pull Request 地址。
            user_prompt: 用户原始输入。
            latest_run_status: 最近一次运行状态，默认 ``pending``。
            backend_type: 执行环境类型（``"local"`` 或 ``"sandbox"``），
                默认 ``None`` 表示不更新此字段。
            project_name: 关联的项目名称（来自 ProjectSelector），
                默认 ``None`` 表示不更新此字段。
            source: 会话来源（``"dashboard"`` / ``"schedule"`` 等），
                默认 ``None`` 表示不更新此字段。

        Returns:
            无返回值。
        """
        existing = self.get_thread(thread_id)
        now = utc_now()
        created_at = existing["created_at"] if existing else now
        # 新建时使用传入值或默认 local；更新时保留原值（除非显式传入）
        effective_backend = backend_type or (existing.get("backend_type") if existing else None) or "local"
        effective_project = project_name or (existing.get("project_name") if existing else None)
        effective_work_dir = local_work_dir or (existing.get("local_work_dir") if existing else None)
        effective_source = source or (existing.get("source") if existing else None) or "dashboard"

        self.execute(
            """
            INSERT INTO threads
                (thread_id, title, user_prompt, repo_url, repo_owner, repo_name,
                 branch_name, pr_url, latest_run_status, backend_type, project_name, local_work_dir, source, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                user_prompt = COALESCE(VALUES(user_prompt), user_prompt),
                repo_url = COALESCE(VALUES(repo_url), repo_url),
                repo_owner = COALESCE(VALUES(repo_owner), repo_owner),
                repo_name = COALESCE(VALUES(repo_name), repo_name),
                branch_name = COALESCE(VALUES(branch_name), branch_name),
                pr_url = COALESCE(VALUES(pr_url), pr_url),
                latest_run_status = VALUES(latest_run_status),
                backend_type = VALUES(backend_type),
                project_name = COALESCE(VALUES(project_name), project_name),
                local_work_dir = COALESCE(VALUES(local_work_dir), local_work_dir),
                source = COALESCE(VALUES(source), source),
                updated_at = VALUES(updated_at)
            """,
            (
                thread_id, title, user_prompt, repo_url, repo_owner, repo_name,
                branch_name, pr_url, latest_run_status, effective_backend, effective_project, effective_work_dir, effective_source, created_at, now,
            ),
        )

    def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        """按 thread_id 查询单条记录。

        Args:
            thread_id: 会话唯一标识。

        Returns:
            thread 记录字典；不存在时返回 ``None``。
        """
        return self.fetch_one(
            "SELECT * FROM threads WHERE thread_id = %s", (thread_id,)
        )

    def list_threads(self, limit: int = 50) -> list[dict[str, Any]]:
        """按更新时间倒序返回 thread 列表。

        Args:
            limit: 最大返回条数，默认 50。

        Returns:
            thread 记录字典列表。
        """
        return self.fetch_all(
            "SELECT * FROM threads ORDER BY updated_at DESC LIMIT %s", (limit,)
        )

    def list_thread_ids_by_source(
        self, source: str, *, repo_url: str | None = None, user_prompt: str | None = None
    ) -> list[str]:
        """按来源及任务内容找出可级联清理的会话。"""
        clauses = ["source = %s"]
        params: list[Any] = [source]
        if repo_url is not None:
            clauses.append("repo_url = %s")
            params.append(repo_url)
        if user_prompt is not None:
            clauses.append("user_prompt = %s")
            params.append(user_prompt)
        rows = self.fetch_all(
            f"SELECT thread_id FROM threads WHERE {' AND '.join(clauses)}",
            tuple(params),
        )
        return [str(row["thread_id"]) for row in rows]

    def update_thread_status(
        self,
        thread_id: str,
        status: str,
        *,
        pr_url: str | None = None,
        branch_name: str | None = None,
    ) -> None:
        """更新 thread 的运行状态及可选的 PR / 分支信息。

        Args:
            thread_id: 会话唯一标识。
            status: 新的运行状态。
            pr_url: 可选的 Pull Request 地址。
            branch_name: 可选的工作分支名。

        Returns:
            无返回值。
        """
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

    def update_thread_title(self, thread_id: str, title: str) -> bool:
        """更新 thread 标题。

        Args:
            thread_id: 会话唯一标识。
            title: 新标题。

        Returns:
            成功更新时返回 ``True``；记录不存在时返回 ``False``。
        """
        affected = self.execute(
            "UPDATE threads SET title = %s, updated_at = %s WHERE thread_id = %s",
            (title, utc_now(), thread_id),
        )
        return affected > 0

    def rename_project(self, old_name: str, new_name: str) -> int:
        """把某个项目名下所有会话的项目名批量改为新名称。

        仅更新 ``project_name`` 字段，不触碰 ``updated_at``，
        避免因重命名而打乱侧边栏按活跃时间的排序。

        Args:
            old_name: 原项目名称。
            new_name: 新项目名称。

        Returns:
            受影响的会话条数。
        """
        return self.execute(
            "UPDATE threads SET project_name = %s WHERE project_name = %s",
            (new_name, old_name),
        )

    def delete_thread(self, thread_id: str) -> bool:
        """删除 thread 主记录（级联清理由 LocalMySQLStore 协调）。

        Args:
            thread_id: 会话唯一标识。

        Returns:
            成功删除时返回 ``True``；记录不存在时返回 ``False``。
        """
        affected = self.execute(
            "DELETE FROM threads WHERE thread_id = %s", (thread_id,)
        )
        return affected > 0
