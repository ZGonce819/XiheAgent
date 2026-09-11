"""PostgreSQL 连接管理。

提供 PostgreSQL 连接字符串构建和 LangGraph Store 工厂函数。
"""

from __future__ import annotations

import logging

from config.app_config import Settings

logger = logging.getLogger("database.postgres")


def build_pg_dsn(settings: Settings) -> str:
    """从 Settings 构建 PostgreSQL DSN。

    Args:
        settings: 应用配置实例。

    Returns:
        PostgreSQL 连接字符串。
    """

    return (
        f"postgresql://{settings.pg_user}:{settings.pg_password}"
        f"@{settings.pg_host}:{settings.pg_port}/{settings.pg_database}"
    )
