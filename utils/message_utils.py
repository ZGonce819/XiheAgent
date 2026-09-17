"""LangChain 消息内容处理工具。

LangChain / DeepAgents 的消息 ``content`` 字段可能是纯字符串，也可能是多模态
content block 列表（包含文本、图片等）。本模块提供统一的文本提取和方案抽取
能力，避免各调用点重复兼容不同消息结构。
"""

from __future__ import annotations

from typing import Any


def message_content_to_text(content: Any) -> str:
    """把 LangChain message.content 规整成可展示文本。

    Args:
        content: 消息内容，可能是 ``str``、``list``（多模态 content block）或 ``None``。

    Returns:
        提取后的纯文本内容，两端空白已去除。
    """

    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    parts.append(str(text))
        return "\n".join(part.strip() for part in parts if part and part.strip()).strip()
    return str(content).strip() if content is not None else ""


def extract_final_assistant_text(messages: list[dict[str, Any]]) -> str:
    """从消息列表中提取最后一条 assistant/ai 消息的文本。

    任务结束后，仓库记忆更新只需要最终总结，不应该把中间工具消息、todo 状态或
    调试事件写进长期记忆。因此从后往前找最后一条有正文的 AI/assistant 消息。

    Args:
        messages: checkpoint 或运行时产生的消息字典列表。

    Returns:
        最后一条 assistant 消息的文本，没有则返回空字符串。
    """

    for message in reversed(messages):
        message_type = str(message.get("type") or "").lower()
        if message_type not in {"ai", "assistant", "aimessage", "chatgeneration"}:
            continue
        text = message_content_to_text(message.get("content"))
        if text:
            return text
    return ""


def extract_best_plan_text(messages: list[dict[str, Any]]) -> str:
    """从多条 assistant 消息中提取最完整的技术方案。

    DeepAgents 有时会把"完整方案"和"是否确认实施该方案？"拆成不同 assistant
    消息。如果只取最后一条，前端就只剩确认句。方案任务应优先选择包含方案关键词
    且篇幅最长的 assistant 消息；没有命中时再退回最长 assistant 消息。

    Args:
        messages: checkpoint 或运行时产生的消息字典列表。

    Returns:
        最完整的技术方案文本，没有则返回空字符串。
    """

    candidates: list[str] = []
    for message in messages:
        message_type = str(message.get("type") or "").lower()
        if message_type not in {"ai", "assistant"}:
            continue
        text = message_content_to_text(message.get("content"))
        if text:
            candidates.append(text)
    if not candidates:
        return ""

    plan_keywords = [
        "方案",
        "技术方案",
        "修复技术方案",
        "实施步骤",
        "修复依据",
        "验证方案",
        "风险",
        "涉及模块",
        "数据结构",
        "是否确认实施该方案",
    ]
    plan_candidates = [
        text for text in candidates if any(keyword in text for keyword in plan_keywords)
    ]
    selected_pool = plan_candidates or candidates
    return max(selected_pool, key=len).strip()
