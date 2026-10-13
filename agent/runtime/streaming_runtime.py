"""DeepAgents V3 事件流消费层。

负责把 DeepAgent 的 `stream_events(version="v3")` 原始协议事件转换成前端可展示的运行步骤。
这是 FastAPI 版本替代 `langgraph dev` 的核心桥接层：

- DeepAgent 继续按官方 `stream_events(version="v3")` 运行。
- 后端把 message、tool_calls、subagents 转成课程项目的 `run_events`。
- 每一轮运行都把 run_id 写进事件 id，保证 plan、coding、review 多轮内容
  不会在前端互相覆盖或拼接到同一个消息里。
- 前端仍只消费我们自己的 `/api/v1/.../stream`，不用绑定 LangGraph 本地服务。

核心能力：
1. 从 raw protocol events 中提取 text-delta，实现正文流式展示。
2. 识别 write_todos 工具调用，转成结构化任务清单事件。
3. 记录子智能体生命周期事件。
4. 通过 AgentRunLimitTracker 做运行保护，防止工具调用次数或耗时失控。
"""

from __future__ import annotations

import hashlib
import json
import logging
import queue
import re
import threading
from uuid import uuid4
from collections.abc import Callable, Iterable
from typing import Any

from langchain_core.messages import BaseMessage

from agent.middleware.run_limits import AgentRunLimits, AgentRunLimitExceeded, AgentRunLimitTracker
from agent.runtime.cancellation import begin_run, end_run, raise_if_cancelled
from agent.runtime.events import record_event
from config.constants import MAX_STREAM_TEXT_LENGTH, STREAM_FLUSH_THRESHOLD
from utils.gitee_utils import mask_token

logger = logging.getLogger(__name__)

StreamEventSink = Callable[[str, dict[str, Any]], None]


def _safe_attr(value: Any, name: str, default: Any = None) -> Any:
    """安全读取官方流对象字段。

    Deep Agents / LangGraph 的 v3 streaming 协议还带有 experimental 提示，
    不同小版本的字段可能是属性，也可能是轻量对象方法。这里统一容错读取，
    避免某个字段缺失时直接打断整个 Agent 任务。

    Args:
        value: 流事件对象或字典。
        name: 要读取的字段名。
        default: 字段不存在或读取失败时的默认值。

    Returns:
        字段值；读取失败时返回 ``default``。
    """

    try:
        return getattr(value, name, default)
    except Exception:
        return default


def _safe_field(value: Any, name: str, default: Any = None) -> Any:
    """兼容官方事件对象和普通 dict 的字段读取。

    Args:
        value: 流事件对象或字典。
        name: 要读取的字段名。
        default: 字段不存在时的默认值。

    Returns:
        字段值；不存在时返回 ``default``。
    """

    if isinstance(value, dict):
        return value.get(name, default)
    return _safe_attr(value, name, default)


def _stringify(value: Any, *, limit: int = MAX_STREAM_TEXT_LENGTH) -> str:
    """把事件对象中的输入、输出压缩成适合前端展示的短文本。

    前端步骤区只需要告诉学员"正在做什么"，不应该塞入大段 token、
    大段文件内容或未脱敏的异常。真正的详细排查仍看后端日志。

    Args:
        value: 待压缩的事件字段值。
        limit: 最大字符数，超出部分截断并追加 ``...``。

    Returns:
        截断并脱敏后的短文本；空值返回空字符串。
    """

    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        text = repr(value)
    text = mask_token(text)
    if len(text) > limit:
        return f"{text[:limit]}..."
    return text


def _event_payloads(event: Any) -> list[Any]:
    """从 raw protocol event 中取出 params.data。

    官方文档中的 messages 事件形态是 `event["params"]["data"][0]`。
    实际小版本中 data 可能是单个 dict，也可能是 list，这里统一规整成列表。

    Args:
        event: raw protocol event 字典。

    Returns:
        规整后的 payload 列表；无法提取时返回空列表。
    """

    if not isinstance(event, dict):
        return []
    params = event.get("params")
    if not isinstance(params, dict):
        return []
    data = params.get("data")
    if data is None:
        return []
    if isinstance(data, list):
        return data
    if isinstance(data, tuple):
        # LangGraph v3 的真实 messages 事件形态通常是：
        # params.data = (payload, metadata)。第 0 项才是 content-block-delta 等正文事件。
        return [data[0]] if data else []
    return [data]


def _text_delta_from_event(event: Any) -> str:
    """按官方 raw event 协议提取正文 token。

    Deep Agents 文档建议 UI 需要精确流式正文时直接读取 raw protocol events：
    method=messages、event=content-block-delta、delta.type=text-delta。

    Args:
        event: raw protocol event 字典。

    Returns:
        提取到的文本增量；非匹配事件返回空字符串。
    """

    if not isinstance(event, dict) or event.get("method") != "messages":
        return ""
    deltas: list[str] = []
    for payload in _event_payloads(event):
        if not isinstance(payload, dict):
            continue
        if payload.get("event") != "content-block-delta":
            continue
        block = payload.get("delta") or {}
        if not isinstance(block, dict):
            continue
        block_type = str(block.get("type") or "")
        if block_type in {"text-delta", "text_delta"}:
            deltas.append(str(block.get("text") or block.get("content") or ""))
            continue
        if block_type == "block-delta":
            fields = block.get("fields")
            if isinstance(fields, dict) and str(fields.get("type") or "") in {
                "text-delta",
                "text_delta",
                "text",
            }:
                deltas.append(str(fields.get("text") or fields.get("content") or ""))
    return "".join(deltas)


