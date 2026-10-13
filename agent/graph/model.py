"""模型工厂模块。

负责创建 Agent 使用的大语言模型实例。当前项目统一使用 OpenAI 兼容接口。
模型分为两类：

- 主模型（main model）：用于 Agent 推理和工具调用，支持流式输出。
- 意图分类模型（intent model）：用于快速判断用户任务类型，不需要流式。

优先使用自定义模型（通过 API 配置），其次使用内置模型用户覆盖，回退到 .env 环境变量配置。
"""

from __future__ import annotations

import logging
from typing import Any

from langchain.chat_models import init_chat_model
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult

from config.constants import DEEPSEEK_V4_MAX_TOKENS, INTENT_MODEL_MAX_TOKENS
from config.env_config import get_env, require_env
from config.model_config import get_builtin_override, get_custom_model, get_default_custom_model

logger = logging.getLogger(__name__)


class FallbackChatModel(BaseChatModel):
    """双模型 fallback 包装：主模型调用异常时自动切换到备用模型。

    用于子 Agent 等低优先级调用：主模型（如阿里 qwen3.8-flash）额度耗尽、
    鉴权失败或网络异常时，透明降级到备用模型（默认 DeepSeek），
    避免子任务因单一上游不可用而整体失败。备用模型再失败则原样抛出。
    流式/异步调用同样走 fallback 路径。
    """

    primary: Any
    fallback: Any

    @property
    def _llm_type(self) -> str:
        return "fallback_chat_model"

    def bind_tools(self, tools, **kwargs):
        """绑定工具（子 Agent 创建时必需）。

        langchain 创建 Agent 时会对模型调用 ``bind_tools``；基类默认抛
        ``NotImplementedError``（str 为空），导致子 Agent 创建失败。
        这里对 primary/fallback 分别绑定后仍返回包装，保留 fallback 语义：
        绑定后的调用失败同样自动切备用模型。
        """
        return FallbackChatModel(
            primary=self.primary.bind_tools(tools, **kwargs),
            fallback=self.fallback.bind_tools(tools, **kwargs),
        )

    def bind(self, **kwargs):
        """bind 转发（langchain Agent 工厂同样会调用 ``model.bind``）。"""
        return FallbackChatModel(
            primary=self.primary.bind(**kwargs),
            fallback=self.fallback.bind(**kwargs),
        )

    def with_structured_output(self, schema, **kwargs):
        """结构化输出转发到主模型（校验/意图等场景）。"""
        return self.primary.with_structured_output(schema, **kwargs)

    @staticmethod
    def _clean_kwargs(kwargs: dict) -> dict:
        """剔除只对 BaseChatModel 内部有效的参数（invoke/stream 不接受）。"""
        return {k: v for k, v in kwargs.items() if k not in ("run_manager", "run_id", "generation_id")}

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        kw = self._clean_kwargs(kwargs)
        try:
            message = self.primary.invoke(messages, stop=stop, **kw)
        except Exception as exc:
            logger.warning("子模型调用失败，降级到备用模型：%s", exc)
            message = self.fallback.invoke(messages, stop=stop, **kw)
        return ChatResult(generations=[ChatGeneration(message=message)])

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        kw = self._clean_kwargs(kwargs)
        try:
            message = await self.primary.ainvoke(messages, stop=stop, **kw)
        except Exception as exc:
            logger.warning("子模型异步调用失败，降级到备用模型：%s", exc)
            message = await self.fallback.ainvoke(messages, stop=stop, **kw)
        return ChatResult(generations=[ChatGeneration(message=message)])

    def _stream(self, messages, stop=None, run_manager=None, **kwargs):
        kw = self._clean_kwargs(kwargs)
        try:
            for chunk in self.primary.stream(messages, stop=stop, **kw):
                yield ChatGenerationChunk(message=self._as_chunk(chunk))
        except Exception as exc:
            logger.warning("子模型流式调用失败，降级到备用模型：%s", exc)
            for chunk in self.fallback.stream(messages, stop=stop, **kw):
                yield ChatGenerationChunk(message=self._as_chunk(chunk))

    async def _astream(self, messages, stop=None, run_manager=None, **kwargs):
        kw = self._clean_kwargs(kwargs)
        try:
            async for chunk in self.primary.astream(messages, stop=stop, **kw):
                yield ChatGenerationChunk(message=self._as_chunk(chunk))
        except Exception as exc:
            logger.warning("子模型流式调用失败，降级到备用模型（异步）：%s", exc)
            async for chunk in self.fallback.astream(messages, stop=stop, **kw):
                yield ChatGenerationChunk(message=self._as_chunk(chunk))

    @staticmethod
    def _as_chunk(message):
        """把流式产出的消息统一转成 BaseMessageChunk（AIMessage 需 to_chunk）。"""
        return message.to_chunk() if hasattr(message, "to_chunk") else message


