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

import base64
import hashlib
import json
import logging
import re
from typing import Any

from config.constants import MIN_MESSAGE_LENGTH
from utils.message_utils import message_content_to_text

logger = logging.getLogger(__name__)


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
        text = message_content_to_text(getattr(message, "content", ""))
        text = _extract_visible_user_input(text)
        if len(text) >= MIN_MESSAGE_LENGTH:
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
    text = message_content_to_text(getattr(message, "content", ""))
    if len(text) < MIN_MESSAGE_LENGTH:
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
    if len(stripped) < MIN_MESSAGE_LENGTH:
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
        if isinstance(message, dict):
            msg_type = str(message.get("type") or message.get("role") or "").lower()
            raw_content = message.get("content") or message.get("text") or ""
            message_id_value = message.get("id")
        else:
            msg_type = str(getattr(message, "type", "") or type(message).__name__).lower()
            raw_content = getattr(message, "content", "")
            message_id_value = getattr(message, "id", "")

        # 跳过工具消息和系统消息，前端不需要展示这些。
        if msg_type in {"tool", "system"}:
            continue

        text = message_content_to_text(raw_content)
        if msg_type in {"human", "user"}:
            text = _extract_visible_user_input(text)
        if not text:
            continue

        role = "user" if msg_type in {"human", "user"} else "assistant"
        # 只对 assistant 消息做内容哈希去重，用户消息即使内容相同也必须保留，
        # 因为多轮对话中用户可能反复输入"确认"等短文本，每条都是独立轮次。
        if role == "assistant":
            content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)

        message_id = str(message_id_value or stable_history_message_id(text, index))
        result.append({
            "id": message_id,
            "role": role,
            "content": text,
        })

    return result


def _msg_class_from_lc_dict(msg: dict[str, Any]) -> str:
    """从 LangChain 序列化字典中提取消息类名。

    Args:
        msg: LangChain 序列化后的消息字典。

    Returns:
        消息类名（如 ``HumanMessage``、``AIMessage``），无法识别时返回空字符串。
    """
    msg_id = msg.get("id")
    if isinstance(msg_id, list) and msg_id:
        return str(msg_id[-1]).lower()
    return ""


def _msg_id_from_lc_dict(msg: dict[str, Any]) -> str:
    """从 LangChain 序列化字典中提取消息唯一 ID。

    Args:
        msg: LangChain 序列化后的消息字典。

    Returns:
        消息 ID 字符串；无法提取时返回空字符串。
    """
    kwargs = msg.get("kwargs")
    if isinstance(kwargs, dict):
        value = kwargs.get("id")
        if isinstance(value, str) and value:
            return value
    return ""


def _extract_visible_user_input(text: str) -> str:
    """从 HumanMessage 内容中提取用户可见输入部分。

    当前项目通过 ``ContextInjectionMiddleware`` 把用户原始输入包装成包含
    "用户可见输入"和"内部执行上下文"的复合文本。前端展示时只需要用户原始输入，
    不应把仓库地址、任务类型等内部上下文展示给用户。

    Args:
        text: HumanMessage 的完整文本内容。

    Returns:
        用户可见输入部分；无法识别格式时返回原文本。
    """
    marker = "用户可见输入："
    if marker not in text:
        return text
    start = text.index(marker) + len(marker)
    rest = text[start:].lstrip("\n").lstrip()
    sep = "内部执行上下文："
    sep_pos = rest.find(sep)
    if sep_pos >= 0:
        return rest[:sep_pos].rstrip()
    return rest.rstrip()


