"""业务实体 ID 生成工具。

项目中 thread、run、message、finding 等实体各自需要全局唯一的标识符。
集中在此模块生成，避免各调用点分散使用 uuid4 导致格式不一致。
"""

from __future__ import annotations

import uuid


def new_thread_id() -> str:
    """生成一个新的 thread ID。"""

    return str(uuid.uuid4())


def new_run_id() -> str:
    """生成一个新的 run ID。"""

    return str(uuid.uuid4())


def new_message_id() -> str:
    """生成一个新的消息 ID。"""

    return str(uuid.uuid4())


def new_finding_id() -> str:
    """生成一个新的 review finding ID。"""

    return str(uuid.uuid4())
