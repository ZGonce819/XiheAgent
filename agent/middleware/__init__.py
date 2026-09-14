"""Agent middleware 包导出入口。

中间件顺序建议：
1. ``ContextInjectionMiddleware`` — 整轮任务开始前注入仓库级记忆。
2. ``MessageSanitizeMiddleware`` — 模型调用前清洗历史消息中的不兼容 content block。
3. ``SanitizeToolInputsMiddleware`` — 工具执行前清洗路径、SCM URL 等参数。
4. ``ToolTrackingMiddleware`` — 记录工具执行开始和完成事件，供前端实时展示。
5. ``ModelCallLimitMiddleware`` — 限制模型循环次数（来自 DeepAgents 框架）。
6. ``ToolErrorMiddleware`` — 把工具异常转成模型可恢复的 ToolMessage。
"""

from __future__ import annotations

from agent.middleware.context_injection import ContextInjectionMiddleware
from agent.middleware.memory_update import MemoryUpdateMiddleware
from agent.middleware.message_sanitize import MessageSanitizeMiddleware
from agent.middleware.tool_error import ToolErrorMiddleware
from agent.middleware.tool_sanitize import SanitizeToolInputsMiddleware
from agent.middleware.tool_tracking import ToolTrackingMiddleware

__all__ = [
    "ContextInjectionMiddleware",
    "MemoryUpdateMiddleware",
    "MessageSanitizeMiddleware",
    "SanitizeToolInputsMiddleware",
    "ToolErrorMiddleware",
    "ToolTrackingMiddleware",
]
