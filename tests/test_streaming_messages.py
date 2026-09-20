"""把最终输出转成消息字典时的保真度。

:func:`agent.runtime.streaming_runtime._message_dict` 是消息进入循环之前的最后一站。
它只保留 ``type`` 与 ``content`` 的话，模型返回的 token 用量就在这一步被丢掉，
自动化任务的 ``maxTokens`` 预算会永远算出 0 —— 一个不报错的静默失效。
"""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from agent.runtime.streaming_runtime import _message_dict


def test_token_usage_survives_the_conversion() -> None:
    """带用量的 AI 消息转成字典之后，用量还在。"""
    message = AIMessage(
        content="好的",
        usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    )
    payload = _message_dict(message)
    assert payload["type"] == "ai"
    assert payload["content"] == "好的"
    assert payload["usage_metadata"]["total_tokens"] == 15


def test_messages_without_usage_do_not_get_an_empty_key() -> None:
    """没有用量的消息不该凭空多出一个空键。"""
    assert "usage_metadata" not in _message_dict(HumanMessage(content="你好"))


def test_serialized_message_dicts_keep_their_usage() -> None:
    """已经是字典的消息（部分兼容模型这样返回）同样要保住用量。"""
    payload = _message_dict(
        {"type": "ai", "content": "好的", "usage_metadata": {"total_tokens": 3}}
    )
    assert payload["type"] == "ai"
    assert payload["usage_metadata"]["total_tokens"] == 3
