"""工具入参清洗中间件。

模型生成工具调用参数时，常见问题包括：
1. 把 Windows 绝对路径直接传给文件工具；
2. 使用 ``..`` 访问工作区外路径；
3. 访问 ``.secrets`` 等敏感目录；
4. 把带 token 的 SCM URL 传入工具；
5. 把本应为整数的 offset/limit 生成为字符串。

本模块在工具真正执行前统一清洗这些高风险参数。
LocalShellBackend 仍然是最终安全边界；middleware 的职责是提前给模型返回更明确的
中文反馈，减少无效重试，并避免把明显危险的参数送入工具实现。
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable
from pathlib import Path, PureWindowsPath
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from backends.local_shell import LocalShellBackend
from agent.runtime.events import record_event
from config.constants import (
    CODEUP_URL_ARGUMENTS,
    GITEE_URL_ARGUMENTS,
    GITHUB_URL_ARGUMENTS,
    PATH_ARGUMENTS,
    READ_FILE_INT_ARGUMENTS,
)
from utils.codeup_utils import normalize_codeup_repo_url
from utils.gitee_utils import mask_token, normalize_gitee_repo_url
from utils.github_utils import normalize_github_repo_url

logger = logging.getLogger(__name__)


class ToolInputRejected(ValueError):
    """工具入参被 middleware 拒绝。

    这是"可恢复错误"，不是系统异常。返回给模型后，模型应改用工作区相对路径、
    正确仓库地址或更安全的命令重新调用工具。

    异常消息包含被拒绝的参数名和具体原因，供模型读取并修正。
    """


def _reject_result(error: ToolInputRejected, *, tool_name: str, backend: LocalShellBackend) -> dict[str, Any]:
    """把入参拦截结果转换成模型可读的中文结构，序列化为 ToolMessage content。

    Args:
        error: 入参被拒绝时抛出的 ToolInputRejected 异常。
        tool_name: 被拒绝的工具名称。
        backend: 当前工作区后端，用于提供 workspace 路径信息。

    Returns:
        包含 ok、tool、error_type、error、workspace、hint 的结构化字典。
    """
    return {
        "ok": False,
        "tool": tool_name,
        "error_type": "ToolInputRejected",
        "error": str(error),
        "workspace": str(backend.workspace.root),
        "hint": "请改用工作区内相对路径，例如 '.'、'projects' 或 'projects/仓库名'；不要访问 .secrets。仓库地址请使用标准 HTTPS 地址。",
    }


def _get_tool_call_id(request: ToolCallRequest) -> str | None:
    """兼容读取 LangGraph tool_call id。

    Args:
        request: LangGraph 工具调用请求对象。

    Returns:
        tool_call_id 字符串；无法提取时返回 ``None``。
    """
    if isinstance(request.tool_call, dict):
        value = request.tool_call.get("id")
        return value if isinstance(value, str) else None
    return None


def _get_thread_id(request: ToolCallRequest) -> str | None:
    """从 runtime config 中读取 thread_id，用于写入前端运行事件。

    Args:
        request: LangGraph 工具调用请求对象。

    Returns:
        当前线程 ID；无法提取或为空时返回 ``None``。
    """
    config = getattr(getattr(request, "runtime", None), "config", None)
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    return thread_id if isinstance(thread_id, str) and thread_id else None


def _coerce_int(value: Any) -> Any:
    """把模型偶尔生成的字符串参数修正为整数。

    只处理纯数字开头的字符串，其他类型原样返回。

    Args:
        value: 待转换的参数值。

    Returns:
        转换后的整数；无法转换时返回原始值。
    """
    if value is None or isinstance(value, int):
        return value
    if isinstance(value, str):
        match = re.match(r"\s*(\d+)", value)
        if match:
            return int(match.group(1))
    return value


def _reject_tool_message(
    error: ToolInputRejected,
    *,
    request: ToolCallRequest,
    tool_name: str,
    backend: LocalShellBackend,
) -> ToolMessage:
    """把参数拦截结果转换为 ToolMessage，让 Agent 可以继续修正。

    Args:
        error: 入参被拒绝时抛出的 ToolInputRejected 异常。
        request: LangGraph 工具调用请求对象，用于提取 tool_call_id。
        tool_name: 被拒绝的工具名称。
        backend: 当前工作区后端。

    Returns:
        包含结构化错误信息的 ToolMessage（status='error'）。
    """
    return ToolMessage(
        content=json.dumps(
            _reject_result(error, tool_name=tool_name, backend=backend),
            ensure_ascii=False,
        ),
        tool_call_id=_get_tool_call_id(request),
        status="error",
    )


def _is_windows_absolute_path(value: str) -> bool:
    """判断字符串是否像 Windows 绝对路径。

    同时检查正则匹配和 ``PureWindowsPath`` 解析结果，确保覆盖各种格式。

    Args:
        value: 待检查的路径字符串。

    Returns:
        ``True`` 表示是 Windows 绝对路径。
    """
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return True
    try:
        return PureWindowsPath(value).is_absolute() and bool(PureWindowsPath(value).drive)
    except Exception:
        return False


def sanitize_workspace_path(value: Any, *, argument_name: str, backend: LocalShellBackend) -> Any:
    """清洗路径类参数。

    后端 ``Workspace.resolve()`` 仍是最终安全边界。middleware 只在调用前做更友好的
    规范化和敏感目录拦截。

    处理规则：
    1. Windows 绝对路径在工作区内时转为相对路径，在工作区外时拒绝；
    2. 包含 ``..`` 的路径直接拒绝，防止跳出工作区；
    3. 包含 ``.secrets`` 的路径直接拒绝，防止访问敏感文件。

    Args:
        value: 待清洗的路径参数值。
        argument_name: 参数名称，用于错误提示。
        backend: 当前工作区后端。

    Returns:
        清洗后的路径字符串。

    Raises:
        ToolInputRejected: 路径越界或包含敏感目录时。
    """
    if not isinstance(value, str):
        return value
    cleaned = value.strip().strip('"').strip("'").replace("\\", "/")
    if not cleaned:
        return cleaned
    if _is_windows_absolute_path(cleaned):
        resolved_root = backend.workspace.root.resolve()
        resolved_path = Path(cleaned).resolve()
        if resolved_path == resolved_root:
            return "."
        if resolved_root in resolved_path.parents:
            return resolved_path.relative_to(resolved_root).as_posix()
        raise ToolInputRejected(f"{argument_name} 不能使用工作区外的 Windows 绝对路径：{cleaned}")
    parts = [part for part in cleaned.split("/") if part not in {"", "."}]
    if ".secrets" in parts:
        raise ToolInputRejected(f"{argument_name} 禁止访问敏感目录 .secrets")
    if ".." in parts:
        raise ToolInputRejected(f"{argument_name} 禁止使用 '..' 跳出工作区：{cleaned}")
    return cleaned or "."


def _sanitize_gitee_url(value: Any) -> Any:
    """把 Gitee 地址规范为不带 token 的标准 HTTPS clone_url。

    非 Gitee 域名或非字符串值原样返回。

    Args:
        value: 待规范的仓库地址。

    Returns:
        规范后的 Gitee HTTPS 地址；非 Gitee 地址或非字符串时返回原始值。
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return text
    if "gitee.com" not in text.lower():
        return text
    return normalize_gitee_repo_url(text)


