"""RunEvent 业务实体。

对应 ``run_events`` 表，记录 Agent 运行过程中的简洁步骤事件，
供 Dashboard 实时展示"正在做什么"。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from models._helpers import _to_iso


@dataclass(frozen=True)
class RunEvent:
    """run_events 表实体模型。

    Attributes:
        event_id: 事件唯一标识（通常为 ``{thread_id}:{key}``）。
        thread_id: 所属会话 ID。
        kind: 事件类型（think / tool / shell 等）。
        title: 事件标题，前端直接展示。
        status: 事件状态（pending / in_progress / completed / failed）。
        detail: 可选的详细信息。
        created_at: 创建时间（ISO 8601）。
        updated_at: 最后更新时间（ISO 8601）。
    """

    event_id: str
    thread_id: str
    kind: str
    title: str
    status: str
    detail: str | None = None
    created_at: str | None = None
    updated_at: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> RunEvent:
        """从数据库查询结果构造实体。

        注意：表中主键列名为 ``id``，映射到实体的 ``event_id`` 属性。

        Args:
            row: 数据库查询返回的字典行，须包含 ``id``、``thread_id``、``kind``、
                ``title``、``status`` 等必填字段。

        Returns:
            构造完成的 :class:`RunEvent` 实例。
        """

        return cls(
            event_id=row["id"],
            thread_id=row["thread_id"],
            kind=row["kind"],
            title=row["title"],
            status=row["status"],
            detail=row.get("detail"),
            created_at=_to_iso(row.get("created_at")),
            updated_at=_to_iso(row.get("updated_at")),
        )

    def to_row(self) -> dict[str, Any]:
        """将实体转换为可用于数据库写入的字典。

        Returns:
            与数据库列名对应的字典，可直接用于 INSERT / UPDATE 语句。
        """

        return {
            "id": self.event_id,
            "thread_id": self.thread_id,
            "kind": self.kind,
            "title": self.title,
            "status": self.status,
            "detail": self.detail,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
