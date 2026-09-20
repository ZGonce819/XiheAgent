""" 核心包。

提供 AI Agent 运行所需的完整能力栈：

- ``graph``: LangGraph 基础设施（模型工厂、持久化层、状态定义、Agent 构建）。
- ``memory``: 长期记忆（工作区级 + 仓库级两层记忆管理）。
- ``middleware``: 中间件管线（上下文注入、消息清洗、工具参数校验、错误处理）。
- ``runtime``: 任务编排与运行时（任务路由、事件流消费、意图分类、方案生成）。
"""

from __future__ import annotations

__all__: list[str] = []
