"""Dashboard 前端接口。

提供会话管理、SSE 流式输出等 Dashboard 所需的 HTTP 接口。
前端通过 POST SSE 实时接收 Agent 运行事件和流式正文。
"""

from fastapi import APIRouter

from api.dashboard.schedules import router as schedules_router
from api.dashboard.settings import router as settings_router
from api.dashboard.skills import router as skills_router
from api.dashboard.threads import router as threads_router
from api.dashboard.workspace import router as workspace_router

dashboard_router = APIRouter(prefix="/api/v1")

dashboard_router.include_router(settings_router)
dashboard_router.include_router(schedules_router)
dashboard_router.include_router(skills_router)
dashboard_router.include_router(threads_router)
dashboard_router.include_router(workspace_router)
