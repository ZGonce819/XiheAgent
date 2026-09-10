"""会话与消息响应 Schema。

定义 Dashboard 会话列表、会话详情、消息和 PR 信息的响应数据结构。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PullRequestInfo(BaseModel):
    """Pull Request 摘要信息。"""

    number: int
    title: str
    state: str = "open"
    headRef: str = ""
    baseRef: str = "master"
    url: str


class MessageChunk(BaseModel):
    """消息内容片段。"""

    kind: str
    text: str


class MessageResponse(BaseModel):
    """单条消息响应。"""

    id: str
    author: str
    timestamp: str
    chunks: list[MessageChunk]


class ThreadDetailResponse(BaseModel):
    """会话详情响应（含消息历史）。"""

    id: str
    title: str
    repo: str
    repoFullName: str
    branch: str
    model: str | None = None
    effort: str | None = None
    source: str = "dashboard"
    status: str = "idle"
    createdAt: int = 0
    updatedAt: int = 0
    messages: list[MessageResponse] = []
    pr: PullRequestInfo | None = None
    latestPlan: dict[str, Any] | None = None
    diffStats: dict[str, Any] | None = None
    changedFiles: list[str] = []


class ThreadMetaResponse(BaseModel):
    """会话元信息响应（不含消息历史）。

    用于 SSE 流式推送，避免把历史 messages 重新推给前端。
    """

    id: str
    title: str
    repo: str
    repoFullName: str
    branch: str
    model: str | None = None
    effort: str | None = None
    source: str = "dashboard"
    status: str = "idle"
    createdAt: int = 0
    updatedAt: int = 0
    pr: PullRequestInfo | None = None
    latestPlan: dict[str, Any] | None = None
    diffStats: dict[str, Any] | None = None
    changedFiles: list[str] = []