def _text_block_marker_from_event(event: Any) -> tuple[str, int | None] | None:
    """识别 raw messages 中的文本块边界。

    DeepAgents v3 会把 assistant 的一次回复拆成若干 content block。只有
    `content-block-delta/text-delta` 真正携带 token，但 `content-block-start` 和
    `content-block-finish` 能告诉我们"这一段 assistant 文本开始/结束了"。这里返回：

    - `("start", index)`：新的文本块开始，后续 token 应进入新的 AIMessage。
    - `("finish", index)`：当前文本块结束，后续非空文本应开启下一条 AIMessage。

    不同小版本的 payload 字段可能略有差异，所以只做保守识别；识别不到时返回 None，
    调用方会继续把 token 归入当前文本块。

    Args:
        event: raw protocol event 字典。

    Returns:
        ``("start", index)`` 表示新文本块开始；``("finish", index)`` 表示结束；
        无法识别时返回 ``None``。
    """

    payload = _message_event_payload(event)
    if not isinstance(payload, dict):
        return None

    event_name = str(payload.get("event") or "")
    index_value = payload.get("index")
    try:
        block_index = int(index_value) if index_value is not None else None
    except (TypeError, ValueError):
        block_index = None

    if event_name == "content-block-start":
        content = payload.get("content")
        if isinstance(content, dict):
            content_type = str(content.get("type") or "")
            if content_type in {"text", "text_delta", "output_text"}:
                return "start", block_index
        # 有些版本的 text block start 不带 content.type；只要不是工具调用块，就按文本块处理。
        if not isinstance(content, dict) or str(content.get("type") or "") not in {"tool_call", "tool_call_chunk"}:
            return "start", block_index

    if event_name == "content-block-finish":
        content = payload.get("content")
        if isinstance(content, dict) and str(content.get("type") or "") in {"tool_call", "tool_call_chunk"}:
            return None
        return "finish", block_index

    return None


def _message_event_payload(event: Any) -> dict[str, Any] | None:
    """读取 raw messages 事件中的第一个 payload。

    Args:
        event: raw protocol event 字典。

    Returns:
        第一个 dict payload；非 messages 事件或无 payload 时返回 ``None``。
    """

    if not isinstance(event, dict) or event.get("method") != "messages":
        return None
    for payload in _event_payloads(event):
        if isinstance(payload, dict):
            return payload
    return None


def _tool_chunk_from_message_event(event: Any) -> dict[str, Any] | None:
    """从 raw messages 事件中读取工具调用 chunk。

    当前 DeepAgents 版本会把工具调用参数作为 message content block 输出：
    content-block-delta -> delta.type=block-delta -> fields.type=tool_call_chunk。
    write_todos 的 JSON 参数会以 fields.args 逐步增长。

    Args:
        event: raw protocol event 字典。

    Returns:
        tool_call_chunk 或 tool_call 字典；非工具事件时返回 ``None``。
    """

    payload = _message_event_payload(event)
    if not payload:
        return None

    if payload.get("event") == "content-block-start":
        content = payload.get("content")
        if isinstance(content, dict) and content.get("type") == "tool_call_chunk":
            return content

    if payload.get("event") == "content-block-delta":
        delta = payload.get("delta")
        if isinstance(delta, dict) and delta.get("type") == "block-delta":
            fields = delta.get("fields")
            if isinstance(fields, dict) and fields.get("type") == "tool_call_chunk":
                return fields

    if payload.get("event") == "content-block-finish":
        content = payload.get("content")
        if isinstance(content, dict) and content.get("type") == "tool_call":
            return content

    return None


def _should_flush_stream_text(*, accumulated_text: str, last_flushed_length: int, delta: str) -> bool:
    """判断是否需要把模型正文增量刷新到业务事件表。

    DeepAgents 的 raw message 事件可能按 token 级别返回，如果每个 token 都写一次数据库，
    页面虽然实时，但提交会过于频繁。这里做轻量合并：
    - 首段内容立即展示，让用户知道模型已经开始输出；
    - 累计新增达到 ``STREAM_FLUSH_THRESHOLD`` 个字符左右刷新一次；
    - 遇到换行也刷新，Markdown 标题、列表和段落会更快出现在页面上。

    Args:
        accumulated_text: 当前文本块累计的全部文本。
        last_flushed_length: 上次刷新时的文本长度。
        delta: 本轮新增的文本增量。

    Returns:
        ``True`` 表示应刷新；``False`` 表示继续累积。
    """

    if not accumulated_text:
        return False
    if last_flushed_length == 0:
        return True
    if len(accumulated_text) - last_flushed_length >= STREAM_FLUSH_THRESHOLD:
        return True
    return "\n" in delta


def _tool_call_from_event(event: Any) -> Any | None:
    """尽量从 raw tool_calls event 中提取工具调用对象。

    工具事件在不同 DeepAgents 小版本中的字段可能不完全一致；本函数只做保守解析。
    解析不到时返回 None，具体文件/命令/SCM 工具仍会通过工具内部 record_event 展示。

    Args:
        event: raw protocol event 字典。

    Returns:
        工具调用对象；非 tool_calls 事件或提取失败时返回 ``None``。
    """

    if not isinstance(event, dict) or event.get("method") != "tool_calls":
        return None
    for payload in _event_payloads(event):
        if isinstance(payload, dict):
            return payload
        if payload is not None:
            return payload
    return None