def _build_checkpoint_lineage(
    redis_client: Any, thread_id: str
) -> list[str]:
    """构建从根到最新 checkpoint 的祖先链。

    LangGraph 的 checkpoint 通过 ``parent_checkpoint_id`` 形成父子链。
    当图多次执行时（例如用户发送多轮消息），会产生多条分支。
    只有沿着最新 checkpoint 回溯到根的路径上的 writes 才是有效消息，
    其他分支上的 writes 是过期/废弃的，不应展示。

    Args:
        redis_client: Redis 客户端实例。
        thread_id: LangGraph checkpoint 的会话标识。

    Returns:
        按从根到最新顺序排列的 checkpoint_id 列表。
    """
    # 1. 找到最新 checkpoint 的指针 key。
    latest_pattern = f"checkpoint_latest:{thread_id}:*"
    latest_checkpoint_key: str | None = None
    try:
        latest_keys = redis_client.keys(latest_pattern)
        if latest_keys:
            raw = latest_keys[0]
            latest_key_str = raw.decode() if isinstance(raw, bytes) else raw
            # checkpoint_latest key 存储的是纯字符串值（指向实际 checkpoint key）。
            pointed = redis_client.get(latest_key_str)
            if pointed:
                latest_checkpoint_key = pointed.decode() if isinstance(pointed, bytes) else pointed
    except Exception:
        logger.warning("查找最新 checkpoint 失败：thread_id=%s", thread_id, exc_info=True)
        return []

    if not latest_checkpoint_key:
        return []

    # 2. 读取最新 checkpoint 获取其 checkpoint_id。
    try:
        latest_data = redis_client.json().get(latest_checkpoint_key)
    except Exception:
        logger.warning("读取最新 checkpoint 失败：key=%s", latest_checkpoint_key, exc_info=True)
        return []

    if not isinstance(latest_data, dict):
        return []

    latest_checkpoint_id = str(latest_data.get("checkpoint_id", ""))
    if not latest_checkpoint_id:
        return []

    # 3. 扫描该 thread 的所有 checkpoint，构建 checkpoint_id → parent_checkpoint_id 映射。
    cp_pattern = f"checkpoint:{thread_id}:*"
    parent_of: dict[str, str] = {}
    checkpoint_ids_set: set[str] = set()

    try:
        cp_keys = redis_client.keys(cp_pattern)
    except Exception:
        logger.warning("扫描 checkpoint 失败：thread_id=%s", thread_id, exc_info=True)
        return []

    for key in cp_keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        if key_str.startswith("checkpoint_latest:"):
            continue
        try:
            data = redis_client.json().get(key_str)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        cp_id = str(data.get("checkpoint_id", ""))
        parent_id = str(data.get("parent_checkpoint_id", "") or "")
        if not cp_id:
            continue
        checkpoint_ids_set.add(cp_id)
        if parent_id:
            parent_of[cp_id] = parent_id

    if latest_checkpoint_id not in checkpoint_ids_set:
        return []

    # 4. 从最新 checkpoint 沿 parent 链回溯到根，收集 lineage 上的所有 checkpoint_id。
    lineage_ordered: list[str] = []
    current = latest_checkpoint_id
    visited: set[str] = set()
    while current and current not in visited:
        visited.add(current)
        lineage_ordered.append(current)
        current = parent_of.get(current, "")

    lineage_ordered.reverse()
    return lineage_ordered


