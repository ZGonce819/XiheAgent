"""ThreadPlan 业务实体。

对应 ``thread_plans`` 表，管理编码前技术方案的存储和确认状态。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from models._helpers import _to_iso


@dataclass(frozen=True)
class ThreadPlan:
    """thread_plans 表实体模型。

    Attributes:
        plan_id: 方案唯一标识。
        thread_id: 所属会话 ID。
        run_id: 关联的运行 ID，可选。
        status: 方案状态（pending / approved / rejected）。
        prompt: 触发方案生成的用户输入。
        plan_text: 方案正文（Markdown）。
        plan_path: 方案归档文件路径（data/plans 下的 Markdown 文件）。
        created_at: 创建时间（ISO 8601）。
        approved_at: 确认时间（ISO 8601），未确认时为 ``None``。
    """

    plan_id: str
    thread_id: str
    prompt: str
    plan_text: str
    plan_path: str
    run_id: str | None = None
    status: str = "pending"
    created_at: str | None = None
    approved_at: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ThreadPlan:
        """从数据库查询结果构造实体。"""

        return cls(
            plan_id=row["plan_id"],
            thread_id=row["thread_id"],
            prompt=row["prompt"],
            plan_text=row["plan_text"],
            plan_path=row["plan_path"],
            run_id=row.get("run_id"),
            status=row.get("status", "pending"),
            created_at=_to_iso(row.get("created_at")),
            approved_at=_to_iso(row.get("approved_at")),
        )

    def to_row(self) -> dict[str, Any]:
        """将实体转换为可用于数据库写入的字典。"""

        return {
            "plan_id": self.plan_id,
            "thread_id": self.thread_id,
            "run_id": self.run_id,
            "status": self.status,
            "prompt": self.prompt,
            "plan_text": self.plan_text,
            "plan_path": self.plan_path,
            "created_at": self.created_at,
            "approved_at": self.approved_at,
        }

    @property
    def is_approved(self) -> bool:
        """判断方案是否已被确认。"""

        return self.status == "approved"