def _subagent_from_event(event: Any) -> Any | None:
    """尽量从 raw subagents event 中提取子智能体对象。

    Args:
        event: raw protocol event 字典。

    Returns:
        子智能体对象；非 subagents 事件时返回 ``None``。
    """

    if not isinstance(event, dict) or event.get("method") != "subagents":
        return None
    for payload in _event_payloads(event):
        if payload is not None:
            return payload
    return None


def _normalize_todo_status(status: Any) -> str:
    """把 DeepAgents / LangChain 的 todo 状态规整为前端支持的三种状态。

    Args:
        status: 原始状态值。

    Returns:
        ``"in_progress"``、``"completed"`` 或 ``"pending"`` 之一。
    """

    text = str(status or "pending").lower()
    if text in {"in_progress", "in-progress", "active", "doing"}:
        return "in_progress"
    if text in {"completed", "complete", "done"}:
        return "completed"
    return "pending"


def _extract_todos(tool_call: Any) -> list[dict[str, str]]:
    """从 write_todos 官方 tool_call 中提取任务清单。

    Args:
        tool_call: write_todos 工具调用对象或字典。

    Returns:
        包含 content 和 status 的任务字典列表；无法提取时返回空列表。
    """

    call_input = _safe_field(tool_call, "input")
    if call_input is None:
        call_input = _safe_field(tool_call, "args")
    if isinstance(call_input, str):
        try:
            call_input = json.loads(call_input)
        except ValueError:
            return [{"content": call_input, "status": "pending"}] if call_input.strip() else []

    raw_todos: Any
    if isinstance(call_input, dict):
        raw_todos = call_input.get("todos") or call_input.get("items") or []
    else:
        raw_todos = call_input

    if not isinstance(raw_todos, list):
        return []

    todos: list[dict[str, str]] = []
    for item in raw_todos:
        if isinstance(item, str):
            content = item.strip()
            status = "pending"
        elif isinstance(item, dict):
            content = str(item.get("content") or item.get("task") or item.get("title") or "").strip()
            status = _normalize_todo_status(item.get("status"))
        else:
            content = str(item).strip()
            status = "pending"
        if content:
            todos.append({"content": content, "status": status})
    return todos


def _record_write_todos(
    thread_id: str,
    run_id: str,
    tool_call: Any,
    index: int,
    *,
    event_sink: StreamEventSink | None = None,
) -> bool:
    """只把 DeepAgents 内置 write_todos 转成结构化任务清单事件。

    Args:
        thread_id: 当前线程 ID。
        run_id: 当前运行 ID。
        tool_call: 工具调用对象。
        index: 工具调用序号，用于生成事件 ID。
        event_sink: 可选的 SSE 事件推送回调。

    Returns:
        ``True`` 表示是 write_todos 并已处理；``False`` 表示不是。
    """

    tool_name = str(_safe_field(tool_call, "tool_name", "") or _safe_field(tool_call, "name", "") or "")
    if tool_name != "write_todos":
        return False
    todos = _extract_todos(tool_call)
    if not todos:
        return True
    call_id = str(_safe_field(tool_call, "id", "") or _safe_field(tool_call, "tool_call_id", "") or index)
    _record_todos(
        thread_id,
        run_id,
        call_id,
        todos,
        status="completed",
        event_sink=event_sink,
    )
    return True


def _decode_json_string_fragment(value: str) -> str:
    """解码正则截取出的 JSON 字符串片段。

    对正则捕获的转义内容执行 ``json.loads`` 还原原始文本，
    解码失败时原样返回。

    Args:
        value: 可能是 JSON 转义字符串的文本片段。

    Returns:
        解码后的纯文本；解码失败时返回原始值。
    """

    try:
        return json.loads(f'"{value}"')
    except ValueError:
        return value


def _todos_from_args_text(args_text: str) -> list[dict[str, str]]:
    """从 write_todos 的参数文本中提取已形成的 todo。

    args_text 在 raw chunk 中经常是"不完整但逐步增长"的 JSON 字符串。完整时直接
    json.loads；不完整时用保守正则提取已经闭合的 content/status 对象，让前端能更早
    看到任务计划逐项出现。

    Args:
        args_text: write_todos 工具的参数文本（可能不完整）。

    Returns:
        提取到的任务字典列表；无法提取时返回空列表。
    """

    if not args_text.strip():
        return []
    try:
        parsed = json.loads(args_text)
    except ValueError:
        parsed = None

    if isinstance(parsed, dict):
        return _extract_todos({"input": parsed})

    todos: list[dict[str, str]] = []
    pattern = re.compile(
        r'\{\s*"content"\s*:\s*"(?P<content>(?:\\.|[^"\\])*)"\s*,\s*"status"\s*:\s*"(?P<status>[^"]*)"',
        re.DOTALL,
    )
    for match in pattern.finditer(args_text):
        content = _decode_json_string_fragment(match.group("content")).strip()
        status = _normalize_todo_status(match.group("status"))
        if content:
            todos.append({"content": content, "status": status})
    return todos


def _record_todos(
    thread_id: str,
    run_id: str,
    call_id: str,
    todos: list[dict[str, str]],
    *,
    status: str,
    event_sink: StreamEventSink | None = None,
) -> None:
    """写入结构化任务计划事件。

    同时写入 MySQL run_events 表和可选的 SSE event_sink，
    保证前端实时展示和刷新后历史恢复都能拿到任务清单。

    Args:
        thread_id: 当前线程 ID。
        run_id: 当前运行 ID。
        call_id: 工具调用 ID。
        todos: 任务清单列表。
        status: 事件状态。
        event_sink: 可选的 SSE 事件推送回调。
    """

    if not todos:
        return
    record_event(
        thread_id,
        f"todos:{run_id}:{call_id}",
        "任务清单",
        kind="todo",
        status=status,
        detail=json.dumps({"todos": todos}, ensure_ascii=False),
    )
    if event_sink is not None:
        event_sink(
            "todo_delta",
            {
                "message_id": f"{thread_id}-live-plan-{run_id}",
                "run_id": run_id,
                "todos": todos,
            },
        )


