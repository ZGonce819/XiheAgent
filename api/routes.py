"""基础路由。

提供健康检查等不依赖业务逻辑的轻量接口。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from config.app_config import get_settings
from config.env_config import get_env

router = APIRouter()


@router.get("/health")
def health() -> dict[str, Any]:
    """后端健康检查。

    确认 FastAPI/Uvicorn 服务是否正常运行，同时展示关键运行目录：
    工作区、数据库文件和日志文件。
    注意：只返回密钥是否存在，不返回真实 API Key 或 Token。
    """
    settings = get_settings()

    return {
        "ok": True,
        "project_root": str(settings.PROJECT_ROOT),
        "workspace_root": str(settings.WORKSPACE_ROOT),
        "log_dir": str(settings.LOG_DIR),
        "backend_log": str(settings.backend_log_path()),
        "agent_log": str(settings.agent_log_path()),
        "has_deepseek_key": bool(get_env("DEEPSEEK_API_KEY")),
        "deepseek_base_url": get_env("DEEPSEEK_BASE_URL"),
        "main_model": get_env("MAIN_MODEL", "deepseek-v4-pro"),
        "has_gitee_token": bool(get_env("GITEE_TOKEN")),
    }
