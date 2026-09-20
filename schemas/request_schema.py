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
    project_name: str | None = Field(default=None, description="关联的项目名称")
    local_work_dir: str | None = Field(default=None, max_length=1000, description="当前对话的本地工作目录")
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


class DashboardProjectRenameRequest(BaseModel):
    """项目重命名请求体。

    Attributes:
        old_name: 原项目名称。
        new_name: 新项目名称。
    """

    old_name: str = Field(min_length=1, max_length=256, description="原项目名称")
    new_name: str = Field(min_length=1, max_length=256, description="新项目名称")


class DashboardProjectBindFolderRequest(BaseModel):
    """创建项目请求体。

    Attributes:
        project_name: 项目名称，绑定关系的业务键。
    """

    project_name: str = Field(min_length=1, max_length=256, description="项目名称")
    local_folder: str | None = Field(default=None, max_length=1000, description="技能本地作用域使用的文件夹")


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


class DashboardSkillCreateRequest(BaseModel):
    """创建技能的请求体。

    支持两种方式：直接提供 ``name`` + ``description`` + ``body`` 粘贴创建；
    或提供完整的 ``content``（含 frontmatter 的 SKILL.md 全文）由后端校验。

    Attributes:
        name: 技能标识，留空时从 ``content`` 的 frontmatter 推导。
        description: 技能说明，留空时从 ``content`` 的 frontmatter 推导。
        body: 技能正文（不含 frontmatter）。
        content: 完整的 SKILL.md 文本，与 ``body`` 二选一。
        scope: 目标作用域，``global`` / ``project_set`` / ``local``。
        project_name: 项目集合名或项目名。
    """

    name: str | None = Field(default=None, max_length=128, description="技能标识")
    description: str | None = Field(default=None, max_length=1024, description="技能说明")
    body: str | None = Field(default=None, max_length=200000, description="技能正文")
    content: str | None = Field(default=None, max_length=512000, description="完整 SKILL.md 文本")
    scope: str = Field(default="global", description="作用域：global / project_set / local")
    project_name: str | None = Field(default=None, max_length=256, description="项目集合名或项目名")
    local_folder: str | None = Field(default=None, max_length=1024, description="本地作用域的直接文件夹路径（无需项目绑定）")


class DashboardSkillUpdateRequest(BaseModel):
    """更新技能的请求体。

    未提供的字段沿用原值，正文与 frontmatter 由后端重新组装。

    Attributes:
        name: 新的技能标识。
        description: 新的技能说明。
        body: 新的正文。
        content: 完整 SKILL.md 文本，提供时覆盖 name / description / body。
        scope: 目标作用域。
        project_name: 项目级技能所属项目名。
    """

    name: str | None = Field(default=None, max_length=128, description="技能标识")
    description: str | None = Field(default=None, max_length=1024, description="技能说明")
    body: str | None = Field(default=None, max_length=200000, description="技能正文")
    content: str | None = Field(default=None, max_length=512000, description="完整 SKILL.md 文本")
    scope: str = Field(default="global", description="作用域：global / project_set / local")
    project_name: str | None = Field(default=None, max_length=256, description="项目集合名或项目名")
