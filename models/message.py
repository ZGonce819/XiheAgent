"""ThreadMessage 业务实体。

对应 ``thread_messages`` 表，保存 Dashboard 会话中用户可见的问答正文。
与 ``run_events`` 的过程步骤分开，记录的是用户能看到的完整消息。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from models._helpers import _parse_json, _to_iso


@dataclass(frozen=True)
class ThreadMessage:
    """thread_messages 表实体模型。

    Attributes:
        message_id: 消息唯一标识。
        thread_id: 所属会话 ID。
        run_id: 关联的运行 ID，可选。
        author: 消息作者（user / agent / system / tool）。
        content: 消息正文。
        metadata: 附加元数据（JSON 字典）。
        created_at: 创建时间（ISO 8601）。
    """

    message_id: str
    thread_id: str
    author: str
    content: str
    run_id: str | None = None
    metadata: dict[str, Any] | None = None
    created_at: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ThreadMessage:
        """从数据库查询结果构造实体。"""

        return cls(
            message_id=row["message_id"],
            thread_id=row["thread_id"],
            author=row["author"],
            content=row["content"],
            run_id=row.get("run_id"),
            metadata=_parse_json(row.get("metadata")),
            created_at=_to_iso(row.get("created_at")),
        )

    def to_row(self) -> dict[str, Any]:
        """将实体转换为可用于数据库写入的字典。

        ``metadata`` 序列化为 JSON 字符串后写入 TEXT 列。
        """

        import json

        return {
            "message_id": self.message_id,
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "author": self.author,
            "content": self.content,
            "metadata": json.dumps(self.metadata, ensure_ascii=False) if self.metadata is not None else None,
            "created_at": self.created_at,
        }
