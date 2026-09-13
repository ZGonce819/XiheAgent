"""Xihe Agent 模型工厂模块。

负责创建 Agent 使用的大语言模型实例。当前项目统一使用 DeepSeek 兼容接口，
通过 OpenAI 协议访问。模型分为两类：

- 主模型（main model）：用于 Agent 推理和工具调用，支持流式输出。
- 意图分类模型（intent model）：用于快速判断用户任务类型，不需要流式。

所有模型参数均从环境变量读取，由 ``config.env_config`` 统一加载。
"""

from __future__ import annotations

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from config.constants import DEEPSEEK_V4_MAX_TOKENS, INTENT_MODEL_MAX_TOKENS
from config.env_config import get_env, require_env


def make_main_model() -> BaseChatModel:
    """创建 Agent 主推理模型。

    返回的模型实例用于 Agent 循环中的推理、工具调用和最终回复生成。
    启用了流式输出以支持 SSE 实时推送给前端。

    Returns:
        配置好的 DeepSeek 兼容 ChatModel 实例。

    Raises:
        RuntimeError: 当 DEEPSEEK_API_KEY 或 DEEPSEEK_BASE_URL 未配置时。
    """
    return init_chat_model(
        model=get_env("MAIN_MODEL", "deepseek-v4-pro"),
        model_provider="openai",
        temperature=1.1,
        api_key=require_env("DEEPSEEK_API_KEY"),
        base_url=require_env("DEEPSEEK_BASE_URL"),
        max_tokens=DEEPSEEK_V4_MAX_TOKENS,
        streaming=True,
        extra_body={"thinking": {"type": "disabled"}},
    )


def make_intent_model() -> BaseChatModel:
    """创建意图分类模型。

    意图分类只需要结构化输出（JSON），不需要流式或长文本生成。
    使用较轻量的配置以降低首次调用延迟。

    Returns:
        配置好的 DeepSeek 兼容 ChatModel 实例。

    Raises:
        RuntimeError: 当 DEEPSEEK_API_KEY 或 DEEPSEEK_BASE_URL 未配置时。
    """
    return init_chat_model(
        model=get_env("INTENT_MODEL", get_env("MAIN_MODEL", "deepseek-v4-pro")),
        model_provider="openai",
        temperature=0.0,
        api_key=require_env("DEEPSEEK_API_KEY"),
        base_url=require_env("DEEPSEEK_BASE_URL"),
        max_tokens=INTENT_MODEL_MAX_TOKENS,
    )