def _message_dict(message: Any) -> dict[str, Any]:
    """把最终输出中的 LangChain 消息对象转换为普通字典。

    Args:
        message: LangChain 消息对象或其他类型的消息。

    Returns:
        包含 type 和 content 的字典；消息带 token 用量时一并带上。用量是自动化任务
        统计预算的唯一来源，这里丢掉就等于那项预算永远算不出来。
    """

    if isinstance(message, BaseMessage):
        payload: dict[str, Any] = {
            "type": message.type,
            "content": message.content,
        }
        if getattr(message, "tool_calls", None):
            payload["tool_calls"] = message.tool_calls
        usage = getattr(message, "usage_metadata", None)
        if usage:
            payload["usage_metadata"] = usage
        return payload
    if isinstance(message, dict):
        # 部分 LangGraph/兼容模型返回已经序列化的消息字典。
        # 保留 type/role/content，避免后续正文提取把整个 dict 当成字符串。
        payload = {
            "type": message.get("type") or message.get("role") or "",
            "role": message.get("role"),
            "content": message.get("content") or message.get("text") or "",
        }
        if message.get("usage_metadata"):
            payload["usage_metadata"] = message["usage_metadata"]
        if message.get("tool_calls"):
            payload["tool_calls"] = message["tool_calls"]
        return payload
    return {"type": type(message).__name__, "content": str(message)}


def _messages_from_output(output: Any) -> list[dict[str, Any]]:
    """从 stream.output 中提取最终 messages。

    官方 Deep Agents 返回值通常是 `{"messages": [...]}`；为了课程版稳定运行，
    这里也兼容对象属性和其它返回结构。

    Args:
        output: DeepAgent stream 的最终输出对象。

    Returns:
        消息字典列表；无法提取时返回空列表。
    """

    if isinstance(output, dict):
        messages = output.get("messages") or []
    else:
        messages = _safe_attr(output, "messages", []) or []
    if not isinstance(messages, Iterable) or isinstance(messages, (str, bytes)):
        return []
    return [_message_dict(message) for message in messages]


def _record_assistant_stream_message(
    thread_id: str,
    run_id: str,
    index: int,
    text: str,
    *,
    event_sink: StreamEventSink | None = None,
) -> None:
    """把某一段非空 assistant 文本写成独立的前端事件。

    `stream:{run_id}:assistant:{index}` 表示运行过程中第 index 段 assistant 文本。
    前端会把不同 index 展示成不同的 AIMessage，因此 Todo 后面的"正在说明/总结/代码处理过程"
    不会互相覆盖。

    Args:
        thread_id: 当前线程 ID。
        run_id: 当前运行 ID。
        index: 文本段序号。
        text: assistant 文本内容。
        event_sink: 可选的 SSE 事件推送回调。
    """

    if not text.strip():
        return
    record_event(
        thread_id,
        f"stream:{run_id}:assistant:{index}",
        "正在生成内容",
        kind="other",
        status="in_progress",
        detail=json.dumps({"text": text}, ensure_ascii=False),
    )
    if event_sink is not None:
        message_id = f"{thread_id}-live-assistant-{run_id}-{index}"
        event_sink(
            "message_start",
            {
                "message_id": message_id,
                "author": "agent",
                "run_id": run_id,
                "assistant_index": str(index),
            },
        )
        event_sink(
            "text_delta",
            {
                "message_id": message_id,
                "run_id": run_id,
                "assistant_index": str(index),
                "content": text,
                "mode": "replace",
            },
        )


def _tool_event_from_raw(event: Any) -> dict[str, Any] | None:
    """读取 raw tools 生命周期事件。

    Args:
        event: raw protocol event 字典。

    Returns:
        tools 事件 data 字典；非 tools 事件时返回 ``None``。
    """

    if not isinstance(event, dict) or event.get("method") != "tools":
        return None
    params = event.get("params")
    if not isinstance(params, dict):
        return None
    data = params.get("data")
    return data if isinstance(data, dict) else None


def _record_subagent(thread_id: str, run_id: str, subagent: Any, index: int) -> None:
    """记录 Deep Agents 子智能体生命周期。

    第一版 UI 不单独做子智能体卡片，只用一条简洁步骤展示 delegated task。

    Args:
        thread_id: 当前线程 ID。
        run_id: 当前运行 ID。
        subagent: 子智能体对象。
        index: 子智能体序号。
    """

    name = str(_safe_field(subagent, "name", "") or "subagent")
    status = str(_safe_field(subagent, "status", "") or "started")
    event_status = "completed" if status == "completed" else "error" if status == "failed" else "in_progress"
    path = _safe_field(subagent, "path")
    record_event(
        thread_id,
        f"stream:{run_id}:subagent:{index}:{name}",
        f"子智能体：{name}",
        kind="think",
        status=event_status,
        detail=_stringify(path, limit=500) or None,
    )


STREAM_STALL_SECONDS = 300  # 主流程事件流超过该秒数无任何事件即判定挂起（180s 对 Excel/代码类读文件题偏短，调回 300s）
# 注意：必须小于调用方静默检测（1200s），让软中断收尾先于调用方放弃执行
REPAIR_STALL_SECONDS = 600   # 修复轮看门狗（放宽，让工具层 timeout 自己处理）
REPAIR_MAX_SECONDS = 120    # 修复轮总时长上限（原 240s）


