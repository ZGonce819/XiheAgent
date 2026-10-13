"""Readiness probes for the two required task persistence services."""
from concurrent.futures import ThreadPoolExecutor

import pymysql
import redis

from config.app_config import get_settings


def check_mysql(settings):
    with pymysql.connect(
        host=settings.mysql_host, port=settings.mysql_port,
        user=settings.mysql_user, password=settings.mysql_password,
        database=settings.mysql_database, connect_timeout=3, read_timeout=3,
    ) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        return cursor.fetchone() == (1,)


def check_redis(settings):
    with redis.Redis(
        host=settings.redis_host, port=settings.redis_port,
        password=settings.redis_password or None, db=settings.redis_checkpoint_db,
        socket_connect_timeout=3, socket_timeout=3,
    ) as client:
        return bool(client.ping())


def readiness():
    settings = get_settings()
    dependencies = {}
    with ThreadPoolExecutor(max_workers=2) as executor:
        checks = {name: executor.submit(check, settings) for name, check in (
            ("mysql", check_mysql), ("redis", check_redis),
        )}
        for name, future in checks.items():
            try:
                dependencies[name] = "ok" if future.result() else "unavailable"
            except Exception:
                dependencies[name] = "unavailable"
    return {"ok": all(v == "ok" for v in dependencies.values()), "dependencies": dependencies}
