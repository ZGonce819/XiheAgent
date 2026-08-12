"""Run 业务实体。

对应 ``runs`` 表，记录每次 Agent 运行的开始、结束和状态。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from models._helpers import _to_iso


@dataclass(frozen=True)
class Run:
    """runs 表实体模型。

    Attributes:
        run_id: 运行记录唯一标识。
        thread_id: 所属会话 ID。
        status: 运行状态（pending / running / completed / failed）。
        started_at: 开始时间（ISO 8601）。
        finished_at: 结束时间（ISO 8601），未完成时为 ``None``。
        error: 失败原因，成功时为 ``None``。
    """

    run_id: str
    thread_id: str
    status: str
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Run:
        """从数据库查询结果构造实体。"""

        return cls(
            run_id=row["run_id"],
            thread_id=row["thread_id"],
            status=row["status"],
            started_at=_to_iso(row.get("started_at")),
            finished_at=_to_iso(row.get("finished_at")),
            error=row.get("error"),
        )

    def to_row(self) -> dict[str, Any]:
        """将实体转换为可用于数据库写入的字典。"""

        return {
            "run_id": self.run_id,
            "thread_id": self.thread_id,
            "status": self.status,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "error": self.error,
        }

    @property
    def is_finished(self) -> bool:
        """判断本次运行是否已结束。"""

        return self.finished_at is not None
