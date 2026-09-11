"""基础路由。

提供健康检查等不依赖业务逻辑的轻量接口。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from config.app_config import PROJECT_ROOT, agent_log_path, backend_log_path, get_settings
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
        "project_root": str(PROJECT_ROOT),
        "workspace_root": str(settings.workspace_root),
        "log_dir": str(settings.log_dir),
        "backend_log": str(backend_log_path()),
        "agent_log": str(agent_log_path()),
        "has_deepseek_key": bool(get_env("DEEPSEEK_API_KEY")),
        "deepseek_base_url": get_env("DEEPSEEK_BASE_URL"),
        "main_model": get_env("MAIN_MODEL", "deepseek-v4-pro"),
        "has_gitee_token": bool(get_env("GITEE_TOKEN")),
    }
