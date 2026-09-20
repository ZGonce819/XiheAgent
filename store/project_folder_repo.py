"""project_local_folders 表 CRUD 操作。

管理项目名称到用户选定的本地工作文件夹的绑定关系。绑定后，该项目的所有会话
都以这个文件夹作为 Agent 工作根目录（文件夹即项目根），并在其中维护
``.xihe-agent/{skills,memories,reviews}`` 布局。
"""

from __future__ import annotations

from typing import Any

from database.mysql import ConnectionManager, utc_now


class ProjectFolderRepo(ConnectionManager):
    """project_local_folders 表数据访问层。"""

    def ensure_table(self) -> None:
        """创建 project_local_folders 表（如不存在）。

        以 project_name 为主键，保证一个项目最多绑定一个本地文件夹。
        """
        self.execute("""
            CREATE TABLE IF NOT EXISTS project_local_folders (
                project_name VARCHAR(256) PRIMARY KEY,
                display_name VARCHAR(256),
                local_folder VARCHAR(1000) NOT NULL,
                created_at VARCHAR(32) NOT NULL,
                updated_at VARCHAR(32) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        # 为已存在的表添加 display_name 列（如果不存在）
        try:
            self.execute(
                "ALTER TABLE project_local_folders "
                "ADD COLUMN display_name VARCHAR(256) NULL AFTER project_name"
            )
        except Exception:
            # 列已存在，忽略错误
            pass

    def upsert(self, project_name: str, local_folder: str, display_name: str | None = None) -> None:
        """创建或更新项目到本地文件夹的绑定。

        Args:
            project_name: 项目名称。
            local_folder: 本地文件夹绝对路径。
            display_name: 项目显示名称（可选，用于 UI 展示）。
        """
        now = utc_now()
        self.execute(
            """
            INSERT INTO project_local_folders
                (project_name, display_name, local_folder, created_at, updated_at)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                display_name = COALESCE(VALUES(display_name), display_name),
                local_folder = VALUES(local_folder),
                updated_at = VALUES(updated_at)
            """,
            (project_name, display_name, local_folder, now, now),
        )

    def get(self, project_name: str) -> dict[str, Any] | None:
        """按项目名查询绑定记录。

        Args:
            project_name: 项目名称。

        Returns:
            绑定记录字典，未找到时返回 None。
        """
        return self.fetch_one(
            "SELECT * FROM project_local_folders WHERE project_name = %s",
            (project_name,),
        )

    def list_all(self) -> list[dict[str, Any]]:
        """返回所有绑定记录，按更新时间降序。

        Returns:
            绑定记录字典列表。
        """
        return self.fetch_all(
            "SELECT * FROM project_local_folders ORDER BY updated_at DESC"
        )

    def rename(self, old_name: str, new_name: str) -> int:
        """项目改名时同步更新绑定记录的项目名。

        Args:
            old_name: 原项目名称。
            new_name: 新项目名称。

        Returns:
            受影响的行数。
        """
        return self.execute(
            "UPDATE project_local_folders SET project_name = %s, updated_at = %s "
            "WHERE project_name = %s",
            (new_name, utc_now(), old_name),
        )
