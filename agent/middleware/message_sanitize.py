"""模型消息兼容清洗中间件。

只处理"发送给大模型之前"的消息副本，不修改 checkpoint 中保存的真实历史，
也不修改前端要展示的对话内容。

为什么需要它：
  当前项目使用 DeepAgents + FastAPI 承载 Agent 运行，并使用 DeepSeek 兼容的
  OpenAI Chat Completions 接口。DeepAgents / LangChain 在多工具调用、工具调用
  解析失败或历史消息恢复时，可能在 AIMessage 中保留 ``invalid_tool_calls``，
  或把 content 组织成包含 ``invalid_tool_call``、``tool_call_chunk`` 等类型的
  content block。这些内容对 LangChain 内部是有意义的，但部分 OpenAI 兼容接口
  并不能接受。典型报错如下::

    messages[301]: unknown variant `invalid_tool_call`, expected `text`

设计边界：
  1. 不删除用户消息，不改写用户输入。
  2. 不持久化清洗结果，避免污染 checkpoint 历史。
  3. 保留正常 tool_calls，保证 LangGraph 工具调用链路仍能配对。
  4. 只移除 invalid_tool_calls 和不适合发给模型服务端的非文本 content block。
  5. 清洗后的内容只用于当前这一次模型请求。
"""

from __future__ import annotations

import logging
from typing import Any, Awaitable, Callable

from langchain.agents.middleware.types import (
    AgentMiddleware,
    AgentState,
    ModelRequest,
    ModelResponse,
)
from langchain_core.messages import AIMessage, AnyMessage, BaseMessage, ToolMessage

from config.constants import NON_TEXT_TOOL_BLOCK_TYPES, TEXT_BLOCK_TYPES

logger = logging.getLogger(__name__)


def _text_from_content_block(block: dict[str, Any]) -> str:
    """从 content block 中提取可发送给模型的文本。

    非文本工具调用块（如 ``invalid_tool_call``、``tool_call``）返回空字符串；
    文本类型块（如 ``text``、``output_text``）直接提取 ``text`` 或 ``content`` 字段。

    Args:
        block: LangChain message content 中的单个 dict 元素。

    Returns:
        可发送文本；非文本块或无文本内容时返回空字符串。
    """
    block_type = block.get("type")
    if block_type in NON_TEXT_TOOL_BLOCK_TYPES:
        return ""
    if block_type in TEXT_BLOCK_TYPES:
        for key in ("text", "content"):
            value = block.get(key)
            if isinstance(value, str):
                return value
        return ""
    for key in ("text", "content"):
        value = block.get(key)
        if isinstance(value, str):
            return value
    return ""


def _tool_call_from_content_block(block: dict[str, Any]) -> dict[str, Any] | None:
    """从 content block 中提取合法 tool_call。

    部分历史消息的 ``message.tool_calls`` 可能为空，但 content 列表里仍保留了
    ``{"type": "tool_call", ...}``。如果直接丢掉这些块，后面的 ToolMessage 就会
    变成"孤立工具消息"，进而触发 provider 400。

    Args:
        block: content 列表中的单个 dict 元素。

    Returns:
        合法的 tool_call 字典；类型不匹配或无效时返回 ``None``。
    """
    if block.get("type") != "tool_call":
        return None
    tool_call = {
        "id": block.get("id"),
        "name": block.get("name"),
        "args": block.get("args") if isinstance(block.get("args"), dict) else {},
    }
    return tool_call if _is_valid_tool_call(tool_call) else None


def _tool_calls_from_content(content: Any) -> list[dict[str, Any]]:
    """从 message.content 中提取可恢复的正常 tool_calls。

    遍历 content 列表，逐条尝试提取合法 tool_call，并按 ``id`` 去重。

    Args:
        content: message 的 content 字段，可能是 list 或其他类型。

    Returns:
        提取到的合法 tool_call 列表；非列表输入时返回空列表。
    """
    if not isinstance(content, list):
        return []
    tool_calls: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in content:
        if not isinstance(item, dict):
            continue
        tool_call = _tool_call_from_content_block(item)
        if tool_call is None:
            continue
        tool_call_id = tool_call["id"]
        if tool_call_id in seen_ids:
            continue
        seen_ids.add(tool_call_id)
        tool_calls.append(tool_call)
    return tool_calls


