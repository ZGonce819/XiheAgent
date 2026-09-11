"""Redis 连接管理。

提供 Redis 连接构建和获取的统一入口，支持 checkpointer 和 stream 两种用途。
"""

from __future__ import annotations

import logging
from typing import Any

from config.app_config import Settings, get_settings

logger = logging.getLogger("database.redis")

_redis_stream_conn: Any = None


def build_redis_url(settings: Settings) -> str:
    """从 Settings 构建 Redis 连接 URL（用于 checkpointer）。

    Args:
        settings: 应用配置实例。

    Returns:
        Redis 连接 URL，格式为 ``redis://[:password]@host:port/db``。
    """

    auth = f":{settings.redis_password}@" if settings.redis_password else ""
    return f"redis://{auth}{settings.redis_host}:{settings.redis_port}/{settings.redis_checkpoint_db}"


def get_redis_stream_connection() -> Any:
    """获取 Redis Stream 连接实例。

    使用与 Checkpoint 相同的 Redis 实例，但切换到 SSE 专用 DB。
    连接在模块级别缓存，避免每次调用都新建连接。
    """

    global _redis_stream_conn
    if _redis_stream_conn is not None:
        try:
            _redis_stream_conn.ping()
            return _redis_stream_conn
        except Exception:
            _redis_stream_conn = None

    import redis

    settings = get_settings()
    _redis_stream_conn = redis.Redis(
        host=settings.redis_host,
        port=settings.redis_port,
        password=settings.redis_password or None,
        db=settings.redis_stream_db,
        decode_responses=True,
        socket_timeout=5,
        socket_connect_timeout=3,
        retry_on_timeout=True,
    )
    return _redis_stream_conn
