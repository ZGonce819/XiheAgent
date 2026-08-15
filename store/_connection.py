"""MySQL 连接管理基类。

提供连接池、线程安全锁和自动重连能力，所有 Repo 通过继承此类获取统一的数据库访问基础。
"""

from __future__ import annotations

import json
import logging
import os
import threading
from datetime import UTC, datetime
from typing import Any

import pymysql
from pymysql.cursors import DictCursor

logger = logging.getLogger("store.connection")


def utc_now() -> str:
    """统一使用 UTC 时间存储，避免本地时区变化影响排序和排查。"""

    return datetime.now(UTC).isoformat()


class ConnectionManager:
    """MySQL 连接管理基类。

    负责：
    - 从环境变量读取连接参数并建立 pymysql 连接。
    - 提供线程安全的 RLock，所有数据库操作必须持锁。
    - 连接断开时自动重连，保证长时间运行的 Agent 任务不会因瞬时网络抖动失败。

    子类（各 Repo）通过 :meth:`execute` / :meth:`fetch_one` / :meth:`fetch_all`
    执行 SQL，无需关心连接细节。
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._conn: pymysql.connections.Connection | None = None
        self._connect_params = self._build_connect_params()
        self._ensure_connection()

    @staticmethod
    def _build_connect_params() -> dict[str, Any]:
        """从环境变量构建 MySQL 连接参数。"""

        return {
            "host": os.getenv("MYSQL_HOST", "127.0.0.1"),
            "port": int(os.getenv("MYSQL_PORT", "3306")),
            "user": os.getenv("MYSQL_USER", "root"),
            "password": os.getenv("MYSQL_PASSWORD", ""),
            "database": os.getenv("MYSQL_DATABASE", "xihe"),
            "charset": "utf8mb4",
            "cursorclass": DictCursor,
            "autocommit": False,
            "connect_timeout": 10,
            "read_timeout": 30,
            "write_timeout": 30,
        }

    def _ensure_connection(self) -> pymysql.connections.Connection:
        """获取可用连接，断开时自动重连。"""

        if self._conn is not None:
            try:
                self._conn.ping(reconnect=True)
                return self._conn
            except Exception:
                self._conn = None

        try:
            self._conn = pymysql.connect(**self._connect_params)
            return self._conn
        except pymysql.Error as exc:
            logger.error("MySQL 连接失败：%s", exc)
            raise

    @property
    def conn(self) -> pymysql.connections.Connection:
        """获取当前活跃的 MySQL 连接。"""

        return self._ensure_connection()

    def execute(self, sql: str, params: tuple | None = None) -> int:
        """执行写操作（INSERT / UPDATE / DELETE），返回受影响行数。"""

        with self._lock:
            connection = self.conn
            cursor = connection.cursor()
            try:
                affected = cursor.execute(sql, params)
                connection.commit()
                return affected
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()

    def fetch_one(self, sql: str, params: tuple | None = None) -> dict[str, Any] | None:
        """执行查询并返回单行结果。"""

        with self._lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute(sql, params)
                row = cursor.fetchone()
                return dict(row) if row else None
            finally:
                cursor.close()

    def fetch_all(self, sql: str, params: tuple | None = None) -> list[dict[str, Any]]:
        """执行查询并返回全部结果。"""

        with self._lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute(sql, params)
                return [dict(row) for row in cursor.fetchall()]
            finally:
                cursor.close()

    def executemany(self, sql: str, params_list: list[tuple]) -> int:
        """批量执行写操作，返回受影响行数。"""

        with self._lock:
            connection = self.conn
            cursor = connection.cursor()
            try:
                affected = cursor.executemany(sql, params_list)
                connection.commit()
                return affected
            except Exception:
                connection.rollback()
                raise
            finally:
                cursor.close()

    def close(self) -> None:
        """关闭 MySQL 连接，主要供测试或优雅关停使用。"""

        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None
