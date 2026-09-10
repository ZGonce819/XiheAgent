"""用户信息响应 Schema。

定义 ``/dashboard/api/me`` 接口返回的用户数据结构。
"""

from __future__ import annotations

from pydantic import BaseModel


class UserInfoResponse(BaseModel):
    """当前用户信息响应。"""

    login: str
    email: str | None = None
    avatar_url: str | None = None
    is_admin: bool = False
    slack_oauth_enabled: bool = False