def make_subagent_fallback_model() -> BaseChatModel:
    """创建子 Agent 的备用模型（子模型不可用时由主模型接手执行）。

    优先级：``SUBAGENT_FALLBACK_MODEL`` / ``SUBAGENT_FALLBACK_BASE_URL`` /
    ``SUBAGENT_FALLBACK_API_KEY`` 显式配置时用指定模型；
    未配置时默认回退**主模型**（``make_main_model``），即子任务由主 Agent
    同一模型能力接手执行，避免委派子任务因子模型断供而整体失败。
    """
    model_name = get_env("SUBAGENT_FALLBACK_MODEL")
    if model_name:
        api_key = get_env("SUBAGENT_FALLBACK_API_KEY") or require_env("DEEPSEEK_API_KEY")
        base_url = get_env("SUBAGENT_FALLBACK_BASE_URL") or get_env(
            "DEEPSEEK_BASE_URL", "https://api.deepseek.com"
        )
        return init_chat_model(
            model=model_name,
            model_provider="openai",
            temperature=0.5,
            api_key=api_key,
            base_url=base_url,
            max_tokens=DEEPSEEK_V4_MAX_TOKENS,
            streaming=True,
            timeout=120,
        )
    return make_main_model()


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
        # 模型 API 挂起保护：OpenAI SDK 默认 read timeout 为 600 秒，
        # 上游无响应时线程会静默挂起 10 分钟。无人值守/长任务场景收紧到 120 秒，
        # 超时按失败返回（进重试队列或触发软修复），避免"看起来卡死"。
        timeout=120,
    )


def _make_main_fallback() -> BaseChatModel | None:
    """主模型失败时的备用模型（防止上游挂起直接中断对话）。

    优先 ``MAIN_FALLBACK_MODEL`` 显式配置；未配置时用 deepseek-flash
    （与 FINAL_VALIDATOR_MODEL 同款，走 DEEPSEEK key），保证偶发上游
    无响应时本轮能换模型续跑而不是直接失败。
    """
    model_name = get_env("MAIN_FALLBACK_MODEL", get_env("FINAL_VALIDATOR_MODEL", "deepseek-flash"))
    try:
        return init_chat_model(
            model=model_name,
            model_provider="openai",
            temperature=0.5,
            api_key=get_env("MAIN_FALLBACK_API_KEY") or require_env("DEEPSEEK_API_KEY"),
            base_url=get_env("MAIN_FALLBACK_BASE_URL") or require_env("DEEPSEEK_BASE_URL"),
            max_tokens=4096,
            streaming=True,
            timeout=120,
        )
    except Exception:
        return None


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
        primary = _build_model_from_custom(
            custom, streaming=True,
            max_tokens=custom.get("max_tokens", 4096),
            temperature=custom.get("temperature", 0.7),
        )
    else:
        primary = init_chat_model(
            model=get_env("MAIN_MODEL", "deepseek-v4-pro"),
            model_provider="openai",
            temperature=1.1,
            api_key=get_env("MAIN_API_KEY") or require_env("DEEPSEEK_API_KEY"),
            base_url=get_env("MAIN_BASE_URL") or require_env("DEEPSEEK_BASE_URL"),
            max_tokens=DEEPSEEK_V4_MAX_TOKENS,
            streaming=True,
        )
    # 降级：主模型上游挂起/超时（异常）时自动换备用模型重发，而不是中断对话。
    # 主节点、修复轮（server 传入 main_model）与子任务回退共用此模型，一处生效。
    fallback = _make_main_fallback()
    if fallback is not None:
        try:
            return primary.with_fallbacks([fallback])
        except Exception:
            return primary
    return primary


