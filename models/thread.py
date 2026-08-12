"""Thread 业务实体。

对应 ``threads`` 表，表示一次 Dashboard 会话的摘要信息。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from models._helpers import _to_iso


@dataclass(frozen=True)
class Thread:
    """threads 表实体模型。

     Attributes:
        thread_id: 会话唯一标识（UUID）。
        title: 会话标题。
        user_prompt: 用户首次输入的内容摘要。
        repo_url: 关联的 Gitee 仓库完整 URL。
        repo_owner: 仓库所有者（组织或个人）。
        repo_name: 仓库名称。
        branch_name: 当前工作分支。
        pr_url: 已创建的 Pull Request 地址。
        latest_run_status: 最近一次运行状态（pending / running / completed / failed）。
        created_at: 创建时间（ISO 8601）。
        updated_at: 最后更新时间（ISO 8601）。
    """

    thread_id: str
    title: str
    user_prompt: str | None = None
    repo_url: str | None = None
    repo_owner: str | None = None
    repo_name: str | None = None
    branch_name: str | None = None
    pr_url: str | None = None
    latest_run_status: str = "pending"
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Thread:
        """从数据库查询结果（DictCursor row）构造实体。"""

        return cls(
            thread_id=row["thread_id"],
            title=row["title"],
            user_prompt=row.get("user_prompt"),
            repo_url=row.get("repo_url"),
            repo_owner=row.get("repo_owner"),
            repo_name=row.get("repo_name"),
            branch_name=row.get("branch_name"),
            pr_url=row.get("pr_url"),
            latest_run_status=row.get("latest_run_status", "pending"),
            created_at=_to_iso(row.get("created_at")),
            updated_at=_to_iso(row.get("updated_at")),
        )

    def to_row(self) -> dict[str, Any]:
        """将实体转换为可用于数据库写入的字典。"""

        return {
            "thread_id": self.thread_id,
            "title": self.title,
            "user_prompt": self.user_prompt,
            "repo_url": self.repo_url,
            "repo_owner": self.repo_owner,
            "repo_name": self.repo_name,
            "branch_name": self.branch_name,
            "pr_url": self.pr_url,
            "latest_run_status": self.latest_run_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @property
    def repo_full_name(self) -> str:
        """拼接仓库全名（owner/repo），缺失时回退到 repo_url。"""

        if self.repo_owner and self.repo_name:
            return f"{self.repo_owner}/{self.repo_name}"
        return self.repo_url or ""