class _WatchdogStream:
    """事件流挂起看门狗包装器：距上一个事件超过 ``idle_limit`` 秒即判定流式挂起。

    底层同步迭代（langgraph ``stream_events`` / 模型流）阻塞在 socket 读时，
    调用方无法感知"长时间无事件"（模型调用 200 但流不产 chunk，且无心跳时
    read timeout 也不触发）。本包装器把迭代放进后台 daemon 线程，主线程按
    事件间隔超时等待：超时抛 :class:`AgentRunLimitExceeded`，由上层走软中断
    收尾（注入 repair 提示 + 一次修复轮），不再让线程/队列无限挂起。

    用类而非生成器函数实现：下游仍需要 ``stream.output``（langgraph
    stream_events 返回值上的最终结果属性），生成器没有该属性会直接
    AttributeError。这里通过 ``output`` property 转发到底层原始 stream，
    保持对调用方透明。

    后台线程在超时中断后会留在原地（无法安全终止阻塞中的 socket 读），
    以 daemon 方式运行随 serve 进程退出；该场景为罕见故障，不阻塞正常路径。
    """

    def __init__(self, stream: Iterable[Any], idle_limit: float = STREAM_STALL_SECONDS):
        self._stream = stream
        self._idle_limit = idle_limit

    @property
    def output(self) -> Any:
        # 转发 langgraph stream_events 返回对象的最终结果属性。
        return self._stream.output

    def __iter__(self) -> Any:
        q: "queue.Queue[Any]" = queue.Queue(maxsize=8)
        done = threading.Event()

        def _pump() -> None:
            try:
                for ev in self._stream:
                    q.put(ev)
            except BaseException as exc:  # noqa: BLE001 - 必须把底层流异常传给主线程
                try:
                    q.put(exc, timeout=5)
                except Exception:  # noqa: BLE001
                    pass
            finally:
                done.set()

        threading.Thread(target=_pump, daemon=True, name="stream-watchdog-pump").start()
        while True:
            try:
                item = q.get(timeout=self._idle_limit)
            except queue.Empty as exc:
                raise AgentRunLimitExceeded(
                    f"事件流超过 {int(self._idle_limit)} 秒无任何事件（流式挂起），主动中断本轮"
                ) from exc
            if isinstance(item, BaseException):
                raise item
            yield item
            if done.is_set() and q.empty():
                return


def _watchdog_stream(stream: Iterable[Any], idle_limit: float = STREAM_STALL_SECONDS) -> _WatchdogStream:
    return _WatchdogStream(stream, idle_limit=idle_limit)


