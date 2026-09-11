"""Xihe LangGraph 持久化层。

提供 checkpointer 和 store 的工厂函数：

- **Checkpointer**（Redis）：保存 Agent 每轮运行的消息历史和中间状态，
  支持断点续跑和历史回放。
- **Store**（PostgreSQL）：LangGraph 的跨线程长期存储，用于仓库级记忆等
  需要持久化但不属于单一 checkpointer 线程的数据。

原始参考项目使用 SQLite，Xihe 改为 Redis + PostgreSQL 三层存储架构：
Redis 负责 checkpoint、MySQL 负责业务数据、PostgreSQL 负责 LangGraph Store。
"""

from __future__ import annotations

import logging
from typing import Any

from config.app_config import Settings, get_settings
from database.postgres import build_pg_dsn
from database.redis import build_redis_url

logger = logging.getLogger(__name__)


def make_checkpointer(settings: Settings | None = None) -> Any:
    """创建 Redis 检查点保存器。

    Checkpointer 负责持久化 LangGraph 的线程状态（消息历史、中间变量），
    使得 Agent 可以在多轮对话间保持上下文，也支持断点恢复。

    Args:
        settings: 可选的配置覆盖，默认从全局 singleton 读取。

    Returns:
        ``RedisSaver`` 实例。
    """

    from langgraph.checkpoint.redis import RedisSaver

    if settings is None:
        settings = get_settings()
    url = build_redis_url(settings)
    logger.info("创建 Redis checkpointer：%s:%s/%s", settings.redis_host, settings.redis_port, settings.redis_checkpoint_db)
    return RedisSaver.from_conn_string(url)


def make_langgraph_store(settings: Settings | None = None) -> Any:
    """创建 PostgreSQL LangGraph Store。

    Store 用于跨线程的长期数据存储，例如仓库级记忆、全局配置等。
    与 checkpointer 不同，Store 的数据不绑定到特定线程。

    Args:
        settings: 可选的配置覆盖，默认从全局 singleton 读取。

    Returns:
        ``PostgresStore`` 实例。
    """

    from langgraph.store.postgres import PostgresStore

    if settings is None:
        settings = get_settings()
    dsn = build_pg_dsn(settings)
    logger.info("创建 PostgreSQL LangGraph Store：%s:%s/%s", settings.pg_host, settings.pg_port, settings.pg_database)
    return PostgresStore.from_conn_string(dsn)
