""" API 接口层。

提供健康检查和 Dashboard 前端所需的 HTTP 接口。
"""

from api.dashboard import dashboard_router
from api.routes import router

__all__ = ["router", "dashboard_router"]
