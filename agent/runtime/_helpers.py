"""Runtime 内部辅助函数。

从 runtime.py 拆分出来的共享工具函数，供 task_router、planning_router、sync_router
共同使用。这些函数不涉及具体的任务路由逻辑，只负责 Agent 构造、消息文本提取、
方案确认判断等通用操作。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent.graph.checkpoint_history import extract_messages_from_writes, visible_checkpoint_messages
from agent.graph.graph import get_checkpointer, get_langgraph_store, get_store
from agent.memory.repo import ensure_repo_memory_initialized
from server import get_agent

logger = logging.getLogger(__name__)


def resolve_project_local_folder(thread_id: str) -> str | None:
    """按 thread 反查其项目绑定的本地工作文件夹。

    thread 记录里保存了 ``project_name``，项目再绑定到本地文件夹
    （``project_local_folders`` 表）。未绑定或查询失败时返回 ``None``，
    调用方据此回退到全局工作区。

    Args:
        thread_id: 会话唯一标识。

    Returns:
        本地文件夹绝对路径字符串；未绑定时返回 ``None``。
    """
    try:
        store = get_store()
        thread = store.get_thread(thread_id)
        project_name = thread.get("project_name") if thread else None
        if not project_name:
            return None
        record = store.get_project_folder(project_name)
        folder = record.get("local_folder") if record else None
        return folder if isinstance(folder, str) and folder.strip() else None
    except Exception:
        logger.warning("解析项目本地文件夹失败：thread_id=%s", thread_id, exc_info=True)
        return None


def resolve_thread_project_name(thread_id: str) -> str | None:
    """按 thread 反查其所属项目名（项目集合名）。

    与 :func:`resolve_project_local_folder` 的区别：这里**不要求**项目已绑定
    本地文件夹。项目集合层（``.project-set/<项目名>``）落在全局技能目录下，
    不依赖磁盘绑定，因此只要 thread 记录了项目名就应该能解析出来。

    Args:
        thread_id: 会话唯一标识。

    Returns:
        项目名字符串；thread 未关联项目或查询失败时返回 ``None``。
    """
    try:
        thread = get_store().get_thread(thread_id)
    except Exception:
        logger.warning("解析会话所属项目失败：thread_id=%s", thread_id, exc_info=True)
        return None
    raw_name = thread.get("project_name") if thread else None
    if isinstance(raw_name, str) and raw_name.strip():
        return raw_name.strip()
    return None


def _get_visible_messages(thread_id: str) -> list[dict[str, Any]]:
    """按 thread_id 获取 checkpoint 中的可见消息列表。

    当前项目的 ``visible_checkpoint_messages`` 接收 checkpoint 字典而非 thread_id，
    这里封装获取 checkpoint 并规整消息字段的逻辑，供方案确认或历史还原使用。
    返回的消息包含 ``author``、``content``、``message_id`` 字段。

    Args:
        thread_id: LangGraph 线程 ID。

    Returns:
        规整后的消息列表；读取失败时返回空列表。
    """

    try:
        checkpoint = get_checkpointer().get({"configurable": {"thread_id": thread_id}})
    except Exception:
        logger.exception("读取 checkpoint 失败：thread_id=%s", thread_id)
        return []
    messages = visible_checkpoint_messages(checkpoint)
    if not messages:
        try:
            checkpointer = get_checkpointer()
            redis_client = getattr(checkpointer, "_redis", None) or getattr(checkpointer, "client", None)
            if redis_client is not None:
                messages = extract_messages_from_writes(redis_client, thread_id)
        except Exception:
            logger.debug("从 Redis writes 恢复消息失败：thread_id=%s", thread_id, exc_info=True)
    result: list[dict[str, Any]] = []
    for msg in messages:
        result.append({
            "message_id": msg.get("id", ""),
            "author": "user" if msg.get("role") == "user" else "agent",
            "content": msg.get("content", ""),
        })
    return result


def build_agent_for_runtime(
    *,
    thread_id: str,
    task_kind: str,
    repo_url: str | None = None,
    model_id: str | None = None,
    backend_type: str | None = None,
) -> Any:
    """为 runtime 构造 DeepAgent 实例。

    每次运行都会重新取得一个 Agent runnable。真正的对话状态不靠这个
    Python 对象长期驻留，而是靠 checkpoint 和 StoreBackend 持久化。

    Args:
        thread_id: 用于绑定 LangGraph checkpoint、Store 业务记录和前端事件流。
        task_kind: 任务类型，影响系统提示词、工具权限、中间件行为和运行保护阈值。
        repo_url: 仓库地址，向前传给 Agent 工厂用于初始化仓库映射和记忆。
        model_id: 自定义模型 ID，为 ``None`` 时使用默认模型。
        backend_type: 运行环境类型，``"local"`` 或 ``"sandbox"``，透传到 backend 创建环节。

    Returns:
        构造完成的 DeepAgent runnable 实例。
    """

    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "task_kind": task_kind,
        "__is_for_execution__": True,
    }
    if repo_url:
        configurable["repo_url"] = repo_url
    if model_id:
        configurable["model_id"] = model_id
    if backend_type:
        configurable["backend_type"] = backend_type
    return get_agent({"configurable": configurable})


def ensure_repo_memory_for_repo(repo: Any, project_dir: str) -> None:
    """根据固定项目目录初始化仓库级长期记忆。

    记忆正文通过 DeepAgents StoreBackend 写入 LangGraph Store。
    已有记忆不会被覆盖。

    Args:
        repo: 仓库标识对象，包含 ``owner`` 和 ``repo`` 属性。
        project_dir: 项目本地目录路径。
    """

    created = ensure_repo_memory_initialized(
        store=get_langgraph_store(),
        repo=repo,
        project_dir=project_dir.replace("\\", "/"),
    )
    if created:
        logger.info("已初始化仓库记忆：repo=%s/%s", repo.owner, repo.repo)


def build_agent_user_content(
    *,
    repo_url: str,
    task_kind: str,
    prompt: str,
    display_prompt: str | None = None,
    approved_plan: str | None = None,
) -> str:
    """构造发送给 DeepAgent 的用户内容，避免只读任务被误导去创建 PR。

    runtime 已经通过 task_kind 做了第一层路由，但模型仍然会看到完整自然语言任务。
    为了降低误操作风险，这里再次把"本轮任务类型"和"允许/禁止的行为"写进用户内容：

    - coding：允许修改代码、运行测试、提交并创建或复用 Pull Request / Merge Request。
    - 非 coding：只能读取仓库、分析、回答或生成方案，禁止写文件和提交。

    如果是用户确认后的 coding 任务，``approved_plan`` 会作为实施依据一并传入。

    Args:
        repo_url: 仓库完整地址。
        task_kind: 任务类型，如 ``"coding"`` 或 ``"planning"``。
        prompt: 用户任务描述文本。
        display_prompt: 用户在网页输入框里真正输入的文本；为 ``None`` 时使用 ``prompt``。
        approved_plan: 用户已确认的技术方案文本；仅在 coding 任务时生效。

    Returns:
        拼接完成的用户内容字符串。
    """

    visible_prompt = (display_prompt or prompt).strip()
    if task_kind == "coding":
        plan_instruction = ""
        if approved_plan:
            plan_instruction = (
                f"\n\n用户已经确认以下技术方案，请按该方案实施；"
                f"如执行中发现必要调整，请在最终总结中说明：\n{approved_plan}"
            )
        task_instruction = (
            "这是开发实现任务。请按系统开发流程完成任务，"
            "必要时修改代码、验证，并创建或复用 Pull Request / Merge Request。"
            f"{plan_instruction}"
        )
    else:
        task_instruction = (
            "这是只读任务。请使用 write_todos 生成适合该任务的清单；"
            "可以准备并读取仓库，但禁止修改文件、提交、push 或创建 Pull Request；"
            "完成后直接用中文回答用户问题。"
        )
    return (
        f"用户可见输入：\n{visible_prompt}\n\n"
        "内部执行上下文：以下内容用于 Agent 判断和执行，不要原样展示为用户输入。\n\n"
        f"仓库地址：{repo_url}\n\n"
        f"任务类型：{task_kind}\n\n"
        f"用户任务：\n{prompt}\n\n"
        f"{task_instruction}"
    )


def build_plan_user_content(
    *,
    repo_url: str,
    prompt: str,
    previous_plan: str | None = None,
    revision_prompt: str | None = None,
) -> str:
    """构造专门用于生成技术方案的只读任务内容。

    技术方案现在作为普通 Agent 回答直接展示在网页中，不再保存到 thread_plans
    或 Markdown 文件。用户确认后，后端会读取上一条方案消息作为实施依据。

    ``previous_plan`` + ``revision_prompt`` 用于"修改上一版方案"的场景。

    Args:
        repo_url: 仓库完整地址。
        prompt: 用户原始需求文本。
        previous_plan: 上一版技术方案内容；为 ``None`` 时表示首次生成。
        revision_prompt: 用户对上一版方案的修改要求；为 ``None`` 时表示全新方案。

    Returns:
        拼接完成的方案生成提示文本。
    """

    if previous_plan and revision_prompt:
        return (
            f"仓库地址：{repo_url}\n\n"
            f"原始用户需求：\n{prompt}\n\n"
            f"上一版技术方案：\n{previous_plan}\n\n"
            f"用户新的修改要求：\n{revision_prompt}\n\n"
            "请基于上一版方案和新的修改要求，重新输出一份完整的新技术方案。\n"
            "不要只输出差异说明，不要只回答新增部分；必须把修订后的完整方案重新组织出来。\n"
            "请只生成技术方案，不要修改文件、不要提交、不要 push、不要创建 Pull Request。\n"
            "方案必须使用中文 Markdown，建议包含：\n"
            "1. 需求理解\n"
            "2. 涉及模块和需要阅读的文件\n"
            "3. 数据结构、接口或页面变化\n"
            "4. 具体实施步骤\n"
            "5. 验证方案\n"
            "6. 风险点和需要用户确认的事项\n"
            "最后必须单独输出一句：是否确认实施该方案？"
        )

    return (
        f"仓库地址：{repo_url}\n\n"
        f"用户需求：\n{prompt}\n\n"
        "请只生成技术方案，不要修改文件、不要提交、不要 push、不要创建 Pull Request。\n"
        "方案必须使用中文 Markdown，建议包含：\n"
        "1. 需求理解\n"
        "2. 涉及模块和需要阅读的文件\n"
        "3. 数据结构、接口或页面变化\n"
        "4. 具体实施步骤\n"
        "5. 验证方案\n"
        "6. 风险点和需要用户确认的事项\n"
        "最后必须单独输出一句：是否确认实施该方案？"
    )


def is_approval_prompt(prompt: str) -> bool:
    """判断用户是否在等待方案确认阶段明确要求开始实施。

    这是"先方案、再实施"流程的关键入口。用户可能输入"确认""同意""开始实施"
    等非常短的指令，runtime 不能把这些短文本当成新的开发需求，而应该回到
    checkpoint 中寻找上一轮技术方案。

    这里故意使用本地规则判断，而不是交给模型判断，因为它会影响是否允许进入
    coding 任务，属于权限相关的流程控制。

    Args:
        prompt: 用户输入文本。

    Returns:
        ``True`` 表示用户明确要求开始实施；``False`` 表示不是确认指令。
    """

    normalized = " ".join((prompt or "").lower().split())
    approval_phrases = [
        "确认",
        "确认实施",
        "同意",
        "同意方案",
        "按方案实施",
        "开始实施",
        "可以实施",
        "按照方案来",
        "就按这个方案",
        "实施",
    ]
    rejection_phrases = ["不确认", "先不要", "不要实施", "修改方案", "重新设计", "调整方案"]
    return any(phrase in normalized for phrase in approval_phrases) and not any(
        phrase in normalized for phrase in rejection_phrases
    )


def is_plan_revision_prompt(prompt: str) -> bool:
    """判断用户是否明确要求修改上一版技术方案。

    只有用户明确表达"修改/补充/重新生成方案"时，才会触发方案修订。
    这能避免历史中的待确认方案劫持后续普通问答。

    Args:
        prompt: 用户输入文本。

    Returns:
        ``True`` 表示用户要求修订方案；``False`` 表示普通输入。
    """

    normalized = " ".join((prompt or "").lower().split())
    revision_markers = [
        "修改方案",
        "调整方案",
        "重新设计方案",
        "重新生成方案",
        "重新输出方案",
        "再生成新的方案",
        "生成新的方案",
        "修订方案",
        "补充方案",
        "完善方案",
        "改一下方案",
        "改一下这个方案",
        "按这个要求修改方案",
        "基于上一版方案",
        "上一版方案",
        "原来的方案",
        "这个方案里面",
        "方案中增加",
        "方案里增加",
    ]
    return any(marker in normalized for marker in revision_markers)


def message_metadata(message: dict[str, Any]) -> dict[str, Any]:
    """解析 checkpoint 消息包装出来的 metadata。

    metadata 可能是 dict，也可能是旧数据中的 JSON 字符串。
    最重要的 metadata 是 source_prompt，用于从"确认实施"恢复出上一轮
    真正的用户需求。

    Args:
        message: checkpoint 中的单条消息字典。

    Returns:
        解析后的 metadata 字典；无法解析时返回空字典。
    """

    metadata = message.get("metadata")
    if isinstance(metadata, dict):
        return metadata
    if isinstance(metadata, str) and metadata.strip():
        try:
            parsed = json.loads(metadata)
        except ValueError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def is_confirmable_plan_text(text: str) -> bool:
    """判断 checkpoint 中的 assistant 正文是否像一份可确认实施的方案。

    用中文方案关键词做保守判断，避免普通问答被误识别成可实施方案。
    判断规则宁可保守，也不要激进。

    Args:
        text: 待判断的 assistant 消息正文。

    Returns:
        ``True`` 表示文本像一份可确认的技术方案；``False`` 表示不是。
    """

    stripped = text.strip()
    if not stripped:
        return False
    rejection_markers = [
        "当前任务是**只读模式**",
        "当前任务是只读模式",
        "我不会执行代码修改",
        "无法执行代码修改",
        "请切换为编码任务",
    ]
    if any(marker in stripped for marker in rejection_markers):
        return False
    plan_markers = [
        "是否确认实施该方案",
        "技术方案",
        "修复技术方案",
        "实施步骤",
        "修复依据",
        "验证方案",
    ]
    return any(marker in stripped for marker in plan_markers)


def latest_confirmable_plan_from_checkpoint(thread_id: str) -> dict[str, Any] | None:
    """从 checkpoint 历史中读取最近一条可确认实施的方案。

    查找方向是从后往前，保证多轮修改方案后总是采用最新的一版。
    返回的是 runtime 自己组装的简化消息结构，不直接暴露 checkpoint 内部结构。

    Args:
        thread_id: LangGraph 线程 ID。

    Returns:
        包含 message_id、thread_id、author、content、metadata 的方案字典；
        未找到可确认方案时返回 ``None``。
    """

    candidates = []
    for message in reversed(_get_visible_messages(thread_id)):
        if message.get("author") != "agent":
            continue
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if is_confirmable_plan_text(content):
            return {
                "message_id": message.get("message_id") or f"checkpoint-plan:{thread_id}",
                "thread_id": thread_id,
                "run_id": None,
                "author": "agent",
                "content": content,
                "metadata": {
                    "task_kind": "planning",
                    "awaiting_confirmation": True,
                    "source": "checkpoint",
                },
            }
        candidates.append(message)

    if candidates:
        fallback = candidates[0]
        fallback_content = str(fallback.get("content") or "").strip()
        if len(fallback_content) >= 200:
            logger.info(
                "方案关键词匹配失败，使用最后一条长消息作为待确认方案：thread_id=%s len=%d",
                thread_id,
                len(fallback_content),
            )
            return {
                "message_id": fallback.get("message_id") or f"checkpoint-plan:{thread_id}",
                "thread_id": thread_id,
                "run_id": None,
                "author": "agent",
                "content": fallback_content,
                "metadata": {
                    "task_kind": "planning",
                    "awaiting_confirmation": True,
                    "source": "checkpoint",
                },
            }
    return None


def latest_confirmable_plan_message(thread_id: str) -> dict[str, Any] | None:
    """读取当前线程最近一条等待确认的技术方案消息。

    页面历史和确认实施都以 checkpoint 为准。这个封装函数保留了"读取来源"的抽象，
    如果以后要增加归档查询或迁移兼容，可以在这里扩展。

    Args:
        thread_id: LangGraph 线程 ID。

    Returns:
        方案消息字典；未找到时返回 ``None``。
    """

    return latest_confirmable_plan_from_checkpoint(thread_id)


def plan_source_prompt(message: dict[str, Any], fallback: str) -> str:
    """读取方案消息里保存的原始需求。

    首次生成方案时，metadata.source_prompt 保存用户原始需求；方案被多次修订时，
    这里继续沿用上一版 source_prompt，避免只把"再改一下"当成完整开发目标。

    Args:
        message: 方案消息字典。
        fallback: metadata 中没有 source_prompt 时使用的回退文本。

    Returns:
        原始需求文本；不存在时返回 ``fallback``。
    """

    metadata = message_metadata(message)
    source_prompt = str(metadata.get("source_prompt") or "").strip()
    return source_prompt or fallback


def latest_non_approval_user_prompt(thread_id: str, fallback: str) -> str:
    """从 checkpoint 历史读取最近一条不是"确认/开始实施"的用户消息。

    方案确认时，如果 checkpoint 里的方案消息没有携带 source_prompt，
    就从 checkpoint 历史向前找真实用户需求。

    Args:
        thread_id: LangGraph 线程 ID。
        fallback: 未找到非确认用户消息时使用的回退文本。

    Returns:
        最近一条非确认的用户消息内容；未找到时返回 ``fallback``。
    """

    for message in reversed(_get_visible_messages(thread_id)):
        if message.get("author") != "user":
            continue
        content = str(message.get("content") or "").strip()
        if content and not is_approval_prompt(content):
            return content
    return fallback


def revision_source_prompt(*, previous_source_prompt: str, revision_prompt: str) -> str:
    """把原始需求和本次修订要求合并成新版方案的 source_prompt。

    多次修改方案时，会把补充要求追加到原始需求后面，
    避免后续实施阶段丢失用户逐轮补充的信息。

    Args:
        previous_source_prompt: 上一版方案对应的原始需求文本。
        revision_prompt: 本次用户补充或修改的要求。

    Returns:
        合并后的 source_prompt 文本。
    """

    revision_prompt = revision_prompt.strip()
    if not revision_prompt:
        return previous_source_prompt
    if "补充/修改要求：" in previous_source_prompt:
        return f"{previous_source_prompt.rstrip()}\n- {revision_prompt}"
    return f"{previous_source_prompt.rstrip()}\n\n补充/修改要求：\n- {revision_prompt}"
