"""xihe-agent LangGraph 持久化层。

提供 checkpointer 和 store 的工厂函数：

- **Checkpointer**（Redis）：保存 Agent 每轮运行的消息历史和中间状态，
  支持断点续跑和历史回放。
- **Store**（PostgreSQL）：LangGraph 的跨线程长期存储，用于仓库级记忆等
  需要持久化但不属于单一 checkpointer 线程的数据。

原始参考项目使用 SQLite，xihe-agent 改为 Redis + PostgreSQL 三层存储架构：
Redis 负责 checkpoint、MySQL 负责业务数据、PostgreSQL 负责 LangGraph Store。
"""

from __future__ import annotations

import logging
from typing import Any

import psycopg
from langgraph.store.postgres import PostgresStore
import redis as _redis

from agent.graph.redis_checkpointer import ReliableRedisSaver
from config.app_config import Settings, get_settings
from database.postgres import build_pg_dsn
from database.redis import build_redis_url

logger = logging.getLogger(__name__)


def _verify_redis_connection(url: str) -> bool:
    """验证 Redis 连接是否可用。

    Args:
        url: Redis 连接 URL。

    Returns:
        连接可用返回 ``True``，否则返回 ``False``。
    """
    try:
        client = _redis.from_url(url, socket_connect_timeout=3, socket_timeout=3)
        client.ping()
        client.close()
        return True
    except Exception as exc:
        logger.warning("Redis 连接验证失败：%s", exc)
        return False


def _verify_postgres_connection(dsn: str) -> bool:
    """验证 PostgreSQL 连接是否可用。

    Args:
        dsn: PostgreSQL 连接字符串。

    Returns:
        连接可用返回 ``True``，否则返回 ``False``。
    """
    try:
        conn = psycopg.connect(dsn, connect_timeout=3)
        conn.close()
        return True
    except Exception as exc:
        logger.warning("PostgreSQL 连接验证失败：%s", exc)
        return False


def make_checkpointer(settings: Settings | None = None) -> Any:
    """创建 Redis 检查点保存器。

    Checkpointer 负责持久化 LangGraph 的线程状态（消息历史、中间变量），
    使得 Agent 可以在多轮对话间保持上下文，也支持断点恢复。

    注意：
        ``RedisSaver.from_conn_string()`` 是上下文管理器，返回的
        ``_GeneratorContextManager`` 不是 saver 本身。这里直接构造
        ``RedisSaver`` 实例以适配 singleton 长生命周期场景。

    Args:
        settings: 可选的配置覆盖，默认从全局 singleton 读取。

    Returns:
        ``RedisSaver`` 实例。
    """
    if settings is None:
        settings = get_settings()
    url = build_redis_url(settings)
    logger.info("创建 Redis checkpointer：%s:%s/%s", settings.redis_host, settings.redis_port, settings.redis_checkpoint_db)
    saver = ReliableRedisSaver(url)
    saver.setup()
    return saver


def _run_store_migrations(dsn: str) -> None:
    """用 autocommit 连接手动执行 LangGraph Store 迁移。

    ``PostgresStore.setup()`` 内部用 ``self._cursor()`` 强制开事务，
    而 ``CREATE INDEX CONCURRENTLY`` 不允许在事务块中执行。
    这里绕过 ``setup()``，直接用 autocommit 连接逐条执行迁移 SQL。

    Args:
        dsn: PostgreSQL 连接字符串。
    """

    migrations = [
        """
        CREATE TABLE IF NOT EXISTS store (
            prefix text NOT NULL,
            key text NOT NULL,
            value jsonb NOT NULL,
            created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (prefix, key)
        );
        """,
        """
        CREATE INDEX CONCURRENTLY IF NOT EXISTS store_prefix_idx
            ON store USING btree (prefix text_pattern_ops);
        """,
        """
        ALTER TABLE store
        ADD COLUMN IF NOT EXISTS expires_at TIMESTAMP WITH TIME ZONE,
        ADD COLUMN IF NOT EXISTS ttl_minutes INT;
        """,
        """
        CREATE INDEX IF NOT EXISTS idx_store_expires_at ON store (expires_at)
        WHERE expires_at IS NOT NULL;
        """,
    ]
    conn = psycopg.connect(dsn, autocommit=True)
    try:
        cur = conn.cursor()
        cur.execute("CREATE TABLE IF NOT EXISTS store_migrations (v INTEGER PRIMARY KEY)")
        cur.execute("SELECT v FROM store_migrations ORDER BY v DESC LIMIT 1")
        row = cur.fetchone()
        current_version = row[0] if row else -1
        for v, sql in enumerate(migrations[current_version + 1 :], start=current_version + 1):
            cur.execute(sql)
            cur.execute("INSERT INTO store_migrations (v) VALUES (%s)", (v,))
            logger.info("LangGraph Store 迁移 %d 已完成", v)
    except Exception as exc:
        logger.warning("LangGraph Store 迁移异常（可能已初始化）：%s", exc)
    finally:
        conn.close()


def make_langgraph_store(settings: Settings | None = None) -> Any:
    """创建 PostgreSQL LangGraph Store。

    Store 用于跨线程的长期数据存储，例如仓库级记忆、全局配置等。
    与 checkpointer 不同，Store 的数据不绑定到特定线程。

    注意：
        ``PostgresStore.from_conn_string()`` 是上下文管理器，返回的
        ``_GeneratorContextManager`` 不是 store 本身。这里直接构造
        ``PostgresStore`` 实例以适配 singleton 长生命周期场景。

    Args:
        settings: 可选的配置覆盖，默认从全局 singleton 读取。

    Returns:
        ``PostgresStore`` 实例。
    """
    if settings is None:
        settings = get_settings()
    dsn = build_pg_dsn(settings)
    logger.info("创建 PostgreSQL LangGraph Store：%s:%s/%s", settings.pg_host, settings.pg_port, settings.pg_database)
    # PostgresStore.setup() 内部用 self._cursor() 强制开事务，
    # 而 CREATE INDEX CONCURRENTLY 不允许在事务中执行。
    # 绕过 setup()，直接用 autocommit 连接手动执行迁移 SQL。
    _run_store_migrations(dsn)
    conn = psycopg.connect(dsn, autocommit=True)
    store = PostgresStore(conn)
    return store
