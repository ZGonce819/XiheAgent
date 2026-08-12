"""RepoWorkspaceMapping 业务实体。

对应 ``repo_workspace_mappings`` 表，管理仓库 URL 到本地工作区路径的映射关系。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from models._helpers import _to_iso


@dataclass(frozen=True)
class RepoWorkspaceMapping:
    """repo_workspace_mappings 表实体模型。

    Attributes:
        mapping_id: 映射记录唯一标识。
        repo_url: 仓库标准 URL（已规范化）。
        repo_owner: 仓库所有者。
        repo_name: 仓库名称。
        workspace_path: 本地工作区路径。
        created_at: 创建时间（ISO 8601）。
        updated_at: 最后更新时间（ISO 8601）。
    """

    mapping_id: str
    repo_url: str
    workspace_path: str
    repo_owner: str | None = None
    repo_name: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> RepoWorkspaceMapping:
        """从数据库查询结果构造实体。

        注意：表中主键列名为 ``id``，映射到实体的 ``mapping_id`` 属性。
        """

        return cls(
            mapping_id=row["id"],
            repo_url=row["repo_url"],
            repo_owner=row.get("repo_owner"),
            repo_name=row.get("repo_name"),
            workspace_path=row["workspace_path"],
            created_at=_to_iso(row.get("created_at")),
            updated_at=_to_iso(row.get("updated_at")),
        )

    def to_row(self) -> dict[str, Any]:
        """将实体转换为可用于数据库写入的字典。"""

        return {
            "id": self.mapping_id,
            "repo_url": self.repo_url,
            "repo_owner": self.repo_owner,
            "repo_name": self.repo_name,
            "workspace_path": self.workspace_path,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @property
    def repo_full_name(self) -> str:
        """拼接仓库全名（owner/repo），缺失时回退到 repo_url。"""

        if self.repo_owner and self.repo_name:
            return f"{self.repo_owner}/{self.repo_name}"
        return self.repo_url
