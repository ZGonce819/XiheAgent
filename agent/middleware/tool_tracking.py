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
    """从 ToolCallRequest 中提取 tool_call_id。

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
    from langgraph.config import get_config
    configurable = get_config().get("configurable", {})
    if not isinstance(configurable, dict):
        return None
    thread_id = configurable.get("thread_id")
    return thread_id if isinstance(thread_id, str) and thread_id else None


def _safe_detail_value(value: Any) -> Any:
    """递归处理工具参数值，遮蔽其中可能出现的访问令牌。

    保留参数的原始结构（dict、list、基本类型），但对字符串值执行
    ``mask_token`` 脱敏，防止令牌泄露到前端。

    Args:
        value: 待处理的参数值，可能是任意嵌套结构。

    Returns:
        脱敏后的同结构值。
    """
    if isinstance(value, dict):
        return {str(key): _safe_detail_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_safe_detail_value(item) for item in value]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return mask_token(str(value))


def _safe_detail_args(args: dict[str, Any]) -> dict[str, Any]:
    """生成可安全发送给前端的结构化工具参数。

    对每个参数值递归调用 ``_safe_detail_value`` 进行脱敏。

    Args:
        args: 原始工具参数字典。

    Returns:
        脱敏后的参数字典，键均为字符串类型。
    """
    return {str(key): _safe_detail_value(value) for key, value in args.items()}


def _tool_result_succeeded(result: ToolMessage | Command, tool_name: str) -> bool:
    """判断工具返回值是否表示执行成功。

    对于命令类工具（``execute``、``run_command``），还需检查退出码和
    输出内容中是否包含失败标记。

    Args:
        result: 工具执行后的返回结果。
        tool_name: 工具名称。

    Returns:
        ``True`` 表示工具执行成功；``False`` 表示失败。
    """
    if not isinstance(result, ToolMessage):
        return True
    if result.status == "error":
        return False
    if tool_name not in {"execute", "run_command"}:
        return True
    artifact = result.artifact
    if isinstance(artifact, dict):
        exit_code = artifact.get("exit_code")
        if isinstance(exit_code, int):
            return exit_code == 0
    content = str(result.content or "")
    return "[Command failed with exit code " not in content


def _tool_result_output(result: ToolMessage | Command, *, limit: int = 8_000) -> str | None:
    """提取可持久化的脱敏工具输出，避免刷新后丢失失败原因。

    Args:
        result: 工具执行后的返回结果。
        limit: 输出最大字符数，超出部分截断。

    Returns:
        脱敏后的输出文本；结果不是 ToolMessage 或无内容时返回 ``None``。
    """
    if not isinstance(result, ToolMessage) or not result.content:
        return None
    if isinstance(result.content, str):
        output = result.content
    else:
        output = json.dumps(result.content, ensure_ascii=False, default=str)
    output = mask_token(output)
    return output if len(output) <= limit else output[:limit] + "\n…输出已截断"


def _map_tool_to_kind(tool_name: str) -> str:
    """根据工具名映射到前端展示的 toolKind。

    Args:
        tool_name: 工具名称。

    Returns:
        前端展示分类标识，如 ``"search"``、``"edit"``、``"execute"`` 等。
    """
    mapping = {
        "ls": "search",
        "list_files": "search",
        "read_file": "read",
        "write_file": "edit",
        "edit_file": "edit",
        "delete": "delete",
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
    """根据工具名和参数生成前端展示的标题。

    Args:
        tool_name: 工具名称。
        args: 工具调用参数。

    Returns:
        人类可读的标题字符串，如 ``"读取文件(main.py)"``。
    """
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
    elif tool_name == "delete":
        path = args.get("path") or args.get("file_path", "")
        return f"删除文件({path})"
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
        """初始化追踪中间件。

        Args:
            backend: 当前工作区后端实例。
        """
        super().__init__()
        self.backend = backend

    def _build_event_key(self, tool_name: str, tool_call_id: str | None) -> str:
        """构建稳定的事件 key，让 start 和 end 事件能被前端匹配。

        Args:
            tool_name: 工具名称。
            tool_call_id: 工具调用 ID，优先使用。

        Returns:
            事件 key 字符串。
        """
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
        """记录工具开始执行，写入 in_progress 事件。

        Args:
            thread_id: 当前线程 ID。
            tool_name: 工具名称。
            tool_call_id: 工具调用 ID。
            args: 工具调用参数。
        """
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
        output: str | None = None,
    ) -> None:
        """记录工具执行完成，写入 completed 或 error 事件。

        Args:
            thread_id: 当前线程 ID。
            key: 事件 key，与 start 事件配对。
            tool_name: 工具名称。
            args: 工具调用参数。
            elapsed_ms: 执行耗时（毫秒）。
            success: 是否执行成功。
            output: 失败时的输出文本，用于前端诊断展示。
        """
        kind = _map_tool_to_kind(tool_name)
        title = _build_tool_title(tool_name, args)
        status = "completed" if success else "error"
        
        detail: dict[str, Any] = {
            "tool": tool_name,
            "args": _safe_detail_args(args),
            "elapsed_ms": elapsed_ms,
        }
        if not success and output:
            detail["output"] = output

        record_event(
            thread_id,
            key,
            title,
            kind=kind,
            status=status,
            detail=json.dumps(detail, ensure_ascii=False),
        )

    def wrap_tool_call(
        self,
        request: ToolCallRequest,
        handler: Callable[[ToolCallRequest], ToolMessage | Command],
    ) -> ToolMessage | Command:
        """同步工具调用包装器，记录执行时间和结果。

        Args:
            request: LangGraph 工具调用请求对象。
            handler: 下游工具调用处理函数。

        Returns:
            下游返回的工具执行结果。
        """
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
            
            success = _tool_result_succeeded(result, tool_name)
            self._record_tool_end(
                thread_id,
                key=key,
                tool_name=tool_name,
                args=args,
                elapsed_ms=elapsed_ms,
                success=success,
                output=_tool_result_output(result) if not success else None,
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
        """异步工具调用包装器，记录执行时间和结果。

        Args:
            request: LangGraph 工具调用请求对象。
            handler: 下游异步工具调用处理函数。

        Returns:
            下游返回的工具执行结果。
        """
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

            success = _tool_result_succeeded(result, tool_name)
            self._record_tool_end(
                thread_id,
                key=key,
                tool_name=tool_name,
                args=args,
                elapsed_ms=elapsed_ms,
                success=success,
                output=_tool_result_output(result) if not success else None,
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
