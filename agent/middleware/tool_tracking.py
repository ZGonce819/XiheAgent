"""工具执行追踪中间件。

在工具执行前后记录事件，让前端能实时展示工具执行步骤。
这是实现"算一下... 1.4s"和可折叠工具步骤展示的关键。
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from langchain.agents.middleware.types import AgentMiddleware, AgentState
from langchain_core.messages import ToolMessage
from langgraph.prebuilt.tool_node import ToolCallRequest
from langgraph.types import Command

from agent.runtime.events import record_event
from backends.local_shell import LocalShellBackend
from utils.gitee_utils import mask_token

logger = logging.getLogger(__name__)


def _get_tool_call_id(request: ToolCallRequest) -> str | None:
    """从 ToolCallRequest 中提取 tool_call_id。"""
    if isinstance(request.tool_call, dict):
        value = request.tool_call.get("id")
        return value if isinstance(value, str) else None
    return None


def _get_tool_name(request: ToolCallRequest) -> str:
    """从 ToolCallRequest 中提取工具名。"""
    if isinstance(request.tool_call, dict):
        name = request.tool_call.get("name")
        return name if isinstance(name, str) and name else "unknown_tool"
    return "unknown_tool"


def _get_tool_args(request: ToolCallRequest) -> dict[str, Any]:
    """从 ToolCallRequest 中提取工具调用参数。"""
    if isinstance(request.tool_call, dict):
        args = request.tool_call.get("args", {})
        return args if isinstance(args, dict) else {}
    return {}


def _get_thread_id() -> str | None:
    """通过 LangGraph 运行时配置读取当前线程的 thread_id。"""
    from langgraph.config import get_config
    configurable = get_config().get("configurable", {})
    if not isinstance(configurable, dict):
        return None
    thread_id = configurable.get("thread_id")
    return thread_id if isinstance(thread_id, str) and thread_id else None


def _safe_detail_value(value: Any) -> Any:
    """保留工具参数结构，同时遮蔽其中可能出现的访问令牌。"""
    if isinstance(value, dict):
        return {str(key): _safe_detail_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_detail_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return mask_token(str(value))


def _safe_detail_args(args: dict[str, Any]) -> dict[str, Any]:
    """生成可安全发送给前端的结构化工具参数。"""
    return {str(key): _safe_detail_value(value) for key, value in args.items()}


def _map_tool_to_kind(tool_name: str) -> str:
    """根据工具名映射到前端展示的 toolKind。"""
    mapping = {
        "ls": "search",
        "list_files": "search",
        "read_file": "read",
        "write_file": "edit",
        "edit_file": "edit",
        "execute": "execute",
        "run_command": "execute",
        "web_search": "search",
        "fetch_url": "fetch",
        "open_gitee_pull_request": "fetch",
        "publish_gitee_pr_comment": "fetch",
        "get_gitee_pull_request_context": "fetch",
        "open_github_pull_request": "fetch",
        "publish_github_pr_comment": "fetch",
        "get_github_pull_request_context": "fetch",
        "open_codeup_merge_request": "fetch",
        "publish_codeup_mr_comment": "fetch",
        "get_codeup_merge_request_context": "fetch",
        "load_default_review_rules": "read",
        "get_review_diff_summary": "read",
        "validate_review_finding_location": "other",
        "add_review_finding": "other",
        "list_review_findings": "other",
    }
    return mapping.get(tool_name, "other")


def _build_tool_title(tool_name: str, args: dict[str, Any]) -> str:
    """根据工具名和参数生成前端展示的标题。"""
    if tool_name in ("ls", "list_files"):
        path = args.get("path", ".")
        return f"查看目录({path})"
    elif tool_name == "read_file":
        path = str(args.get("path") or args.get("file_path") or "")
        file_name = path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        return f"读取文件({file_name})" if file_name else "读取文件"
    elif tool_name in ("write_file", "edit_file"):
        path = args.get("path") or args.get("file_path", "")
        return f"编辑文件({path})"
    elif tool_name in ("execute", "run_command"):
        command = args.get("command", "")
        if len(command) > 50:
            command = command[:50] + "..."
        return f"执行命令({command})"
    elif tool_name == "web_search":
        query = args.get("query", "")
        if len(query) > 40:
            query = query[:40] + "..."
        return f"搜索({query})"
    elif tool_name == "fetch_url":
        url = args.get("url", "")
        if len(url) > 50:
            url = url[:50] + "..."
        return f"抓取网页({url})"
    elif "pr" in tool_name or "mr" in tool_name:
        return f"SCM操作({tool_name})"
    else:
        return tool_name


class ToolTrackingMiddleware(AgentMiddleware):
    """工具执行追踪中间件。

    在工具执行前后记录事件，通过 SSE 实时推送到前端，
    实现工具执行步骤的可视化展示。
    """

    state_schema = AgentState

    def __init__(self, *, backend: LocalShellBackend) -> None:
        super().__init__()
        self.backend = backend

    def _build_event_key(self, tool_name: str, tool_call_id: str | None) -> str:
        """构建稳定的事件 key，让 start 和 end 事件能被前端匹配。"""
        if tool_call_id:
            return f"tool:{tool_call_id}"
        return f"tool:{tool_name}"

    def _record_tool_start(
        self,
        thread_id: str,
        *,
        tool_name: str,
        tool_call_id: str | None,
        args: dict[str, Any],
    ) -> None:
        """记录工具开始执行。"""
        kind = _map_tool_to_kind(tool_name)
        title = _build_tool_title(tool_name, args)
        key = self._build_event_key(tool_name, tool_call_id)
        
        # 构建 detail，包含工具输入参数
        detail = json.dumps({
            "tool": tool_name,
            "args": _safe_detail_args(args),
        }, ensure_ascii=False)
        
        record_event(
            thread_id,
            key,
            title,
            kind=kind,
            status="in_progress",
            detail=detail,
        )

    def _record_tool_end(
        self,
        thread_id: str,
        *,
        key: str,
        tool_name: str,
        args: dict[str, Any],
        elapsed_ms: int,
        success: bool,
    ) -> None:
        """记录工具执行完成。"""
        kind = _map_tool_to_kind(tool_name)
        title = _build_tool_title(tool_name, args)
        status = "completed" if success else "error"
        
        record_event(
            thread_id,
            key,
            title,
            kind=kind,
            status=status,
            detail=json.dumps({
                "tool": tool_name,
                "args": _safe_detail_args(args),
                "elapsed_ms": elapsed_ms,
            }, ensure_ascii=False),
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """同步工具调用包装器，记录执行时间。"""
        thread_id = _get_thread_id()
        if not thread_id:
            return handler(request)
        
        tool_name = _get_tool_name(request)
        tool_call_id = _get_tool_call_id(request)
        args = _get_tool_args(request)
        key = self._build_event_key(tool_name, tool_call_id)
        
        self._record_tool_start(
            thread_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            args=args,
        )
        
        start_time = time.time()
        try:
            result = handler(request)
            elapsed_ms = int((time.time() - start_time) * 1000)
            
            self._record_tool_end(
                thread_id,
                key=key,
                tool_name=tool_name,
                args=args,
                elapsed_ms=elapsed_ms,
                success=True,
            )
            
            return result
        except Exception:
            elapsed_ms = int((time.time() - start_time) * 1000)
            
            self._record_tool_end(
                thread_id,
                key=key,
                tool_name=tool_name,
                args=args,
                elapsed_ms=elapsed_ms,
                success=False,
            )
            
            raise

    async def awrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], Awaitable[ToolMessage | Command]],
    ) -> ToolMessage | Command:
        """异步工具调用包装器，记录执行时间。"""
        thread_id = _get_thread_id()
        if not thread_id:
            return await handler(request)
        
        tool_name = _get_tool_name(request)
        tool_call_id = _get_tool_call_id(request)
        args = _get_tool_args(request)
        key = self._build_event_key(tool_name, tool_call_id)
        
        self._record_tool_start(
            thread_id,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            args=args,
        )
        
        start_time = time.time()
        try:
            result = await handler(request)
            elapsed_ms = int((time.time() - start_time) * 1000)
            
            self._record_tool_end(
                thread_id,
                key=key,
                tool_name=tool_name,
                args=args,
                elapsed_ms=elapsed_ms,
                success=True,
            )
            
            return result
        except Exception:
            elapsed_ms = int((time.time() - start_time) * 1000)
            
            self._record_tool_end(
                thread_id,
                key=key,
                tool_name=tool_name,
                args=args,
                elapsed_ms=elapsed_ms,
                success=False,
            )
            
            raise
