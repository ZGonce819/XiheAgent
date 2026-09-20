"""xihe-agent LangGraph 基础设施子包。

提供 Agent 运行所需的底层支撑：

- ``model``: 大语言模型工厂（DeepSeek 兼容接口）。
- ``persistence``: Redis checkpointer + PostgreSQL store 持久化层。
- ``graph``: singleton 访问器，统一提供 checkpointer/store/Agent 构建入口。
- ``checkpoint_history``: 从 checkpoint 中提取可见的消息历史，供前端展示。
"""

from __future__ import annotations

__all__: list[str] = []