def extract_messages_from_writes(redis_client: Any, thread_id: str) -> list[dict[str, str]]:
    """从 Redis checkpoint write 条目中重建可见消息列表。

    当前版本的 Redis checkpointer 把消息以 ``checkpoint_write:*`` 键的形式存储，
    但不会把累积结果写入 ``channel_values.messages``。本函数先构建 checkpoint
    祖先链（从根到最新），只处理祖先链上的 writes，避免不同执行分支的 writes
    混在一起导致消息重复。

    Args:
        redis_client: Redis 客户端实例（来自 checkpointer 或直连）。
        thread_id: LangGraph checkpoint 的会话标识。

    Returns:
        可见消息字典列表，每项包含 ``id``、``role``、``content``。
    """
    lineage = _build_checkpoint_lineage(redis_client, thread_id)
    if not lineage:
        return []

    lineage_set = set(lineage)
    lineage_order = {cp_id: i for i, cp_id in enumerate(lineage)}

    pattern = f"checkpoint_write:{thread_id}:*"
    keys: list[bytes] = []
    try:
        keys = list(redis_client.keys(pattern))
    except Exception:
        logger.warning("扫描 checkpoint writes 失败：thread_id=%s", thread_id, exc_info=True)
        return []

    message_writes: list[tuple[int, int, list[dict[str, Any]]]] = []
    for key in keys:
        key_str = key.decode() if isinstance(key, bytes) else key
        try:
            data = redis_client.json().get(key_str)
        except Exception:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("channel") != "messages":
            continue

        checkpoint_id = str(data.get("checkpoint_id", ""))
        if checkpoint_id not in lineage_set:
            continue

        blob = data.get("blob")
        if not isinstance(blob, str) or not blob:
            continue
        idx = int(data.get("idx", 0))
        try:
            decoded_bytes = base64.b64decode(blob)
            messages = json.loads(decoded_bytes.decode("utf-8"))
        except Exception:
            logger.debug("解码 checkpoint write blob 失败：key=%s", key_str, exc_info=True)
            continue
        if isinstance(messages, list):
            order = lineage_order.get(checkpoint_id, 0)
            message_writes.append((order, idx, messages))

    message_writes.sort(key=lambda x: (x[0], x[1]))

    all_messages: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for _order, _idx, msgs in message_writes:
        for msg in msgs:
            if not isinstance(msg, dict):
                continue
            msg_class = _msg_class_from_lc_dict(msg)
            if msg_class in {"removemessage", "remove"}:
                continue
            msg_uid = _msg_id_from_lc_dict(msg)
            if msg_uid and msg_uid in seen_ids:
                continue
            if msg_uid:
                seen_ids.add(msg_uid)
            all_messages.append(msg)

    result: list[dict[str, str]] = []
    seen_hashes: set[str] = set()
    for index, msg in enumerate(all_messages):
        msg_class = _msg_class_from_lc_dict(msg)
        if msg_class in {"toolmessage", "tool"}:
            continue
        if msg_class in {"systemmessage", "system"}:
            continue

        kwargs = msg.get("kwargs", {})
        content = kwargs.get("content", "") if isinstance(kwargs, dict) else ""
        text = message_content_to_text(content)
        if msg_class in {"humanmessage", "human", "user"}:
            text = _extract_visible_user_input(text)
        if not text:
            continue

        role = "user" if msg_class in {"humanmessage", "human", "user"} else "assistant"
        if role == "assistant":
            content_hash = hashlib.sha256(text.encode()).hexdigest()[:16]
            if content_hash in seen_hashes:
                continue
            seen_hashes.add(content_hash)

        message_id = _msg_id_from_lc_dict(msg) or stable_history_message_id(text, index)
        result.append({
            "id": message_id,
            "role": role,
            "content": text,
        })

    return result


def checkpoint_before_latest_user(redis_client: Any, thread_id: str) -> str | None:
    """Return the checkpoint immediately before the latest user message.

    ``None`` means the latest user message is the first turn, so callers should
    start the thread from an empty checkpoint. Writes are scanned along the
    active lineage and message ids are deduplicated because some saver versions
    persist cumulative message lists.
    """

    lineage = _build_checkpoint_lineage(redis_client, thread_id)
    if not lineage:
        return None

    order = {checkpoint_id: index for index, checkpoint_id in enumerate(lineage)}
    user_checkpoints: list[str] = []
    seen_users: set[str] = set()
    entries: list[tuple[int, int, str, list[dict[str, Any]]]] = []

    for raw_key in redis_client.keys(f"checkpoint_write:{thread_id}:*"):
        key = raw_key.decode() if isinstance(raw_key, bytes) else raw_key
        try:
            data = redis_client.json().get(key)
        except Exception:
            continue
        if not isinstance(data, dict) or data.get("channel") != "messages":
            continue
        checkpoint_id = str(data.get("checkpoint_id") or "")
        if checkpoint_id not in order:
            continue
        blob = data.get("blob")
        if not isinstance(blob, str) or not blob:
            continue
        try:
            decoded = json.loads(base64.b64decode(blob).decode("utf-8"))
        except Exception:
            continue
        if isinstance(decoded, list):
            entries.append((order[checkpoint_id], int(data.get("idx", 0)), checkpoint_id, decoded))

    entries.sort(key=lambda item: (item[0], item[1]))
    for _order, _idx, checkpoint_id, messages in entries:
        for message in messages:
            if not isinstance(message, dict):
                continue
            message_class = _msg_class_from_lc_dict(message)
            if message_class not in {"humanmessage", "human", "user"}:
                continue
            kwargs = message.get("kwargs")
            content = kwargs.get("content", "") if isinstance(kwargs, dict) else ""
            identity = _msg_id_from_lc_dict(message) or hashlib.sha256(
                message_content_to_text(content).encode()
            ).hexdigest()
            if identity in seen_users:
                continue
            seen_users.add(identity)
            user_checkpoints.append(checkpoint_id)

    if not user_checkpoints:
        return None
    latest_user_checkpoint = user_checkpoints[-1]
    latest_index = order[latest_user_checkpoint]
    return lineage[latest_index - 1] if latest_index > 0 else None
