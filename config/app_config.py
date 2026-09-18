"""应用配置模块。

提供 :class:`Settings` 单例，集中管理所有运行时配置项。
通过 :func:`get_settings()` 获取全局唯一实例，首次调用时创建并缓存。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from config.constants import PROJECT_ROOT, WORKSPACE_ROOT
from config.env_config import get_env


def _safe_int(value: str, default: int) -> int:
    """安全地将字符串转换为整数，无效值回退到默认值。

    Args:
        value: 待转换的字符串。
        default: 转换失败时的回退值。

    Returns:
        转换后的整数，或 ``default``。
    """
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


class Settings:
    """应用全局配置。

    优先从环境变量 / .env 文件读取，未配置时使用默认值。
    """

    def __init__(self) -> None:
        """初始化全局配置，从环境变量和 .env 文件读取所有配置项。

        配置按类别分组：项目路径、日志、Redis、MySQL、PostgreSQL、
        OpenSandbox 沙盒、外部服务密钥。未配置时使用内置默认值。
        """
        # ── 项目路径 ──────────────────────────────────────────
        self.data_dir: Path = Path(
            get_env("XIHE_AGENT_DATA_DIR", str(PROJECT_ROOT / "data"))
        ).resolve()
        self.log_dir: Path = Path(
            get_env("XIHE_AGENT_LOG_DIR", str(PROJECT_ROOT / "logs"))
        ).resolve()
        self.workspace_root: Path = WORKSPACE_ROOT
        self._projects_dir: Path | None = None
        self._skills_dir: Path | None = None

        # ── 日志 ──────────────────────────────────────────────
        self.log_level: str = get_env("XIHE_AGENT_LOG_LEVEL", "INFO").upper()
        self.log_rotation_when: str = get_env("XIHE_AGENT_LOG_WHEN", "midnight")
        self.log_rotation_interval: int = _safe_int(get_env("XIHE_AGENT_LOG_INTERVAL", "1"), 1)
        self.log_retention_days: int = _safe_int(get_env("XIHE_AGENT_LOG_RETENTION_DAYS", "14"), 14)

        # ── Redis（Checkpoint + 可选 SSE 事件总线）──────────────
        self.redis_host: str = get_env("REDIS_HOST", "localhost")
        self.redis_port: int = _safe_int(get_env("REDIS_PORT", "6379"), 6379)
        self.redis_password: str = get_env("REDIS_PASSWORD")
        self.redis_checkpoint_db: int = _safe_int(get_env("REDIS_CHECKPOINT_DB", "0"), 0)
        self.redis_stream_db: int = _safe_int(get_env("REDIS_STREAM_DB", "1"), 1)
        self.use_redis_stream: bool = get_env("USE_REDIS_STREAM", "false").lower() == "true"

        # ── MySQL（业务数据）────────────────────────────────────
        self.mysql_host: str = get_env("MYSQL_HOST", "localhost")
        self.mysql_port: int = _safe_int(get_env("MYSQL_PORT", "3306"), 3306)
        self.mysql_user: str = get_env("MYSQL_USER", "root")
        self.mysql_password: str = get_env("MYSQL_PASSWORD")
        self.mysql_database: str = get_env("MYSQL_DATABASE", "xihe-agent")

        # ── PostgreSQL（长期记忆 / LangGraph Store）─────────────
        self.pg_host: str = get_env("PG_HOST", "localhost")
        self.pg_port: int = _safe_int(get_env("PG_PORT", "5432"), 5432)
        self.pg_user: str = get_env("PG_USER", "postgres")
        self.pg_password: str = get_env("PG_PASSWORD")
        self.pg_database: str = get_env("PG_DATABASE", "xihe-agent")

        # ── OpenSandbox 沙盒（可选）────────────────────────────
        self.use_sandbox: bool = get_env("USE_SANDBOX", "false").lower() == "true"
        self.sandbox_host: str = get_env("SANDBOX_HOST")
        self.sandbox_port: int = _safe_int(get_env("SANDBOX_PORT", "8080"), 8080)
        self.sandbox_idle_timeout_days: int = _safe_int(
            get_env("SANDBOX_IDLE_TIMEOUT_DAYS", "3"), 3
        )
        self.sandbox_max_per_thread: int = _safe_int(
            get_env("SANDBOX_MAX_PER_THREAD", "3"), 3
        )

        # ── 自动化任务调度 ────────────────────────────────────
        self.automation_scheduler_enabled: bool = (
            get_env("XIHE_AUTOMATION_SCHEDULER", "true").lower() == "true"
        )
        self.automation_tick_seconds: int = _safe_int(
            get_env("XIHE_AUTOMATION_TICK_SECONDS", "30"), 30
        )

        # ── 外部服务密钥 ──────────────────────────────────────
        self.deepseek_api_key: str = get_env("DEEPSEEK_API_KEY")
        self.gitee_token: str = get_env("GITEE_TOKEN")

    @property
    def projects_dir(self) -> Path:
        """Agent 操作代码仓库的工作区子目录。

        优先使用显式设置的路径，否则返回 ``workspace_root/projects``。

        Returns:
            项目目录路径。
        """
        return self._projects_dir if self._projects_dir is not None else self.workspace_root / "projects"

    @projects_dir.setter
    def projects_dir(self, value: Path) -> None:
        self._projects_dir = value

    @property
    def skills_dir(self) -> Path:
        """DeepAgents skills 目录。

        优先使用显式设置的路径，否则返回 ``workspace_root/skills``。

        Returns:
            技能目录路径。
        """
        return self._skills_dir if self._skills_dir is not None else self.workspace_root / "skills"

    @skills_dir.setter
    def skills_dir(self, value: Path) -> None:
        self._skills_dir = value


_settings_instance: Settings | None = None
"""全局唯一的 Settings 实例缓存，首次调用 :func:`get_settings` 时初始化。"""


def get_settings() -> Settings:
    """返回全局唯一的 Settings 实例。

    首次调用时创建并缓存实例，后续调用直接返回缓存对象。

    Returns:
        全局唯一的 :class:`Settings` 配置实例。
    """
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance


def log_date_text(target_date: date | None = None) -> str:
    """返回历史日志文件使用的日期文本（YYYY-MM-DD）。

    Args:
        target_date: 目标日期，默认使用今天。

    Returns:
        ISO 8601 格式的日期字符串。
    """
    return (target_date or date.today()).isoformat()


def backend_log_path(target_date: date | None = None) -> Path:
    """返回后端日志路径。

    不传日期时返回当前正在写入的固定文件名；
    传日期时返回标准轮转后的历史文件名。

    Args:
        target_date: 目标日期，默认返回当前日志文件。

    Returns:
        后端日志文件的完整路径。
    """
    settings = get_settings()
    if target_date is None:
        return settings.log_dir / "backend.log"
    return settings.log_dir / f"backend.log.{log_date_text(target_date)}"


def agent_log_path(target_date: date | None = None) -> Path:
    """返回 Agent 运行日志路径。

    Args:
        target_date: 目标日期，默认返回当前日志文件。

    Returns:
        Agent 运行日志文件的完整路径。
    """
    settings = get_settings()
    if target_date is None:
        return settings.log_dir / "agent-runs.log"
    return settings.log_dir / f"agent-runs.log.{log_date_text(target_date)}"
