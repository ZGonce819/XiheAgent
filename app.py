"""FastAPI 应用入口。

负责加载环境变量和日志配置、创建 FastAPI 实例、配置 CORS 中间件并注册路由。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.env_config import load_environment
from config.logging_config import configure_logging

load_environment()
configure_logging()

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
