"""模型工厂模块。

负责创建 Agent 使用的大语言模型实例。当前项目统一使用 OpenAI 兼容接口。
模型分为两类：

- 主模型（main model）：用于 Agent 推理和工具调用，支持流式输出。
- 意图分类模型（intent model）：用于快速判断用户任务类型，不需要流式。

优先使用自定义模型（通过 API 配置），其次使用内置模型用户覆盖，回退到 .env 环境变量配置。
"""

from __future__ import annotations

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel

from config.constants import DEEPSEEK_V4_MAX_TOKENS, INTENT_MODEL_MAX_TOKENS
from config.env_config import get_env, require_env
from config.model_config import get_builtin_override, get_custom_model, get_default_custom_model


def _build_model_from_custom(
    custom: dict, *, streaming: bool, max_tokens: int, temperature: float,
) -> BaseChatModel:
    """根据自定义模型配置创建 ChatModel 实例。

    Args:
        custom: 自定义模型配置字典，需包含 ``model_name``、``api_key``、
            ``api_base`` 字段。
        streaming: 是否启用流式输出。
        max_tokens: 模型最大生成 token 数。
        temperature: 采样温度，值越高输出越随机。

    Returns:
        配置好的 BaseChatModel 实例。
    """
    return init_chat_model(
        model=custom["model_name"],
        model_provider="openai",
        temperature=temperature,
        api_key=custom["api_key"],
        base_url=custom["api_base"],
        max_tokens=max_tokens,
        streaming=streaming,
    )


def make_main_model(model_id: str | None = None) -> BaseChatModel:
    """创建 Agent 主推理模型。

    优先使用指定的自定义模型；未指定时使用默认自定义模型；
    都没有时回退到 .env 中的 DeepSeek 配置。
    启用了流式输出以支持 SSE 实时推送给前端。

    Args:
        model_id: 自定义模型 ID，为 ``None`` 时使用默认模型。

    Returns:
        配置好的 ChatModel 实例。

    Raises:
        RuntimeError: 当必要的 API Key 或 Base URL 未配置时。
    """
    custom = None
    if model_id:
        custom = get_custom_model(model_id)
    if custom is None:
        custom = get_default_custom_model()
    if custom is None:
        builtin = get_builtin_override()
        if builtin:
            custom = builtin
    if custom:
        return _build_model_from_custom(
            custom, streaming=True,
            max_tokens=custom.get("max_tokens", 4096),
            temperature=custom.get("temperature", 0.7),
        )
    return init_chat_model(
        model=get_env("MAIN_MODEL", "deepseek-v4-pro"),
        model_provider="openai",
        temperature=1.1,
        api_key=require_env("DEEPSEEK_API_KEY"),
        base_url=require_env("DEEPSEEK_BASE_URL"),
        max_tokens=DEEPSEEK_V4_MAX_TOKENS,
        streaming=True,
    )


def make_intent_model(model_id: str | None = None) -> BaseChatModel:
    """创建意图分类模型。

    优先使用指定的自定义模型；未指定时使用默认自定义模型；
    都没有时回退到 .env 配置。
    意图分类只需要结构化输出（JSON），不需要流式或长文本生成。

    Args:
        model_id: 自定义模型 ID，为 ``None`` 时使用默认模型。

    Returns:
        配置好的 ChatModel 实例。

    Raises:
        RuntimeError: 当必要的 API Key 或 Base URL 未配置时。
    """
    custom = None
    if model_id:
        custom = get_custom_model(model_id)
    if custom is None:
        custom = get_default_custom_model()
    if custom is None:
        builtin = get_builtin_override()
        if builtin:
            custom = builtin
    if custom:
        return _build_model_from_custom(
            custom, streaming=False,
            max_tokens=INTENT_MODEL_MAX_TOKENS,
            temperature=0.0,
        )
    return init_chat_model(
        model=get_env("INTENT_MODEL", get_env("MAIN_MODEL", "deepseek-v4-pro")),
        model_provider="openai",
        temperature=0.0,
        api_key=require_env("DEEPSEEK_API_KEY"),
        base_url=require_env("DEEPSEEK_BASE_URL"),
        max_tokens=INTENT_MODEL_MAX_TOKENS
    )


def make_subagent_model(model_id: str | None = None) -> BaseChatModel:
    """创建子 Agent 模型（多模型编排）。

    子 Agent 用于阅读、分析、总结、建议等辅助任务，与主 Agent 采用
    不同的模型实例，实现异构多模型协同：

    1. 优先使用指定的自定义模型（``model_id``）。
    2. 显式配置了 ``SUBAGENT_MODEL`` 时，使用独立于主模型的子模型
       （如智谱 GLM 等其它厂商模型），缺 Key/BaseURL 时回退 DeepSeek。
    3. 都没有时使用标记为默认的自定义模型 / 内置模型覆盖。
    4. 全部缺失时回退主模型，保证向后兼容。

    优先级说明：``SUBAGENT_MODEL`` 高于默认自定义模型，因为默认自定义
    模型通常指向主模型（与 ``MAIN_MODEL`` 同源），先命中它会退化成
    「主/子同模型」，失去多模型编排的意义。

    Args:
        model_id: 子 Agent 自定义模型 ID，为 ``None`` 时按上述顺序解析。

    Returns:
        配置好的 ChatModel 实例。

    Raises:
        RuntimeError: 当必要的 API Key 或 Base URL 未配置时。
    """
    if model_id:
        custom = get_custom_model(model_id)
        if custom:
            return _build_model_from_custom(
                custom, streaming=True,
                max_tokens=custom.get("max_tokens", 4096),
                temperature=custom.get("temperature", 0.7),
            )
    sub_model = get_env("SUBAGENT_MODEL")
    if sub_model:
        sub_key = get_env("SUBAGENT_API_KEY")
        sub_base = get_env("SUBAGENT_BASE_URL")
        return init_chat_model(
            model=sub_model,
            model_provider="openai",
            temperature=0.5,
            api_key=sub_key or require_env("DEEPSEEK_API_KEY"),
            base_url=sub_base or require_env("DEEPSEEK_BASE_URL"),
            max_tokens=DEEPSEEK_V4_MAX_TOKENS,
            streaming=True,
        )
    custom = get_default_custom_model()
    if custom is None:
        custom = get_builtin_override()
    if custom:
        return _build_model_from_custom(
            custom, streaming=True,
            max_tokens=custom.get("max_tokens", 4096),
            temperature=custom.get("temperature", 0.7),
        )
    return make_main_model(model_id)
