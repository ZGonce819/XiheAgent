"""用户任务意图识别（兼容再导出层）。

历史上意图识别逻辑在本模块实现，后来在 ``utils.intent_utils`` 中出现了等价副本，
两份代码逐渐漂移。为消除重复、并避免 ``utils`` ↔ ``agent`` 双向依赖形成的循环导入，
唯一实现已统一收敛到 :mod:`utils.intent_utils`。

本模块只保留 ``agent.runtime.task_intent`` 这个导入路径作为向后兼容层，
把公开符号原样再导出，不再包含任何业务逻辑。新增意图识别能力请改
``utils.intent_utils``，不要在此处重新实现。
"""

from __future__ import annotations

from config.constants import TaskKind
from utils.intent_utils import (
    IntentClassification,
    classify_task_kind,
    is_pull_only_task,
    is_read_only_task,
    is_workspace_listing_task,
)

__all__ = [
    "IntentClassification",
    "TaskKind",
    "classify_task_kind",
    "is_pull_only_task",
    "is_read_only_task",
    "is_workspace_listing_task",
]
