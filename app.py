"""FastAPI 应用入口。

负责加载环境变量和日志配置、创建 FastAPI 实例、配置 CORS 中间件并注册路由，
并在启动时拉起自动化任务的常驻调度。
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.env_config import load_environment
from config.logging_config import configure_logging
from services.skill_installer import ensure_default_skills

load_environment()
configure_logging()

# 安装默认 skills（从 agent/skills 拷贝到工作区）
logger = logging.getLogger(__name__)
installed = ensure_default_skills()
if installed:
    logger.info("已安装 %d 个默认 skill(s): %s", len(installed), ", ".join(installed))

from api import dashboard_router, router  # noqa: E402
from config.app_config import get_settings  # noqa: E402


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """应用生命周期：启动时拉起自动化任务调度，关闭时停掉。

    调度起不来（例如导入失败）不能让整个服务起不来 —— 手动触发和别的功能都不依赖它。
    """
    settings = get_settings()
    scheduler = None
    if settings.automation_scheduler_enabled:
        try:
            from services.automation_scheduler import start_scheduler

            scheduler = start_scheduler(tick_seconds=settings.automation_tick_seconds)
        except Exception:
            logger.exception("自动化任务调度启动失败，本次不启用自动触发")
    else:
        logger.info("自动化任务调度已按配置关闭（XIHE_AUTOMATION_SCHEDULER=false）")

    yield

    if scheduler is not None:
        scheduler.stop()


app = FastAPI(
    title="XiheAgent Backend",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:3000",
        "http://localhost:3000",
        "http://127.0.0.1:5173",
        "http://localhost:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(dashboard_router)
