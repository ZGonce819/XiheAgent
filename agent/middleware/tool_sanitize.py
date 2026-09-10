"""工具入参清洗中间件。

模型生成工具调用参数时，常见问题包括：
1. 把 Windows 绝对路径直接传给文件工具；
2. 使用 ``..`` 访问工作区外路径；
3. 访问 ``.secrets`` 等敏感目录；
4. 把带 token 的 Gitee URL 传入工具；
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
from utils.gitee_utils import mask_token, normalize_gitee_repo_url
from utils.github_utils import normalize_github_repo_url

logger = logging.getLogger(__name__)

PATH_ARGUMENTS = {"path", "cwd", "repo_dir", "project_dir", "file_path", "old_path", "new_path"}
GITEE_URL_ARGUMENTS = {"repo_url"}
GITHUB_URL_ARGUMENTS = {"repo_url"}
READ_FILE_INT_ARGUMENTS = {"offset", "limit"}


class ToolInputRejected(ValueError):
    """工具入参被 middleware 拒绝。

    这是"可恢复错误"，不是系统异常。返回给模型后，模型应改用工作区相对路径、
    正确 Gitee 地址或更安全的命令重新调用工具。
    """


def _reject_result(error: ToolInputRejected, *, tool_name: str, backend: LocalShellBackend) -> dict[str, Any]:
    """把入参拦截结果转换成模型可读的中文结构，序列化为 ToolMessage content。"""
    return {
        "ok": False,
        "tool": tool_name,
        "error_type": "ToolInputRejected",
        "error": str(error),
        "workspace": str(backend.workspace.root),
        "hint": "请改用工作区内相对路径，例如 '.'、'projects' 或 'projects/仓库名'；不要访问 .secrets。仓库地址请使用标准 Gitee HTTPS 地址。",
    }


def _get_tool_call_id(request: ToolCallRequest) -> str | None:
    """兼容读取 LangGraph tool_call id。"""
    if isinstance(request.tool_call, dict):
        value = request.tool_call.get("id")
        return value if isinstance(value, str) else None
    return None


def _get_thread_id(request: ToolCallRequest) -> str | None:
    """从 runtime config 中读取 thread_id，用于写入前端运行事件。"""
    config = getattr(getattr(request, "runtime", None), "config", None)
    configurable = config.get("configurable", {}) if isinstance(config, dict) else {}
    thread_id = configurable.get("thread_id") if isinstance(configurable, dict) else None
    return thread_id if isinstance(thread_id, str) and thread_id else None


def _coerce_int(value: Any) -> Any:
    """把模型偶尔生成的字符串参数修正为整数。"""
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
    """把参数拦截结果转换为 ToolMessage，让 Agent 可以继续修正。"""
    return ToolMessage(
        content=json.dumps(
            _reject_result(error, tool_name=tool_name, backend=backend),
            ensure_ascii=False,
        ),
        tool_call_id=_get_tool_call_id(request),
        status="error",
    )


def _is_windows_absolute_path(value: str) -> bool:
    """判断字符串是否像 Windows 绝对路径。"""
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
    """把 Gitee 地址规范为不带 token 的标准 HTTPS clone_url。"""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return text
    if "gitee.com" not in text.lower():
        return text
    return normalize_gitee_repo_url(text)


def _sanitize_github_url(value: Any) -> Any:
    """把 GitHub 地址规范为不带 token 的标准 HTTPS clone_url。"""
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return text
    if "github.com" not in text.lower():
        return text
    return normalize_github_repo_url(text)


def sanitize_tool_kwargs(tool_name: str, kwargs: dict[str, Any], *, backend: LocalShellBackend) -> dict[str, Any]:
    """根据参数名对工具入参做统一清洗。

    只处理所有工具共享的高风险参数：路径、Gitee URL、GitHub URL。
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
    for key in READ_FILE_INT_ARGUMENTS:
        if key in sanitized:
            sanitized[key] = _coerce_int(sanitized[key])
    logger.debug("工具入参清洗完成：tool=%s keys=%s", tool_name, sorted(sanitized))
    return sanitized


class SanitizeToolInputsMiddleware(AgentMiddleware):
    """工具入参清洗中间件。

    运行在 DeepAgents 工具调用生命周期里，能同时覆盖自定义 Gitee 工具
    和 DeepAgents 原生的文件/命令工具。LocalShellBackend 仍是最终安全边界，
    这里主要负责把常见错误参数转换成 Agent 可恢复的中文反馈。
    """

    state_schema = AgentState

    def __init__(self, *, backend: LocalShellBackend) -> None:
        super().__init__()
        self.backend = backend

    def _sanitize_request(self, request: ToolCallRequest) -> ToolCallRequest:
        """返回清洗后的 ToolCallRequest。"""
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
        """把参数拒绝事件写入运行事件表。"""
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
        tool_name = str(request.tool_call.get("name") or "") if isinstance(request.tool_call, dict) else ""
        try:
            return await handler(self._sanitize_request(request))
        except ToolInputRejected as exc:
            logger.warning("工具入参被拒绝：tool=%s error=%s", tool_name, mask_token(str(exc)))
            self._record_rejection(request, tool_name, exc)
            return _reject_tool_message(exc, request=request, tool_name=tool_name, backend=self.backend)