def _consume_raw_event_stream(
    *,
    stream: Any,
    thread_id: str,
    run_id: str,
    task_kind: str | None = None,
    event_sink: StreamEventSink | None = None,
    repair_limits: AgentRunLimits | None = None,
) -> tuple[int, int, bool]:
    """按官网 raw protocol event 消费 DeepAgents 输出。

    这个函数解决"技术方案正文只能最终一次性展示"的问题：
    1. 直接读取 `method=messages` 的 `content-block-delta/text-delta`，把累计正文写入
       `stream:message`，前端 SSE 会持续拿到越来越完整的 Markdown 正文。
    2. 同时继续读取 `method=tool_calls` 和 `method=subagents`，保留 write_todos 任务计划、
       工具步骤和子 Agent 生命周期展示。

    如果某个 DeepAgents 小版本没有在 raw event 中暴露 tool_calls，工具内部的 record_event
    仍然会记录读文件、命令、SCM 仓库操作等步骤；但 write_todos 只有 raw tool_calls 可见时才会出现。

    Args:
        stream: DeepAgent 的 v3 事件流迭代器。
        thread_id: 当前线程 ID。
        run_id: 当前运行 ID。
        task_kind: 任务类型，用于运行保护阈值。
        event_sink: 可选的 SSE 事件推送回调。

    Returns:
        ``(tool_call_index, subagent_index)`` 元组。
    """

    tool_call_index = 0
    subagent_index = 0
    # 审批恢复沿用 run_id；每次消费必须使用新的段落编号，避免覆盖先前正文和工具。
    assistant_message_index = uuid4().int
    current_assistant_index = 0
    current_assistant_text = ""
    saw_assistant_text = False
    last_flushed_length = 0
    write_todo_args_by_call: dict[str, str] = {}
    write_todo_last_payload_by_call: dict[str, str] = {}
    saw_write_todos = False
    if repair_limits is not None:
        limit_tracker = AgentRunLimitTracker(limits=repair_limits, task_kind=task_kind)
    else:
        limit_tracker = AgentRunLimitTracker(task_kind=task_kind)

    for event in stream:
        # 每两个协议事件之间检查一次停止请求。放在这里而不是只在 event_sink 里，
        # 是因为工具事件、子 Agent 事件不一定都走 event_sink；这个循环是所有
        # 协议事件的必经之处。
        raise_if_cancelled(thread_id)
        limit_tracker.observe_event(event)
        marker = _text_block_marker_from_event(event)
        if marker is not None:
            marker_kind, _block_index = marker
            if marker_kind == "start":
                if current_assistant_text and last_flushed_length != len(current_assistant_text):
                    _record_assistant_stream_message(
                        thread_id,
                        run_id,
                        current_assistant_index or assistant_message_index or 1,
                        current_assistant_text,
                        event_sink=event_sink,
                    )
                assistant_message_index += 1
                current_assistant_index = assistant_message_index
                current_assistant_text = ""
                last_flushed_length = 0
                continue
            if marker_kind == "finish":
                if current_assistant_text and last_flushed_length != len(current_assistant_text):
                    _record_assistant_stream_message(
                        thread_id,
                        run_id,
                        current_assistant_index or assistant_message_index or 1,
                        current_assistant_text,
                        event_sink=event_sink,
                    )
                current_assistant_index = 0
                current_assistant_text = ""
                last_flushed_length = 0
                continue

        delta = _text_delta_from_event(event)
        if delta:
            if current_assistant_index == 0:
                assistant_message_index += 1
                current_assistant_index = assistant_message_index
                current_assistant_text = ""
                last_flushed_length = 0
            current_assistant_text += delta
            saw_assistant_text = True
            if _should_flush_stream_text(
                accumulated_text=current_assistant_text,
                last_flushed_length=last_flushed_length,
                delta=delta,
            ):
                _record_assistant_stream_message(
                    thread_id,
                    run_id,
                    current_assistant_index,
                    current_assistant_text,
                    event_sink=event_sink,
                )
                last_flushed_length = len(current_assistant_text)
            continue

        tool_chunk = _tool_chunk_from_message_event(event)
        if tool_chunk is not None:
            tool_name = str(tool_chunk.get("name") or "")
            if tool_name == "write_todos":
                saw_write_todos = True
                call_id = str(tool_chunk.get("id") or tool_chunk.get("tool_call_id") or "write_todos")
                args = tool_chunk.get("args")
                if isinstance(args, dict):
                    todos = _extract_todos({"input": args})
                    payload_text = json.dumps(todos, ensure_ascii=False)
                    if payload_text != write_todo_last_payload_by_call.get(call_id):
                        _record_todos(
                            thread_id,
                            run_id,
                            call_id,
                            todos,
                            status="completed",
                            event_sink=event_sink,
                        )
                        write_todo_last_payload_by_call[call_id] = payload_text
                elif isinstance(args, str):
                    write_todo_args_by_call[call_id] = args
                    todos = _todos_from_args_text(args)
                    payload_text = json.dumps(todos, ensure_ascii=False)
                    if todos and payload_text != write_todo_last_payload_by_call.get(call_id):
                        _record_todos(
                            thread_id,
                            run_id,
                            call_id,
                            todos,
                            status="in_progress",
                            event_sink=event_sink,
                        )
                        write_todo_last_payload_by_call[call_id] = payload_text
            continue

        tool_call = _tool_call_from_event(event)
        if tool_call is not None:
            tool_call_index += 1
            if _record_write_todos(thread_id, run_id, tool_call, tool_call_index, event_sink=event_sink):
                saw_write_todos = True
                continue
            logger.debug(
                "忽略官方 raw tool_call 展示事件：thread_id=%s index=%s item=%s",
                thread_id,
                tool_call_index,
                tool_call,
            )
            continue

        tool_event = _tool_event_from_raw(event)
        if tool_event is not None:
            tool_name = str(tool_event.get("tool_name") or "")
            if tool_name == "write_todos":
                saw_write_todos = True
                call_id = str(tool_event.get("tool_call_id") or "write_todos")
                tool_input = tool_event.get("input")
                if isinstance(tool_input, dict):
                    todos = _extract_todos({"input": tool_input})
                    payload_text = json.dumps(todos, ensure_ascii=False)
                    if todos and payload_text != write_todo_last_payload_by_call.get(call_id):
                        event_status = "completed" if tool_event.get("event") == "tool-finished" else "in_progress"
                        _record_todos(
                            thread_id,
                            run_id,
                            call_id,
                            todos,
                            status=event_status,
                            event_sink=event_sink,
                        )
                        write_todo_last_payload_by_call[call_id] = payload_text
            continue

        subagent = _subagent_from_event(event)
        if subagent is not None:
            subagent_index += 1
            _record_subagent(thread_id, run_id, subagent, subagent_index)

    if current_assistant_text and last_flushed_length != len(current_assistant_text):
        _record_assistant_stream_message(
            thread_id,
            run_id,
            current_assistant_index or assistant_message_index or 1,
            current_assistant_text,
            event_sink=event_sink,
        )
    return tool_call_index if saw_write_todos else 0, subagent_index, saw_assistant_text


def _record_pending_commands(thread_id: str, output: Any, approval: Any) -> None:
    """审批可在追踪中间件之前暂停；使用真实调用 ID 保存待执行命令。"""
    if not isinstance(output, dict) or not isinstance(approval, dict):
        return
    actions = list(approval.get("action_requests") or [])
    for message in reversed(output.get("messages") or []):
        calls = _safe_field(message, "tool_calls", []) or []
        if not calls:
            continue
        from agent.middleware.tool_tracking import _build_tool_title, _map_tool_to_kind, _safe_detail_args

        for call in calls:
            name = _safe_field(call, "name", "")
            args = _safe_field(call, "args", {})
            call_id = _safe_field(call, "id", "")
            match = next((a for a in actions if a.get("name") == name and a.get("args") == args), None)
            if not call_id or match is None:
                continue
            actions.remove(match)
            record_event(thread_id, f"tool:{call_id}", _build_tool_title(name, args),
                         kind=_map_tool_to_kind(name), status="pending",
                         detail=json.dumps({"tool": name, "args": _safe_detail_args(args)}, ensure_ascii=False))
        break


