"""应用配置模块。

提供 Settings 单例，集中管理所有运行时配置项。
通过 ``get_settings()`` 获取全局唯一实例。
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from config.env_config import get_env

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _safe_int(value: str, default: int) -> int:
    """安全地将字符串转换为整数，无效值回退到默认值。"""
    try:
        return int(value)
    except (ValueError, TypeError):
        return default


class Settings:
    """应用全局配置。

    优先从环境变量 / .env 文件读取，未配置时使用默认值。
    """

    def __init__(self) -> None:
        # ── 项目路径 ──────────────────────────────────────────
        self.data_dir: Path = Path(
            get_env("XIHE_DATA_DIR", str(PROJECT_ROOT / "data"))
        ).resolve()
        self.log_dir: Path = Path(
            get_env("XIHE_LOG_DIR", str(PROJECT_ROOT / "logs"))
        ).resolve()
        self.workspace_root: Path = Path(
            get_env("AI_WORKSPACE_ROOT", r"E:\ai_workspace")
        )

        # ── 日志 ──────────────────────────────────────────────
        self.log_level: str = get_env("XIHE_LOG_LEVEL", "INFO").upper()
        self.log_rotation_when: str = get_env("XIHE_LOG_WHEN", "midnight")
        self.log_rotation_interval: int = _safe_int(get_env("XIHE_LOG_INTERVAL", "1"), 1)
        self.log_retention_days: int = _safe_int(get_env("XIHE_LOG_RETENTION_DAYS", "14"), 14)

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
        self.mysql_database: str = get_env("MYSQL_DATABASE", "xihe")

        # ── PostgreSQL（长期记忆 / LangGraph Store）─────────────
        self.pg_host: str = get_env("PG_HOST", "localhost")
        self.pg_port: int = _safe_int(get_env("PG_PORT", "5432"), 5432)
        self.pg_user: str = get_env("PG_USER", "postgres")
        self.pg_password: str = get_env("PG_PASSWORD")
        self.pg_database: str = get_env("PG_DATABASE", "xihe")

        # ── OpenSandbox 沙盒（可选）────────────────────────────
        self.use_sandbox: bool = get_env("USE_SANDBOX", "false").lower() == "true"
        self.sandbox_host: str = get_env("SANDBOX_HOST")
        self.sandbox_port: int = _safe_int(get_env("SANDBOX_PORT", "8080"), 8080)

        # ── 外部服务密钥 ──────────────────────────────────────
        self.deepseek_api_key: str = get_env("DEEPSEEK_API_KEY")
        self.gitee_token: str = get_env("GITEE_TOKEN")

    @property
    def projects_dir(self) -> Path:
        """Agent 操作代码仓库的工作区子目录。"""
        return self.workspace_root / "projects"

    @property
    def skills_dir(self) -> Path:
        """DeepAgents skills 目录。"""
        return self.workspace_root / "skills"


_settings_instance: Settings | None = None


def get_settings() -> Settings:
    """返回全局唯一的 Settings 实例。"""
    global _settings_instance
    if _settings_instance is None:
        _settings_instance = Settings()
    return _settings_instance


def log_date_text(target_date: date | None = None) -> str:
    """返回历史日志文件使用的日期文本（YYYY-MM-DD）。"""
    return (target_date or date.today()).isoformat()


def backend_log_path(target_date: date | None = None) -> Path:
    """返回后端日志路径。

    不传日期时返回当前正在写入的固定文件名；
    传日期时返回标准轮转后的历史文件名。
    """
    settings = get_settings()
    if target_date is None:
        return settings.log_dir / "backend.log"
    return settings.log_dir / f"backend.log.{log_date_text(target_date)}"


def agent_log_path(target_date: date | None = None) -> Path:
    """返回 Agent 运行日志路径。"""
    settings = get_settings()
    if target_date is None:
        return settings.log_dir / "agent-runs.log"
    return settings.log_dir / f"agent-runs.log.{log_date_text(target_date)}"