def _sanitize_github_url(value: Any) -> Any:
    """把 GitHub 地址规范为不带 token 的标准 HTTPS clone_url。

    非 GitHub 域名或非字符串值原样返回。

    Args:
        value: 待规范的仓库地址。

    Returns:
        规范后的 GitHub HTTPS 地址；非 GitHub 地址或非字符串时返回原始值。
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return text
    if "github.com" not in text.lower():
        return text
    return normalize_github_repo_url(text)


def _sanitize_codeup_url(value: Any) -> Any:
    """把 Codeup 地址规范为不带 token 的标准 HTTPS clone_url。

    非 Codeup 域名或非字符串值原样返回。

    Args:
        value: 待规范的仓库地址。

    Returns:
        规范后的 Codeup HTTPS 地址；非 Codeup 地址或非字符串时返回原始值。
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return text
    if "codeup.aliyun.com" not in text.lower():
        return text
    return normalize_codeup_repo_url(text)


def sanitize_tool_kwargs(tool_name: str, kwargs: dict[str, Any], *, backend: LocalShellBackend) -> dict[str, Any]:
    """根据参数名对工具入参做统一清洗。

    只处理所有工具共享的高风险参数：路径、SCM URL、整型参数。

    Args:
        tool_name: 工具名称，用于日志输出。
        kwargs: 原始工具调用参数。
        backend: 当前工作区后端。

    Returns:
        清洗后的参数字典。
    """
    sanitized = dict(kwargs)
    for key in PATH_ARGUMENTS:
        if key in sanitized:
            sanitized[key] = sanitize_workspace_path(sanitized[key], argument_name=key, backend=backend)
    for key in GITEE_URL_ARGUMENTS:
        if key in sanitized:
            sanitized[key] = _sanitize_gitee_url(sanitized[key])
    for key in GITHUB_URL_ARGUMENTS:
        if key in sanitized:
            sanitized[key] = _sanitize_github_url(sanitized[key])
    for key in CODEUP_URL_ARGUMENTS:
        if key in sanitized:
            sanitized[key] = _sanitize_codeup_url(sanitized[key])
    for key in READ_FILE_INT_ARGUMENTS:
        if key in sanitized:
            sanitized[key] = _coerce_int(sanitized[key])
    logger.debug("工具入参清洗完成：tool=%s keys=%s", tool_name, sorted(sanitized))
    return sanitized


