"""Checkpoint 历史消息提取模块。

从 LangGraph checkpoint 中提取用户可见的消息历史，供前端对话列表和详情页展示。

为什么需要这个模块：
  LangGraph 的 checkpoint 保存了完整的图执行状态，包括中间过程消息、
  工具调用结果、系统消息等。但前端只需要展示用户发送的提问和 Agent 的
  最终回复，不需要展示内部过程。

  本模块负责从 checkpoint 中过滤出"可见"的消息，并做去重和格式化，
  使得前端可以直接渲染对话历史。

设计要点：
  1. 用户消息需要从 LangGraph 包装的 HumanMessage 中提取原始文本。
  2. 过短的过程消息（如"确认"、"好的"）会被过滤掉。
  3. 相同内容的消息通过内容哈希去重，避免重复展示。
  4. 每条消息生成稳定的 ID，前端可以基于 ID 做增量更新。
"""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# 低于此长度的过程消息会被过滤掉，避免前端展示无意义的中间步骤。
_MIN_MESSAGE_LENGTH = 4


def _content_to_text(content: Any) -> str:
    """把 LangChain message content 转换成纯文本。

    content 可能是字符串或 content block 列表，不同模型和不同流式阶段
    的结构不同。这里统一处理，提取所有可读文本。

    Args:
        content: message.content 字段，可能是 str、list 或 None。

    Returns:
        提取的纯文本，空白已压缩。
    """

    if content is None:
        return ""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content") or ""
                if isinstance(text, str) and text.strip():
                    parts.append(text)
        return "\n".join(parts).strip()
    return str(content).strip()


def _extract_user_prompt(messages: list[Any]) -> str:
    """从消息列表中提取用户原始提问。

    LangGraph 在执行过程中可能会把用户消息包装成 HumanMessage，
    甚至拆分成多条。这里取第一条有意义的用户消息作为任务标题。

    Args:
        messages: checkpoint 中提取的消息列表。

    Returns:
        用户提问文本，找不到时返回空字符串。
    """

    for message in messages:
        msg_type = str(getattr(message, "type", "") or type(message).__name__).lower()
        if msg_type not in {"human", "user"}:
            continue
        text = _content_to_text(getattr(message, "content", ""))
        if len(text) >= _MIN_MESSAGE_LENGTH:
            return text
    return ""


def _has_visible_markdown_value(message: Any) -> bool:
    """判断消息是否包含值得展示的 Markdown 内容。

    不是所有 AI 消息都适合展示给用户：有些只是过程确认或空响应。
    这里检查消息内容是否包含有意义的文本（代码块、列表、标题等）。

    Args:
        message: LangChain message 对象。

    Returns:
        消息内容足够丰富时返回 True。
    """

    text = _content_to_text(getattr(message, "content", ""))
    if len(text) < _MIN_MESSAGE_LENGTH:
        return False
    # 包含 Markdown 结构特征的消息优先展示。
    markdown_markers = ["#", "```", "- ", "* ", "|", "```"]
    return any(marker in text for marker in markdown_markers) or len(text) > 50


def stable_history_message_id(content: str, index: int) -> str:
    """为历史消息生成稳定的唯一 ID。

    基于内容哈希和位置索引，确保相同内容在不同时间提取时得到相同 ID。
    前端可以基于此 ID 做增量更新和去重。

    Args:
        content: 消息文本内容。
        index: 消息在历史中的位置索引。

    Returns:
        格式为 ``hist-{hash[:12]}`` 的稳定 ID。
    """

    digest = hashlib.sha256(f"{index}:{content}".encode()).hexdigest()[:12]
    return f"hist-{digest}"


def _is_short_process_message(text: str) -> bool:
    """判断消息是否属于过短的过程消息。

    Agent 执行过程中会产生大量短消息（如"好的"、"确认"、"开始执行"），
    这些消息对用户没有信息价值，应该从展示历史中过滤掉。

    Args:
        text: 消息文本。

    Returns:
        是过程消息时返回 True。
    """

    stripped = text.strip()
    if len(stripped) < _MIN_MESSAGE_LENGTH:
        return True
    # 常见的无意义过程消息模式。
    process_patterns = [
        r"^(好的|确认|收到|开始|继续|下一步|ok|yes|sure)$",
        r"^(正在|让我|我来|好的，)",
    ]
    return any(re.match(pattern, stripped, re.IGNORECASE) for pattern in process_patterns)


def visible_checkpoint_messages(checkpoint: dict[str, Any] | None) -> list[dict[str, str]]:
    """从 checkpoint 中提取用户可见的消息列表。

    这是本模块的核心入口函数。前端对话详情页调用此函数获取可展示的历史消息。

    处理流程：
      1. 从 checkpoint 中读取完整消息列表。
      2. 过滤掉系统消息、工具消息和过短的过程消息。
      3. 提取用户提问和 AI 回复，按顺序组装。
      4. 通过内容哈希去重，避免重复展示。

    Args:
        checkpoint: LangGraph checkpoint 字典。为 None 时返回空列表。

    Returns:
        消息字典列表，每项包含 ``id``、``role``、``content`` 字段。
    """

    if not checkpoint:
        return []

    channel_values = checkpoint.get("channel_values")
    if not isinstance(channel_values, dict):
        return []

    raw_messages = channel_values.get("messages")
    if not isinstance(raw_messages, list):
        return []

    result: list[dict[str, str]] = []
    seen_hashes: set[str] = set()

    for index, message in enumerate(raw_messages):
        msg_type = str(getattr(message, "type", "") or type(message).__name__).lower()

        # 跳过工具消息和系统消息，前端不需要展示这些。
        if msg_type in {"tool", "system"}:
            continue

        text = _content_to_text(getattr(message, "content", ""))
        if not text:
            continue

        # 过滤过短的过程消息。
        if msg_type in {"human", "user"} and _is_short_process_message(text):
            continue

        # 内容哈希去重。
        content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
        if content_hash in seen_hashes:
            continue
        seen_hashes.add(content_hash)

        role = "user" if msg_type in {"human", "user"} else "assistant"
        message_id = stable_history_message_id(text, index)
        result.append({
            "id": message_id,
            "role": role,
            "content": text,
        })

    return result
