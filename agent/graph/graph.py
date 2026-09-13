"""Xihe LangGraph 基础设施入口。

提供 checkpointer、store 和 Agent 构建器的 singleton 访问器。
所有对象采用懒初始化模式：首次调用时创建并缓存，后续调用直接返回缓存实例。

这样设计的原因：
- FastAPI 启动时不需要立即连接 Redis/PostgreSQL，避免启动失败。
- 测试可以绕过 singleton 直接调用底层工厂函数。
- 避免循环导入：多个模块都需要 checkpointer/store，但不需要在模块加载时就创建连接。
"""

from __future__ import annotations

import logging
from typing import Any

from agent.graph.persistence import (
    make_checkpointer,
    make_langgraph_store,
    _verify_postgres_connection,
    _verify_redis_connection,
)
from config.app_config import get_settings
from database.postgres import build_pg_dsn
from database.redis import build_redis_url
from store import LocalMySQLStore

logger = logging.getLogger(__name__)

# 模块级缓存，首次调用时初始化。
_checkpointer: Any = None
_langgraph_store: Any = None
_store: Any = None


def get_checkpointer() -> Any:
    """获取 Redis 检查点保存器的 singleton 实例。

    Returns:
        已缓存的 ``RedisSaver`` 实例。
    """
    global _checkpointer
    if _checkpointer is None:
        _checkpointer = make_checkpointer()
        settings = get_settings()
        if _verify_redis_connection(build_redis_url(settings)):
            logger.info("Redis checkpointer 已初始化")
        else:
            logger.warning("Redis checkpointer 对象已创建，但 Redis 不可用")
    return _checkpointer


def get_langgraph_store() -> Any:
    """获取 PostgreSQL LangGraph Store 的 singleton 实例。

    Returns:
        已缓存的 ``PostgresStore`` 实例。
    """
    global _langgraph_store
    if _langgraph_store is None:
        _langgraph_store = make_langgraph_store()
        settings = get_settings()
        if _verify_postgres_connection(build_pg_dsn(settings)):
            logger.info("PostgreSQL LangGraph Store 已初始化")
        else:
            logger.warning("PostgreSQL LangGraph Store 对象已创建，但 PostgreSQL 不可用")
    return _langgraph_store


def get_store() -> Any:
    """获取 MySQL 业务存储的 singleton 实例。

    业务 Store 用于保存线程状态、运行事件、审查发现等业务数据。
    与 LangGraph Store 不同，这里保存的是应用层面的业务实体。

    Returns:
        已缓存的 ``LocalMySQLStore`` 实例。
    """
    global _store
    if _store is None:
        _store = LocalMySQLStore()
        logger.info("MySQL 业务 Store 已初始化")
    return _store


def build_agent(config: dict[str, Any]) -> Any:
    """构建 DeepAgent 实例。

    委托给 ``agent.server.get_agent()`` 完成实际的 Agent 组装，
    包括模型、工具、子 Agent、中间件、记忆等组件的注入。

    Args:
        config: Agent 运行配置，包含 thread_id、repo_url、task_kind 等。

    Returns:
        配置好的 DeepAgent 实例。
    """
    from server import get_agent
    return get_agent(config)
