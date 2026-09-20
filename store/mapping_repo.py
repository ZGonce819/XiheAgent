"""repo_workspace_mappings 表 CRUD 操作。

管理仓库 URL 到本地工作区路径的映射关系。
"""

from __future__ import annotations

from typing import Any

from database.mysql import ConnectionManager, utc_now


class MappingRepo(ConnectionManager):
    """repo_workspace_mappings 表数据访问层。"""

    def ensure_table(self) -> None:
        """创建 repo_workspace_mappings 表（如不存在）。

        表结构以 id 为主键，repo_url 设唯一索引，保证同一仓库不会出现重复映射。
        """
        self.execute("""
            CREATE TABLE IF NOT EXISTS repo_workspace_mappings (
                id VARCHAR(64) PRIMARY KEY,
                repo_url VARCHAR(500) NOT NULL,
                repo_owner VARCHAR(128),
                repo_name VARCHAR(128),
                workspace_path VARCHAR(500) NOT NULL,
                created_at VARCHAR(32) NOT NULL,
                updated_at VARCHAR(32) NOT NULL,
                UNIQUE INDEX idx_repo_url (repo_url)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

    def upsert_mapping(
        self,
        *,
        mapping_id: str,
        repo_url: str,
        workspace_path: str,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> None:
        """创建或更新仓库到工作区的映射。

        Args:
            mapping_id: 映射记录唯一 ID。
            repo_url: 仓库 URL。
            workspace_path: 本地工作区路径。
            repo_owner: 可选的仓库 owner。
            repo_name: 可选的仓库名称。
        """
        now = utc_now()
        self.execute(
            """
            INSERT INTO repo_workspace_mappings
                (id, repo_url, repo_owner, repo_name, workspace_path, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                repo_owner = COALESCE(VALUES(repo_owner), repo_owner),
                repo_name = COALESCE(VALUES(repo_name), repo_name),
                workspace_path = VALUES(workspace_path),
                updated_at = VALUES(updated_at)
            """,
            (mapping_id, repo_url, repo_owner, repo_name, workspace_path, now, now),
        )

    def get_mapping_by_repo(self, repo_url: str) -> dict[str, Any] | None:
        """按仓库 URL 查询映射记录。

        Args:
            repo_url: 仓库 URL。

        Returns:
            映射记录字典，未找到时返回 None。
        """
        return self.fetch_one(
            "SELECT * FROM repo_workspace_mappings WHERE repo_url = %s",
            (repo_url,),
        )

    def list_mappings(self) -> list[dict[str, Any]]:
        """返回所有映射记录。

        Returns:
            映射字典列表，按更新时间降序。
        """
        return self.fetch_all(
            "SELECT * FROM repo_workspace_mappings ORDER BY updated_at DESC"
        )