def sanitize_message_content(content: Any) -> str:
    """把任意 LangChain message content 清洗成普通字符串。

    处理三种 content 形态：``None`` → 空字符串；纯字符串 → 原样返回；
    ``list[dict | str]`` → 提取所有文本块并拼接。

    Args:
        content: LangChain message 的 content 字段。

    Returns:
        清洗后的纯文本字符串。
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                if item:
                    parts.append(item)
            elif isinstance(item, dict):
                text = _text_from_content_block(item)
                if text:
                    parts.append(text)
        return "\n".join(parts)
    return str(content)


def _sanitize_additional_kwargs(value: Any) -> Any:
    """递归移除 additional_kwargs 中的无效工具调用痕迹。

    部分 provider adapter 会把 additional_kwargs 原样序列化到请求体中，
    因此即使 ``AIMessage.invalid_tool_calls`` 已清空，也需要把
    additional_kwargs 里的同名字段一并清掉。

    Args:
        value: additional_kwargs 中的值，可能是 dict、list 或基本类型。

    Returns:
        清洗后的值，结构保持不变。
    """
    if isinstance(value, dict):
        cleaned: dict[Any, Any] = {}
        for key, child in value.items():
            if key == "invalid_tool_calls":
                continue
            cleaned[key] = _sanitize_additional_kwargs(child)
        return cleaned
    if isinstance(value, list):
        result: list[Any] = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "invalid_tool_call":
                continue
            result.append(_sanitize_additional_kwargs(item))
        return result
    return value


def _is_valid_tool_call(tool_call: Any) -> bool:
    """判断 tool_call 是否可以保留给 LangGraph 工具链路使用。

    合法条件：是 dict、类型不是 ``invalid_tool_call``、且同时具备
    ``name``（str）和 ``id``（str）字段。

    Args:
        tool_call: 待校验的工具调用 dict。

    Returns:
        ``True`` 表示可保留；``False`` 表示应丢弃。
    """
    if not isinstance(tool_call, dict):
        return False
    if tool_call.get("type") == "invalid_tool_call":
        return False
    return isinstance(tool_call.get("name"), str) and isinstance(tool_call.get("id"), str)


def _sanitize_ai_message(message: AIMessage) -> AIMessage:
    """清洗 AIMessage 中 provider 不兼容的字段。

    正常 tool_calls 保留，invalid_tool_calls 一律移除。
    同时从 content 列表中尝试恢复被嵌入的 tool_call，并清理 additional_kwargs。

    Args:
        message: 待清洗的 AIMessage。

    Returns:
        清洗后的 AIMessage 副本，原消息不变。
    """
    content = sanitize_message_content(message.content)
    tool_calls: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for tool_call in [*message.tool_calls, *_tool_calls_from_content(message.content)]:
        if not _is_valid_tool_call(tool_call):
            continue
        tool_call_id = tool_call["id"]
        if tool_call_id in seen_ids:
            continue
        seen_ids.add(tool_call_id)
        tool_calls.append(tool_call)
    return message.model_copy(
        update={
            "content": content,
            "tool_calls": tool_calls,
            "invalid_tool_calls": [],
            "additional_kwargs": _sanitize_additional_kwargs(message.additional_kwargs),
        },
    )


def sanitize_message_for_model(message: AnyMessage) -> AnyMessage:
    """清洗单条消息，返回可发送给模型的消息副本。

    AIMessage 走完整清洗流程（content + tool_calls + invalid_tool_calls）；
    其他 BaseMessage 子类只清洗 content。

    Args:
        message: 任意 LangChain 消息对象。

    Returns:
        清洗后的同类型消息浅副本。
    """
    if isinstance(message, AIMessage):
        return _sanitize_ai_message(message)
    if isinstance(message, BaseMessage):
        return message.model_copy(update={"content": sanitize_message_content(message.content)})
    return message


def _tool_message_call_id(message: AnyMessage) -> str | None:
    """读取 ToolMessage 的 tool_call_id。

    Args:
        message: 任意 LangChain 消息对象。

    Returns:
        非空 tool_call_id 字符串；非 ToolMessage 或字段为空时返回 ``None``。
    """
    if isinstance(message, ToolMessage):
        value = message.tool_call_id
        return value if isinstance(value, str) and value else None
    return None


def _ai_tool_call_ids(message: AnyMessage) -> set[str]:
    """读取 AIMessage 中合法 tool_calls 的 id 集合。

    Args:
        message: 任意 LangChain 消息对象。

    Returns:
        合法 tool_call id 集合；非 AIMessage 时返回空集合。
    """
    if not isinstance(message, AIMessage):
        return set()
    return {
        tool_call["id"]
        for tool_call in message.tool_calls
        if isinstance(tool_call, dict) and isinstance(tool_call.get("id"), str)
    }


def _assistant_tool_calls_are_matched(
    messages: list[AnyMessage],
    index: int,
    tool_call_ids: set[str],
) -> bool:
    """判断 AIMessage 的 tool_calls 是否能被后续连续 ToolMessage 完整响应。

    OpenAI/DeepSeek 兼容接口要求：带 tool_calls 的 assistant 消息后面必须紧跟
    对应的 tool 消息。这里按最严格的方式判断——后续连续 ToolMessage 的 id 必须
    属于本次 assistant 的 tool_call_ids，每个 id 正好出现一次，中间一旦出现
    非 ToolMessage 就停止收集。

    Args:
        messages: 完整消息列表。
        index: 当前 AIMessage 在列表中的位置。
        tool_call_ids: 当前 AIMessage 中需要匹配的 tool_call id 集合。

    Returns:
        所有 tool_call 都被后续连续 ToolMessage 完整匹配时返回 ``True``。
    """
    expected = set(tool_call_ids)
    seen: set[str] = set()
    cursor = index + 1
    while cursor < len(messages):
        tool_call_id = _tool_message_call_id(messages[cursor])
        if tool_call_id is None:
            break
        if tool_call_id not in expected or tool_call_id in seen:
            return False
        seen.add(tool_call_id)
        cursor += 1
    return seen == expected


def sanitize_messages_for_model(messages: list[AnyMessage]) -> list[AnyMessage]:
    """按序列清洗一组消息。

    不能简单逐条清洗，因为工具消息有跨消息配对关系：
    ``AIMessage(tool_calls=[id=A])`` 后面必须跟着 ``ToolMessage(tool_call_id=A)``。
    先清洗单条消息，再按顺序保留合法工具调用组。

    Args:
        messages: 待清洗的消息列表。

    Returns:
        清洗后的消息列表，未配对的 ToolMessage 会被移除。
    """
    first_pass = [sanitize_message_for_model(message) for message in messages]
    cleaned: list[AnyMessage] = []
    pending_tool_call_ids: set[str] = set()

    for index, message in enumerate(first_pass):
        if isinstance(message, AIMessage):
            tool_call_ids = _ai_tool_call_ids(message)
            if tool_call_ids and not _assistant_tool_calls_are_matched(first_pass, index, tool_call_ids):
                message = message.model_copy(update={"tool_calls": []})
                tool_call_ids = set()
            pending_tool_call_ids = set(tool_call_ids)
            cleaned.append(message)
            continue

        tool_call_id = _tool_message_call_id(message)
        if tool_call_id is not None:
            if tool_call_id not in pending_tool_call_ids:
                logger.debug("丢弃孤立 ToolMessage：tool_call_id=%s", tool_call_id)
                continue
            pending_tool_call_ids.remove(tool_call_id)
            cleaned.append(message)
            continue

        pending_tool_call_ids.clear()
        cleaned.append(message)

    return cleaned


def _message_changed(before: AnyMessage, after: AnyMessage) -> bool:
    """判断清洗前后是否发生变化，用于输出诊断日志。

    优先使用 ``model_dump()`` 做结构化比较；序列化失败时回退到 ``!=`` 比较。

    Args:
        before: 清洗前的消息对象。
        after: 清洗后的消息对象。

    Returns:
        ``True`` 表示清洗产生了变化。
    """
    try:
        return before.model_dump() != after.model_dump()
    except Exception:
        return before != after


class MessageSanitizeMiddleware(AgentMiddleware):
    """在模型调用前清洗历史消息中的 provider 不兼容内容。

    DeepAgents 会从 checkpointer 中恢复完整消息历史。历史里如果包含
    ``invalid_tool_calls``，即使当前用户只是输入"确认实施"，下一次模型调用仍会
    把旧消息一并发给 provider，从而触发 400。该中间件放在模型调用链靠前位置，
    确保每次请求都先经过兼容清洗。
    """

    state_schema = AgentState

    def _clean_request(self, request: ModelRequest[Any]) -> ModelRequest[Any]:
        """对 ModelRequest 中的消息执行兼容清洗。

        同时清洗 ``request.messages`` 和 ``request.state["messages"]``，
        并在有变化时输出诊断日志。

        Args:
            request: 即将发送给模型的请求对象。

        Returns:
            清洗后的新 ModelRequest（原对象不被修改）。
        """
        cleaned_messages = sanitize_messages_for_model(list(request.messages))
        state = dict(request.state)
        state_messages = state.get("messages")
        if isinstance(state_messages, list):
            state["messages"] = sanitize_messages_for_model(state_messages)

        changed_count = sum(
            1
            for before, after in zip(request.messages, cleaned_messages, strict=False)
            if _message_changed(before, after)
        )
        if changed_count:
            logger.info(
                "模型消息兼容清洗完成：request_messages=%s changed=%s state_messages=%s",
                len(cleaned_messages),
                changed_count,
                len(state.get("messages", [])),
            )
        return request.override(messages=cleaned_messages, state=state)

    def wrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], ModelResponse[Any]],
    ) -> ModelResponse[Any]:
        """同步模型调用包装：先清洗再交给下游 handler。

        Args:
            request: 即将发送的模型请求。
            handler: 下游模型调用处理函数。

        Returns:
            下游返回的模型响应。
        """
        return handler(self._clean_request(request))

    async def awrap_model_call(
        self,
        request: ModelRequest[Any],
        handler: Callable[[ModelRequest[Any]], Awaitable[ModelResponse[Any]]],
    ) -> ModelResponse[Any]:
        """异步模型调用包装：先清洗再交给下游 handler。

        Args:
            request: 即将发送的模型请求。
            handler: 下游异步模型调用处理函数。

        Returns:
            下游返回的模型响应。
        """
        return await handler(self._clean_request(request))
