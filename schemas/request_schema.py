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
    backend_type: str | None = Field(default=None, description="运行环境：local 或 sandbox")
    edit_message_id: str | None = Field(default=None, description="正在编辑的最近一条用户消息 ID")
    edit_message_content: str | None = Field(default=None, description="待编辑消息的原始文本，用于校验实时消息 ID")
    edit_snapshot: list[dict[str, Any]] | None = Field(default=None, description="编辑前的页面消息快照")
    rollback_confirmed: bool = Field(default=False, description="是否确认回退后重新发送")


class DashboardEditPreviewRequest(BaseModel):
    message_id: str = Field(min_length=1, description="待编辑的用户消息 ID")
    original_content: str | None = Field(default=None, description="消息原文，用于校验实时 ID")


class DashboardViewSnapshotRequest(BaseModel):
    messages: list[dict[str, Any]] = Field(description="当前页面完整消息视图")


class DashboardThreadTitleUpdateRequest(BaseModel):
    title: str = Field(min_length=1, max_length=500, description="新的会话标题")


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