class SanitizeToolInputsMiddleware(AgentMiddleware):
    """工具入参清洗中间件。

    运行在 DeepAgents 工具调用生命周期里，能同时覆盖自定义 SCM 工具
    和 DeepAgents 原生的文件/命令工具。LocalShellBackend 仍是最终安全边界，
    这里主要负责把常见错误参数转换成 Agent 可恢复的中文反馈。
    """

    state_schema = AgentState

    def __init__(self, *, backend: LocalShellBackend) -> None:
        """初始化工具入参清洗中间件。

        Args:
            backend: 当前工作区后端实例，用于路径解析和 workspace 信息。
        """
        super().__init__()
        self.backend = backend

    def _sanitize_request(self, request: ToolCallRequest) -> ToolCallRequest:
        """返回清洗后的 ToolCallRequest。

        对 request 中的工具参数执行统一清洗；如果 tool_call 不是 dict 或
        args 不是 dict，原样返回。

        Args:
            request: LangGraph 工具调用请求对象。

        Returns:
            参数已清洗的请求副本；无法解析时返回原始对象。
        """
        tool_call = request.tool_call
        if not isinstance(tool_call, dict):
            return request
        tool_name = str(tool_call.get("name") or "")
        args = tool_call.get("args", {})
        if not isinstance(args, dict):
            return request
        sanitized_args = sanitize_tool_kwargs(tool_name, args, backend=self.backend)
        return request.override(tool_call={**tool_call, "args": sanitized_args})

    def _record_rejection(self, request: ToolCallRequest, tool_name: str, error: ToolInputRejected) -> None:
        """把参数拒绝事件写入运行事件表。

        Args:
            request: LangGraph 工具调用请求对象，用于提取 thread_id。
            tool_name: 被拒绝的工具名称。
            error: 入参拒绝异常。
        """
        thread_id = _get_thread_id(request)
        if not thread_id:
            return
        record_event(
            thread_id,
            f"tool-sanitize:{tool_name}",
            f"参数被拦截：{tool_name}",
            kind="other",
            status="error",
            detail=json.dumps({"tool": tool_name, "error": mask_token(str(error))}, ensure_ascii=False),
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """同步工具调用包装器，先清洗参数再交给下游 handler。

        Args:
            request: LangGraph 工具调用请求对象。
            handler: 下游工具调用处理函数。

        Returns:
            下游返回的正常结果；或入参被拒绝时返回的错误 ToolMessage。
        """
        tool_name = str(request.tool_call.get("name") or "") if isinstance(request.tool_call, dict) else ""
        try:
            return handler(self._sanitize_request(request))
        except ToolInputRejected as exc:
            logger.warning("工具入参被拒绝：tool=%s error=%s", tool_name, mask_token(str(exc)))
            self._record_rejection(request, tool_name, exc)
            return _reject_tool_message(exc, request=request, tool_name=tool_name, backend=self.backend)

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        """异步工具调用包装器，先清洗参数再交给下游 handler。

        Args:
            request: LangGraph 工具调用请求对象。
            handler: 下游异步工具调用处理函数。

        Returns:
            下游返回的正常结果；或入参被拒绝时返回的错误 ToolMessage。
        """
        tool_name = str(request.tool_call.get("name") or "") if isinstance(request.tool_call, dict) else ""
        try:
            return await handler(self._sanitize_request(request))
        except ToolInputRejected as exc:
            logger.warning("工具入参被拒绝：tool=%s error=%s", tool_name, mask_token(str(exc)))
            self._record_rejection(request, tool_name, exc)
            return _reject_tool_message(exc, request=request, tool_name=tool_name, backend=self.backend)