def make_vision_model(model_id: str | None = None) -> BaseChatModel:
    """创建视觉模型（inspect_image 读图转写用）。

    主模型可能不具备视觉能力（如 MiMo 通道），此时图片题无法由主模型直接
    查看，必须用独立视觉模型（如智谱 glm-4v-plus）读图并转写为文本。

    未配置 ``VISUAL_MODEL`` 时回退主模型，保持原有行为。

    Args:
        model_id: 自定义模型 ID，为 ``None`` 时使用 .env 的 VISUAL_* 配置。

    Returns:
        配置好的 ChatModel 实例。

    Raises:
        RuntimeError: 当必要的 API Key 或 Base URL 未配置时。
    """
    model_name = get_env("VISUAL_MODEL")
    if not model_name:
        return make_main_model(model_id)
    return init_chat_model(
        model=model_name,
        model_provider="openai",
        temperature=0.0,
        api_key=get_env("VISUAL_API_KEY") or require_env("MAIN_API_KEY"),
        base_url=get_env("VISUAL_BASE_URL") or get_env("MAIN_BASE_URL") or require_env("DEEPSEEK_BASE_URL"),
        max_tokens=2048,
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
        primary = init_chat_model(
            model=sub_model,
            model_provider="openai",
            temperature=0.5,
            api_key=sub_key or require_env("DEEPSEEK_API_KEY"),
            base_url=sub_base or require_env("DEEPSEEK_BASE_URL"),
            max_tokens=DEEPSEEK_V4_MAX_TOKENS,
            streaming=True,
            timeout=120,
        )
        # 子模型 fallback：主配置（如阿里）额度耗尽/鉴权失败/网络异常时
        # 自动降级到备用模型（默认主模型 MiMo，即主 Agent 自己执行），
        # 保证委派子任务不因单上游断供。
        return FallbackChatModel(primary=primary, fallback=make_subagent_fallback_model())
    custom = get_default_custom_model()
    if custom is None:
        custom = get_builtin_override()
    if custom:
        return _build_model_from_custom(
            custom, streaming=True,
            max_tokens=custom.get("max_tokens", 4096),
            temperature=custom.get("temperature", 0.7),
        )
    # make_main_model 返回 with_fallbacks 包装（RunnableWithFallbacks），
    # deepagents resolve_model 只认 BaseChatModel，这里退回裸 primary。
    mm = make_main_model(model_id)
    return getattr(mm, "runnable", mm)


def make_validator_model(model_id: str | None = None) -> BaseChatModel:
    """创建最终答案证据校验模型。

    校验模型独立于子 Agent 配置，便于使用另一家模型降低相关偏差。
    对 DeepSeek 校验模型可通过 ``FINAL_VALIDATOR_THINKING=enabled`` 打开推理模式。
    """
    if model_id:
        custom = get_custom_model(model_id)
        if custom:
            return _build_model_from_custom(
                custom, streaming=False,
                max_tokens=custom.get("max_tokens", INTENT_MODEL_MAX_TOKENS),
                temperature=custom.get("temperature", 0.0),
            )
    model_name = get_env("FINAL_VALIDATOR_MODEL")
    if not model_name:
        return make_subagent_model(model_id)
    thinking = get_env("FINAL_VALIDATOR_THINKING", "disabled").lower() in {"enabled", "true", "on", "1"}
    kwargs: dict[str, Any] = {}
    if thinking:
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
    return init_chat_model(
        model=model_name,
        model_provider="openai",
        temperature=0.0,
        api_key=get_env("FINAL_VALIDATOR_API_KEY") or require_env("DEEPSEEK_API_KEY"),
        base_url=get_env("FINAL_VALIDATOR_BASE_URL") or require_env("DEEPSEEK_BASE_URL"),
        max_tokens=INTENT_MODEL_MAX_TOKENS,
        streaming=False,
        **kwargs,
    )
