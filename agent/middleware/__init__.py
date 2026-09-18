"""Agent middleware 包导出入口。

中间件顺序建议：
1. ``ContextInjectionMiddleware`` — 整轮任务开始前注入仓库级记忆。
2. ``MessageSanitizeMiddleware`` — 模型调用前清洗历史消息中的不兼容 content block。
3. ``SanitizeToolInputsMiddleware`` — 工具执行前清洗路径、SCM URL 等参数。
4. ``ToolTrackingMiddleware`` — 记录工具执行开始和完成事件，供前端实时展示。
5. ``ModelCallLimitMiddleware`` — 限制模型循环次数（来自 DeepAgents 框架）。
6. ``ToolErrorMiddleware`` — 把工具异常转成模型可恢复的 ToolMessage。

``SkillRetrievalMiddleware`` 是 DeepAgents ``SkillsMiddleware`` 的替换实现：
它的**类名刻意与内置实现相同**（按名称原位替换的前提），因此在本包内以
``SkillRetrievalMiddleware`` 别名导出，避免调用方误以为是两个不同的中间件。
详见 :mod:`agent.middleware.skill_retrieval`。
"""

from __future__ import annotations

from agent.middleware.context_injection import ContextInjectionMiddleware
from agent.middleware.memory_update import MemoryUpdateMiddleware
from agent.middleware.message_sanitize import MessageSanitizeMiddleware
from agent.middleware.skill_retrieval import SkillsMiddleware as SkillRetrievalMiddleware
from agent.middleware.tool_error import ToolErrorMiddleware
from agent.middleware.tool_sanitize import SanitizeToolInputsMiddleware
from agent.middleware.tool_tracking import ToolTrackingMiddleware

__all__ = [
    "ContextInjectionMiddleware",
    "MemoryUpdateMiddleware",
    "MessageSanitizeMiddleware",
    "SanitizeToolInputsMiddleware",
    "SkillRetrievalMiddleware",
    "ToolErrorMiddleware",
    "ToolTrackingMiddleware",
]
