"""Dashboard 请求体 Schema。

定义前端发送到后端 API 的请求数据结构。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class DashboardThreadMessageRequest(BaseModel):
    """Dashboard 发送消息的请求体。

    用于创建新会话或向已有会话追加对话轮次。

    Attributes:
        content: 用户输入的消息内容。
        images: 附带的图片列表。
        repo: Gitee 仓库地址（完整 URL 或 ``owner/repo`` 简写）。
        model_id: 指定使用的模型 ID。
        effort: 推理努力程度。
    """

    content: str = Field(min_length=1, pattern=r".*\S.*", description="用户输入的消息内容")
    images: list[dict[str, Any]] | None = Field(default=None, description="附带的图片列表")
    repo: str | None = Field(default=None, description="Gitee 仓库地址（完整 URL 或 owner/repo 简写）")
    model_id: str | None = Field(default=None, description="指定使用的模型 ID")
    effort: str | None = Field(default=None, description="推理努力程度")


class TaskCreateRequest(BaseModel):
    """通用任务创建请求体。

    用于非 Dashboard 入口（如 CLI、API 调用）创建 Agent 任务。

    Attributes:
        repo_url: Gitee 仓库完整 URL。
        prompt: 任务描述。
        thread_id: 指定会话 ID，不传则自动生成。
    """

    repo_url: str = Field(min_length=1, description="Gitee 仓库完整 URL")
    prompt: str = Field(min_length=1, max_length=20000, description="任务描述")
    thread_id: str | None = Field(default=None, description="指定会话 ID，不传则自动生成")
