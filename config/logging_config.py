"""日志配置模块。

使用标准库 TimedRotatingFileHandler 实现按天轮转，
分离全量后端日志与 Agent 执行链路日志。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from config import app_config


class PrefixLoggerFilter(logging.Filter):
    """只允许指定 logger 前缀进入某个 handler。

    用于分离日志：
    - ``backend.log``：完整后端日志；
    - ``agent-runs.log``：只记录 ``agent.run.*`` 链路。
    """

    def __init__(self, *prefixes: str) -> None:
        """初始化前缀过滤器。

        Args:
            *prefixes: 允许通过的 logger 名称前缀，如 ``"agent.run"``。
        """
        super().__init__()
        self.prefixes = prefixes

    def filter(self, record: logging.LogRecord) -> bool:
        """判断日志记录是否匹配任一允许的前缀。

        Args:
            record: 待过滤的日志记录。

        Returns:
            名称匹配任一前缀时返回 ``True``。
        """
        return any(record.name.startswith(prefix) for prefix in self.prefixes)


def _configure_console_encoding() -> None:
    """尽量把控制台输出切换为 UTF-8。

    遍历 ``stdout`` 和 ``stderr``，调用 ``reconfigure`` 设置编码。
    不支持 ``reconfigure`` 的流或失败时静默跳过。

    Returns:
        无返回值，直接修改标准流编码。
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            continue


def _log_level() -> int:
    """解析日志级别，非法配置回退到 INFO。

    Returns:
        对应的 :mod:`logging` 级别常量，无效值时返回 ``logging.INFO``。
    """
    settings = app_config.get_settings()
    return getattr(logging, settings.log_level, logging.INFO)


def _close_and_remove_handlers(logger: logging.Logger) -> None:
    """移除并关闭旧 handler，避免 reload/test 重复写日志。

    Args:
        logger: 需要清理的 logger 实例。

    Returns:
        无返回值，直接修改 logger 的 handler 列表。
    """
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:
            continue


def _make_timed_file_handler(path: Path) -> TimedRotatingFileHandler:
    """创建按时间轮转的文件 handler。

    当前写入固定文件名（如 ``backend.log``），到轮转点后生成
    ``backend.log.2026-07-10`` 格式的历史文件，按 backupCount 自动清理。
    ``delay=True`` 延迟到首条日志到来时才创建文件。

    Args:
        path: 日志文件路径，父目录不存在时自动创建。

    Returns:
        配置完成的 :class:`TimedRotatingFileHandler` 实例。
    """
    settings = app_config.get_settings()
    path.parent.mkdir(parents=True, exist_ok=True)
    handler = TimedRotatingFileHandler(
        filename=str(path),
        when=settings.log_rotation_when,
        interval=settings.log_rotation_interval,
        backupCount=settings.log_retention_days,
        encoding="utf-8",
        utc=False,
        delay=True,
    )
    handler.suffix = "%Y-%m-%d"
    return handler


def configure_logging() -> None:
    """配置后端日志系统。

    初始化日志目录，创建控制台和文件 handler，
    并设置日志格式和过滤规则。

    - 使用标准库 TimedRotatingFileHandler 按天轮转；
    - ``backend.log`` 记录全量后端日志；
    - ``agent-runs.log`` 仅记录 ``agent.run.*`` 链路；
    - 日志格式包含 pid 和 threadName，便于排查并发问题。

    Returns:
        无返回值，通过配置全局 logging 系统产生副作用。
    """
    settings = app_config.get_settings()

    _configure_console_encoding()
    settings.log_dir.mkdir(parents=True, exist_ok=True)

    root_logger = logging.getLogger()
    root_logger.setLevel(_log_level())
    _close_and_remove_handlers(root_logger)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s [%(name)s] [pid=%(process)d thread=%(threadName)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler()
    console_handler.setLevel(_log_level())
    console_handler.setFormatter(formatter)

    backend_handler = _make_timed_file_handler(app_config.backend_log_path())
    backend_handler.setLevel(_log_level())
    backend_handler.setFormatter(formatter)

    agent_handler = _make_timed_file_handler(app_config.agent_log_path())
    agent_handler.setLevel(_log_level())
    agent_handler.setFormatter(formatter)
    agent_handler.addFilter(PrefixLoggerFilter("agent.run"))

    root_logger.addHandler(console_handler)
    root_logger.addHandler(backend_handler)
    root_logger.addHandler(agent_handler)

    logging.getLogger("xihe-agent").info(
        "日志系统已启动：dir=%s level=%s rotation=%s interval=%s retention=%s",
        settings.log_dir,
        settings.log_level,
        settings.log_rotation_when,
        settings.log_rotation_interval,
        settings.log_retention_days,
    )


def read_recent_log(path: Path, max_lines: int = 200) -> list[str]:
    """读取日志文件末尾若干行，供 API 页面或前端展示。

    Args:
        path: 日志文件路径。
        max_lines: 最大读取行数，默认 200；小于 1 时自动修正为 1。

    Returns:
        日志行列表；文件不存在时返回空列表。
    """
    if max_lines < 1:
        max_lines = 1
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    return lines[-max_lines:]
