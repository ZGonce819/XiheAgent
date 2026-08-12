"""ReviewFinding 业务实体。

对应 ``review_findings`` 表，管理 Reviewer Agent 发现的代码审查问题。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from models._helpers import _to_iso


@dataclass(frozen=True)
class ReviewFinding:
    """review_findings 表实体模型。

    Attributes:
        finding_id: 发现记录唯一标识。
        thread_id: 所属会话 ID。
        file: 问题所在文件路径。
        line: 问题所在行号，可选。
        severity: 严重程度（error / warning / info）。
        title: 问题标题。
        description: 问题详细描述。
        status: 处理状态（open / resolved / dismissed）。
        created_at: 创建时间（ISO 8601）。
        updated_at: 最后更新时间（ISO 8601）。
    """

    finding_id: str
    thread_id: str
    file: str
    severity: str
    title: str
    description: str
    line: int | None = None
    status: str = "open"
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ReviewFinding:
        """从数据库查询结果构造实体。

        注意：表中主键列名为 ``id``，映射到实体的 ``finding_id`` 属性。
        """

        return cls(
            finding_id=row["id"],
            thread_id=row["thread_id"],
            file=row["file"],
            line=row.get("line"),
            severity=row["severity"],
            title=row["title"],
            description=row["description"],
            status=row.get("status", "open"),
            created_at=_to_iso(row.get("created_at")),
            updated_at=_to_iso(row.get("updated_at")),
        )

    def to_row(self) -> dict[str, Any]:
        """将实体转换为可用于数据库写入的字典。"""

        return {
            "id": self.finding_id,
            "thread_id": self.thread_id,
            "file": self.file,
            "line": self.line,
            "severity": self.severity,
            "title": self.title,
            "description": self.description,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
