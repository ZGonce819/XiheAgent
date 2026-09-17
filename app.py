"""FastAPI 应用入口。

负责加载环境变量和日志配置、创建 FastAPI 实例、配置 CORS 中间件并注册路由。
"""

from __future__ import annotations

import logging

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

app = FastAPI(
    title="XiheAgent Backend",
    version="0.1.0",
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
