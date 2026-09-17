"""会话与消息响应 Schema。

定义 Dashboard 会话列表、会话详情、消息和 PR 信息的响应数据结构。
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class PullRequestInfo(BaseModel):
    """Pull Request 摘要信息。

    Attributes:
        number: PR 编号。
        title: PR 标题。
        state: PR 状态。
        headRef: 源分支名称。
        baseRef: 目标分支名称。
        url: PR 访问地址。
    """

    number: int
    title: str
    state: str = "open"
    headRef: str = ""
    baseRef: str = "master"
    url: str


class MessageChunk(BaseModel):
    """消息内容片段。

    Attributes:
        kind: 片段类型（如 ``text``、``image``）。
        text: 片段文本内容。
    """

    kind: str
    text: str


class MessageResponse(BaseModel):
    """单条消息响应。

    Attributes:
        id: 消息唯一标识。
        author: 消息作者。
        timestamp: 消息时间戳。
        chunks: 消息内容片段列表。
    """

    id: str
    author: str
    timestamp: str
    chunks: list[MessageChunk]


class ThreadDetailResponse(BaseModel):
    """会话详情响应（含消息历史）。

    Attributes:
        id: 会话唯一标识。
        title: 会话标题。
        repo: 仓库标识。
        repoFullName: 仓库全名。
        branch: 分支名称。
        model: 使用的模型 ID。
        effort: 推理努力程度。
        source: 会话来源。
        status: 会话状态。
        createdAt: 创建时间戳。
        updatedAt: 最后更新时间戳。
        messages: 消息历史列表。
        pr: 关联的 Pull Request 信息。
        latestPlan: 最新计划数据。
        diffStats: 代码变更统计。
        changedFiles: 变更文件路径列表。
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
    messages: list[MessageResponse] = []
    pr: PullRequestInfo | None = None
    latestPlan: dict[str, Any] | None = None
    diffStats: dict[str, Any] | None = None
    changedFiles: list[str] = []
    pendingApproval: dict[str, Any] | None = None


class ThreadMetaResponse(BaseModel):
    """会话元信息响应（不含消息历史）。

    用于 SSE 流式推送，避免把历史 messages 重新推给前端。

    Attributes:
        id: 会话唯一标识。
        title: 会话标题。
        repo: 仓库标识。
        repoFullName: 仓库全名。
        branch: 分支名称。
        model: 使用的模型 ID。
        effort: 推理努力程度。
        source: 会话来源。
        status: 会话状态。
        createdAt: 创建时间戳。
        updatedAt: 最后更新时间戳。
        pr: 关联的 Pull Request 信息。
        latestPlan: 最新计划数据。
        diffStats: 代码变更统计。
        changedFiles: 变更文件路径列表。
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
    pendingApproval: dict[str, Any] | None = None