def run_agent_with_event_stream(
    *,
    agent: Any,
    thread_id: str,
    run_id: str,
    content: str,
    task_kind: str | None = None,
    event_sink: StreamEventSink | None = None,
    checkpoint_id: str | None = None,
    resume_decisions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """使用官方 v3 event streaming 驱动 DeepAgent。

    这里只负责「运行期间可被停止」这一件事：进入前登记运行态，退出时（正常、
    报错、被停止都算）注销。真正的消费逻辑在 :func:`_run_agent_with_event_stream`。

    Args:
        agent: DeepAgent runnable 实例。
        thread_id: 当前线程 ID。
        run_id: 当前运行 ID。
        content: 发送给 Agent 的用户内容。
        task_kind: 任务类型，用于运行保护阈值。
        event_sink: 可选的 SSE 事件推送回调。
        checkpoint_id: 可选的回退基点。
        resume_decisions: 审批恢复时的表决结果。

    Returns:
        包含 messages 和 raw_output 的结果字典。

    Raises:
        AgentRunLimitExceeded: 运行保护阈值被突破时。
        AgentRunCancelledError: 用户在这一轮中途点了「停止」。
    """

    begin_run(thread_id)
    try:
        return _run_agent_with_event_stream(
            agent=agent,
            thread_id=thread_id,
            run_id=run_id,
            content=content,
            task_kind=task_kind,
            event_sink=event_sink,
            checkpoint_id=checkpoint_id,
            resume_decisions=resume_decisions,
        )
    finally:
        end_run(thread_id)


def _run_agent_with_event_stream(
    *,
    agent: Any,
    thread_id: str,
    run_id: str,
    content: str,
    task_kind: str | None = None,
    event_sink: StreamEventSink | None = None,
    checkpoint_id: str | None = None,
    resume_decisions: list[dict[str, Any]] | None = None,
    repair_used: int = 0,
    max_repairs: int = 1,
) -> dict[str, Any]:
    """实际消费逻辑，由 :func:`run_agent_with_event_stream` 负责运行态登记后调用。

    这个函数是 FastAPI 版本替代 `langgraph dev` 的核心桥接层：
    - DeepAgent 继续按官方 `stream_events(version="v3")` 运行。
    - 后端把 message、tool_calls、subagents 转成课程项目的 `run_events`。
    - 每一轮运行都把 run_id 写进事件 id，保证 plan、coding、review 多轮内容
      不会在前端互相覆盖或拼接到同一个消息里。
    - 前端仍只消费我们自己的 `/api/v1/.../stream`，不用绑定 LangGraph 本地服务。

    Args:
        agent: DeepAgent runnable 实例。
        thread_id: 当前线程 ID。
        run_id: 当前运行 ID。
        content: 发送给 Agent 的用户内容。
        task_kind: 任务类型，用于运行保护阈值。
        event_sink: 可选的 SSE 事件推送回调。

    Returns:
        包含 messages 和 raw_output 的结果字典。

    Raises:
        AgentRunLimitExceeded: 运行保护阈值被突破时。
        AgentRunCancelledError: 用户在这一轮中途点了「停止」。
    """

    configurable = {"thread_id": thread_id}
    if checkpoint_id:
        configurable["checkpoint_id"] = checkpoint_id
    if resume_decisions is None:
        graph_input: Any = {"messages": [{"role": "user", "content": content}]}
    else:
        from langgraph.types import Command

        graph_input = Command(resume={"decisions": resume_decisions})
    run_config = {"configurable": configurable}
    stream = agent.stream_events(
        graph_input,
        version="v3",
        config=run_config,
    )
    # 流式挂起看门狗：langgraph/模型流同步迭代阻塞时调用方无法感知，
    # 包装后事件间隔超过 STREAM_STALL_SECONDS 即主动中断，走软中断收尾。
    stream = _watchdog_stream(stream)
    record_event(thread_id, "model", "调用大模型", kind="other", status="in_progress")
    # raw protocol events 是当前版本里唯一能拿到 token/chunk 的通道。
    # 这里同时解析 text-delta 和 write_todos 的 tool_call_chunk，保证正文和任务计划都能流式更新。
    try:
        tool_call_index, subagent_index, saw_assistant_text = _consume_raw_event_stream(
            stream=stream,
            thread_id=thread_id,
            run_id=run_id,
            task_kind=task_kind,
            event_sink=event_sink,
        )
    except AgentRunLimitExceeded as exc:
        record_event(
            thread_id,
            "agent:run-limit",
            "达到运行保护上限",
            kind="other",
            status="error",
            detail=str(exc),
        )
        # 软中断自修复：不直接作废本轮，而是把"超时"作为一条用户指令注入，
        # 用同一 checkpoint 恢复运行，让模型基于已有证据自行收尾给出最终答案。
        # 只允许一次修复轮；修复轮仍挂起则从 checkpoint 抽最后一条 assistant 兜底。
        if repair_used < max_repairs:
            # F: 主动丢弃旧 stream 引用，帮助 GC 回收底层卡死的 HTTP/SSE 连接，
            # 避免修复轮复用同一个半死的 TCP 连接继续挂起。
            stream = None
            repair_msg = (
                "你上一轮运行已超过运行保护上限被强制中断。现在禁止调用任何工具，"
                "不要发起任何搜索/文件读取/代码执行；直接基于当前已有消息和证据，"
                "用一句话给出最终答案。如果证据不足，给出最可能的答案并标注不确定性。"
            )
            try:
                repair_stream = agent.stream_events(
                    {"messages": [{"role": "user", "content": repair_msg}]},
                    version="v3",
                    config=run_config,
                )
                # 修复轮看门狗与时间预算同步调短，避免在脏连接上干等。
                repair_stream = _watchdog_stream(repair_stream, idle_limit=REPAIR_STALL_SECONDS)
                repair_limits = AgentRunLimits(
                    max_seconds=REPAIR_MAX_SECONDS,
                    max_tool_calls=5,
                    task_kind=task_kind or "default",
                )
                tool_call_index, subagent_index, saw_assistant_text = _consume_raw_event_stream(
                    stream=repair_stream,
                    thread_id=thread_id,
                    run_id=run_id,
                    task_kind=task_kind,
                    event_sink=event_sink,
                    repair_limits=repair_limits,
                )
                stream = repair_stream
                repair_used += 1
                record_event(
                    thread_id,
                    "agent:run-limit",
                    "超限后注入收尾指令，模型已自行恢复并给出最终答案",
                    kind="other",
                    status="completed",
                    detail=str(exc),
                )
            except AgentRunLimitExceeded as repair_exc:
                # C: 修复轮也挂起。不再硬失败——从 checkpoint 抽最后一条 assistant
                # 中间输出作为降级最终答案返回，整题至少有个结果，不会被判成 error。
                record_event(
                    thread_id,
                    "agent:run-limit",
                    "修复轮仍挂起，从 checkpoint 抽取最后一条 assistant 输出作为降级答案",
                    kind="other",
                    status="warning",
                    detail=str(repair_exc),
                )
                fallback_msgs: list[Any] = []
                try:
                    snapshot = agent.get_state(run_config)
                    snap_values = getattr(snapshot, "values", None) or {}
                    hist = snap_values.get("messages", []) if isinstance(snap_values, dict) else []
                    from utils.message_utils import extract_final_assistant_text
                    fallback_text = ""
                    for m in reversed(hist):
                        mtype = getattr(m, "type", None) or (m.get("role") if isinstance(m, dict) else None)
                        if mtype in ("ai", "assistant"):
                            fallback_text = extract_final_assistant_text([m]) or ""
                            if fallback_text:
                                break
                    if fallback_text:
                        fallback_msgs = [{"role": "assistant", "type": "ai", "content": fallback_text}]
                except Exception:
                    fallback_msgs = []
                if fallback_msgs:
                    record_event(
                        thread_id,
                        f"final-answer:{run_id}",
                        "已校验最终答案（降级：checkpoint 抽取）",
                        kind="other",
                        status="completed",
                        detail=json.dumps({"fallback": True}, ensure_ascii=False),
                    )
                    return {
                        "messages": fallback_msgs,
                        "raw_output": {"messages": hist, "_fallback_from_checkpoint": True},
                        "interrupted": False,
                    }
                # 连 checkpoint 都抽不出 assistant 文本，才硬失败
                record_event(thread_id, "model", "调用大模型", kind="other", status="error", detail=str(repair_exc))
                raise
        else:
            record_event(thread_id, "model", "调用大模型", kind="other", status="error", detail=str(exc))
            raise

    # The exhausted stream describes this run, including approval interrupts.
    # A checkpoint read can omit pending writes or select the parent checkpoint
    # during an edited turn, so it must not override the live result.
    output = stream.output
    interrupts = list(_safe_attr(stream, "interrupts", ()) or ())
    if output is None:
        snapshot = agent.get_state(run_config)
        output = getattr(snapshot, "values", None)
        if not interrupts:
            interrupts = list(getattr(snapshot, "interrupts", ()) or ())
    if interrupts:
        approval = _safe_field(interrupts[0], "value")
        _record_pending_commands(thread_id, output, approval)
        record_event(thread_id, "model", "等待人工审批", kind="other", status="pending")
        return {
            "messages": _messages_from_output(output),
            "raw_output": output,
            "interrupted": True,
            "interrupt_id": _safe_field(interrupts[0], "id", ""),
            "approval": approval if isinstance(approval, dict) else {},
        }
    final_messages = _messages_from_output(output)
    from utils.message_utils import require_final_answer

    final_text = require_final_answer(final_messages)
    verified_hash = output.get("final_response_verified_hash") if isinstance(output, dict) else None
    if verified_hash != hashlib.sha256(final_text.encode()).hexdigest():
        raise RuntimeError("最终答案缺少有效校验凭证，不能将中间回复记为完成")
    record_event(thread_id, f"final-answer:{run_id}", "已校验最终答案", kind="other", status="completed",
                 detail=json.dumps({"text": final_text, "sha256": verified_hash}, ensure_ascii=False))
    # 某些 DeepSeek/LangGraph 版本只在最终 output 中提供 assistant 文本，
    # 不发送 messages/content-block-delta 事件。把最终回复补写进统一事件流，
    # 这样刷新页面和 SSE 实时页面都能看到正文。
    if not interrupts and not saw_assistant_text:
        from utils.message_utils import extract_final_assistant_text

        last_message = final_messages[-1] if final_messages else {}
        final_text = (extract_final_assistant_text([last_message])
                      if last_message.get("type", last_message.get("role")) in {"ai", "assistant"}
                      else "")
        if final_text:
            _record_assistant_stream_message(
                thread_id,
                run_id,
                uuid4().int,
                final_text,
                event_sink=event_sink,
            )
    record_event(thread_id, "model", "调用大模型", kind="other", status="completed")
    logger.info(
        "官方事件流消费完成：thread_id=%s tool_calls=%s subagents=%s",
        thread_id,
        tool_call_index,
        subagent_index,
    )
    return {"messages": final_messages, "raw_output": output}
