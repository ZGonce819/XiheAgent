"""工具异常处理中间件。

运行在 LangGraph 的工具调用环节，捕获所有工具函数抛出的异常，并将其转换为
"模型可读"的结构化错误 ToolMessage。这样模型可以把错误当作观察结果继续推理，
不会因为某个文件工具路径错误或网络请求超时而直接终止整轮 Agent 任务。

为什么要单独做这个：
  1. LangGraph 默认行为：工具抛出异常 → 工具节点报错 → 整轮任务 fail。
     在 FastAPI 后台任务场景里，任务 fail 后前端只能看到"任务失败"，
     用户和模型都无法知道原因、无法自行修正。
  2. 本中间件拦截后返回 ``status="error"`` 的 ToolMessage，content 里包含：
     error_type（异常类名）、error（脱敏后的异常文本）、hint（中文修正建议）、
     workspace（当前工作区路径）。模型读到后可以判断是否路径写错了、是否需要
     先 ls 再读写、是否需要切换命令再试，从而实现一定程度的自主恢复。
  3. 除了返回 ToolMessage，还会把工具失败状态写回前端 run_events 表，
     避免前端步骤长时间停留在 in_progress 的"加载中"状态。

配合关系：
  - ``tool_sanitize.py`` 在工具执行前清洗参数，减少可预防性异常。
  - 本中间件是兜底防线，处理所有未被前置检查挡住的运行时异常。
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.config import get_config
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from backends.local_shell import LocalShellBackend
from backends.permissions import WorkspacePermissionError
from agent.runtime.events import record_event
from utils.gitee_utils import mask_token

logger = logging.getLogger(__name__)


def tool_error_result(
    error: Exception,
    *,
    tool_name: str,
    backend: LocalShellBackend,
) -> dict[str, Any]:
    """把未捕获的工具异常转换为结构化结果字典，供 ToolMessage 序列化。

    返回字典包含 ok、tool、error_type、error、workspace、hint 字段，
    模型可以通过读取 content 的 JSON 了解失败原因。

    Args:
        error: 工具执行过程中抛出的异常。
        tool_name: 失败的工具名称。
        backend: 当前工作区后端，用于提供 workspace 路径信息。

    Returns:
        包含 ok、tool、error_type、error、workspace、hint 的结构化字典。
    """
    error_text = mask_token(str(error))

    if isinstance(error, WorkspacePermissionError):
        hint = "只能访问工作区内路径。请使用 '/projects' 或 '/projects/仓库名' 这样的虚拟路径。"
    elif isinstance(error, IsADirectoryError):
        hint = "当前路径是目录。请先调用 ls 查看目录内容，再对具体文件调用 read_file 或 write_file。"
    elif isinstance(error, FileNotFoundError):
        hint = "文件不存在。请先调用 ls 确认真实路径，再继续操作。"
    elif isinstance(error, PermissionError):
        hint = "文件系统拒绝访问。请确认路径不是目录、没有被其他程序占用，并改用工作区内具体文件路径。"
    elif isinstance(error, TimeoutError):
        hint = "操作超时。请缩小任务范围，或先用更小的命令检查项目状态。"
    else:
        hint = "工具执行失败。请根据 error 字段调整参数，必要时先读取目录、文件或 Git 状态再重试。"

    return {
        "ok": False,
        "tool": tool_name,
        "error_type": error.__class__.__name__,
        "error": error_text,
        "workspace": str(backend.workspace.root),
        "hint": hint,
    }


def _get_tool_call_id(request: ToolCallRequest) -> str | None:
    """从 ToolCallRequest 中提取 tool_call_id。

    ToolMessage 必须绑定 tool_call_id 才能与对应的工具调用配对。

    Args:
        request: LangGraph 工具调用请求对象。

    Returns:
        tool_call_id 字符串；无法提取时返回 ``None``。
    """
    if isinstance(request.tool_call, dict):
        value = request.tool_call.get("id")
        return value if isinstance(value, str) else None
    return None


def _get_tool_name(request: ToolCallRequest) -> str:
    """从 ToolCallRequest 中提取工具名。

    Args:
        request: LangGraph 工具调用请求对象。

    Returns:
        工具名称；读取失败时返回 ``"unknown_tool"``。
    """
    if isinstance(request.tool_call, dict):
        name = request.tool_call.get("name")
        return name if isinstance(name, str) and name else "unknown_tool"
    return "unknown_tool"


def _get_tool_args(request: ToolCallRequest) -> dict[str, Any]:
    """从 ToolCallRequest 中提取工具调用参数。

    Args:
        request: LangGraph 工具调用请求对象。

    Returns:
        工具参数字典；无法提取时返回空字典。
    """
    if isinstance(request.tool_call, dict):
        args = request.tool_call.get("args", {})
        return args if isinstance(args, dict) else {}
    return {}


def _get_thread_id() -> str | None:
    """通过 LangGraph 运行时配置读取当前线程的 thread_id。

    Returns:
        当前线程 ID；未配置或为空时返回 ``None``。
    """
    configurable = get_config().get("configurable", {})
    if not isinstance(configurable, dict):
        return None
    thread_id = configurable.get("thread_id")
    return thread_id if isinstance(thread_id, str) and thread_id else None


def _error_tool_message(
    error: Exception,
    *,
    request: ToolCallRequest,
    tool_name: str,
    backend: LocalShellBackend,
) -> ToolMessage:
    """把工具异常转换为结构化的 ToolMessage（status='error'）。

    Args:
        error: 工具执行过程中抛出的异常。
        request: LangGraph 工具调用请求对象，用于提取 tool_call_id。
        tool_name: 失败的工具名称。
        backend: 当前工作区后端。

    Returns:
        包含结构化错误信息的 ToolMessage。
    """
    return ToolMessage(
        content=json.dumps(
            tool_error_result(error, tool_name=tool_name, backend=backend),
            ensure_ascii=False,
        ),
        tool_call_id=_get_tool_call_id(request),
        status="error",
    )


def _record_original_tool_error(
    thread_id: str,
    *,
    tool_name: str,
    kwargs: dict[str, Any],
    error: Exception,
) -> None:
    """把工具失败状态写回运行事件表中该工具原步骤的 key。

    每个工具在真正执行前会先写入一条 in_progress 事件。工具执行过程中一旦
    抛出异常，需要反向查找工具名对应的原步骤 key，把那条事件的 status 改为 error，
    避免前端步骤永远停留在"运行中"。

    Args:
        thread_id: 当前线程 ID。
        tool_name: 失败的工具名称。
        kwargs: 工具调用时的原始参数。
        error: 工具抛出的异常。
    """
    error_detail = mask_token(str(error))

    mapping = {
        "ls": ("list:{path}", "查看目录", "search"),
        "read_file": ("read:{path}", "读取文件", "read"),
        "write_file": ("write:{path}", "写入文件", "edit"),
        "edit_file": ("write:{file_path}", "修改文件", "edit"),
        "execute": ("cmd:{command}", "执行命令", "execute"),
        "list_files": ("list:{path}", "查看目录", "search"),
        "run_command": ("cmd:{command}:{cwd}", "执行命令", "execute"),
        "open_gitee_pull_request": ("gitee:pr", "创建或复用 Pull Request", "fetch"),
        "publish_gitee_pr_comment": ("gitee:comment", "发布 PR 评论", "fetch"),
        "get_gitee_pull_request_context": ("gitee:pr-context", "读取 PR 审查上下文", "fetch"),
        "open_github_pull_request": ("github:pr", "创建或复用 GitHub Pull Request", "fetch"),
        "publish_github_pr_comment": ("github:comment", "发布 GitHub PR 评论", "fetch"),
        "get_github_pull_request_context": ("github:pr-context", "读取 GitHub PR 审查上下文", "fetch"),
        "open_codeup_merge_request": ("codeup:mr", "创建或复用 Merge Request", "fetch"),
        "publish_codeup_mr_comment": ("codeup:comment", "发布 MR 评论", "fetch"),
        "get_codeup_merge_request_context": ("codeup:mr-context", "读取 MR 审查上下文", "fetch"),
        "web_search": ("web:search:{query}", "搜索网络", "search"),
        "fetch_url": ("fetch:{url}", "抓取网页", "fetch"),
        "load_default_review_rules": ("review:rules", "读取默认审查规则", "read"),
        "get_review_diff_summary": ("review:diff:{repo_dir}", "读取审查 diff", "read"),
        "validate_review_finding_location": ("review:validate:{file}", "校验审查位置", "other"),
        "add_review_finding": ("review:add:{file}", "记录审查发现", "other"),
        "list_review_findings": ("review:list", "列出审查发现", "other"),
    }
    item = mapping.get(tool_name)
    if not item:
        return

    key_template, title, kind = item
    try:
        key = key_template.format(**{"cwd": ".", **kwargs})
    except Exception:
        key = f"tool:{tool_name}"
    record_event(
        thread_id,
        key,
        title,
        kind=kind,
        status="error",
        detail=json.dumps(
            {
                "tool": tool_name,
                "args": {name: mask_token(str(value)) for name, value in kwargs.items()},
                "error": error_detail,
            },
            ensure_ascii=False,
        ),
    )


class ToolErrorMiddleware(AgentMiddleware):
    """工具异常处理中间件。

    包装 LangGraph 工具节点的工具调用过程，捕获所有 throw 的异常，
    并返回 ``status="error"`` 的 ToolMessage，把错误信息以结构化 JSON 形式
    交还给模型。

    和 ``SanitizeToolInputsMiddleware`` 的关系：
    后者在工具调用前清洗参数，阻挡明显非法的工具调用；本中间件作为第二道防线，
    捕获运行时异常（文件不存在、网络超时、权限拒绝等）。
    """

    state_schema = AgentState

    def __init__(self, *, backend: LocalShellBackend) -> None:
        """初始化工具异常处理中间件。

        Args:
            backend: 当前工作区后端实例，用于在错误结果中提供 workspace 路径。
        """
        super().__init__()
        self.backend = backend

    def _record_error(self, request: ToolCallRequest, tool_name: str, error: Exception) -> None:
        """记录工具异常事件到运行事件表。

        同时做两件事：把原工具步骤从 in_progress 更新为 error，以及新增一条
        通用 tool-error 事件作为兜底记录。

        Args:
            request: LangGraph 工具调用请求对象，用于提取工具参数。
            tool_name: 失败的工具名称。
            error: 工具抛出的异常。
        """
        thread_id = _get_thread_id()
        if not thread_id:
            return
        kwargs = _get_tool_args(request)
        _record_original_tool_error(thread_id, tool_name=tool_name, kwargs=kwargs, error=error)
        record_event(
            thread_id,
            f"tool-error:{tool_name}",
            f"工具失败：{tool_name}",
            kind="other",
            status="error",
            detail=json.dumps(
                {
                    "tool": tool_name,
                    "error_type": error.__class__.__name__,
                    "error": mask_token(str(error)),
                },
                ensure_ascii=False,
            ),
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """同步工具调用包装器，捕获异常并返回结构化错误 ToolMessage。

        Args:
            request: LangGraph 工具调用请求对象。
            handler: 下游工具调用处理函数。

        Returns:
            下游返回的正常结果；或异常被捕获后返回的错误 ToolMessage。
        """
        tool_name = _get_tool_name(request)
        try:
            return handler(request)
        except Exception as exc:  # noqa: BLE001
            logger.warning("工具执行异常已被中间件捕获：tool=%s error=%s", tool_name, mask_token(str(exc)))
            logger.debug("工具异常调试栈：tool=%s", tool_name, exc_info=True)
            self._record_error(request, tool_name, exc)
            return _error_tool_message(exc, request=request, tool_name=tool_name, backend=self.backend)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        """异步工具调用包装器，捕获异常并返回结构化错误 ToolMessage。

        Args:
            request: LangGraph 工具调用请求对象。
            handler: 下游异步工具调用处理函数。

        Returns:
            下游返回的正常结果；或异常被捕获后返回的错误 ToolMessage。
        """
        tool_name = _get_tool_name(request)
        try:
            return await handler(request)
        except Exception as exc:  # noqa: BLE001
            logger.warning("工具执行异常已被中间件捕获：tool=%s error=%s", tool_name, mask_token(str(exc)))
            logger.debug("工具异常调试栈：tool=%s", tool_name, exc_info=True)
            self._record_error(request, tool_name, exc)
            return _error_tool_message(exc, request=request, tool_name=tool_name, backend=self.backend)
