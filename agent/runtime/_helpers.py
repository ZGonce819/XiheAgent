"""Runtime 内部辅助函数。

从 runtime.py 拆分出来的共享工具函数，供 task_router、planning_router、sync_router
共同使用。这些函数不涉及具体的任务路由逻辑，只负责 Agent 构造、消息文本提取、
方案确认判断等通用操作。
"""

from __future__ import annotations

import json
import logging
from typing import Any

from agent.graph.checkpoint_history import visible_checkpoint_messages
from agent.graph.graph import get_checkpointer, get_langgraph_store
from agent.memory.repo import ensure_repo_memory_initialized, repo_project_dir
from agent.server import get_agent
from tools.gitee_api import parse_gitee_repo_url

logger = logging.getLogger(__name__)


def _get_visible_messages(thread_id: str) -> list[dict[str, Any]]:
    """按 thread_id 获取 checkpoint 中的可见消息列表。

    当前项目的 ``visible_checkpoint_messages`` 接收 checkpoint 字典而非 thread_id，
    这里封装获取 checkpoint 并规整消息字段的逻辑，供方案确认和历史还原使用。
    返回的消息包含 ``author``、``content``、``message_id`` 字段。
    """

    try:
        checkpoint = get_checkpointer().get({"configurable": {"thread_id": thread_id}})
    except Exception:
        logger.exception("读取 checkpoint 失败：thread_id=%s", thread_id)
        return []
    messages = visible_checkpoint_messages(checkpoint)
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
) -> Any:
    """为 runtime 构造 DeepAgent 实例。

    每次运行都会重新取得一个 Agent runnable。真正的对话状态不靠这个
    Python 对象长期驻留，而是靠 checkpoint 和 StoreBackend 持久化。

    ``thread_id`` 用于绑定 LangGraph checkpoint、Store 业务记录和前端事件流。
    ``task_kind`` 会影响系统提示词、工具权限、中间件行为和运行保护阈值。
    ``repo_url`` 会继续向下传给 Agent 工厂，用于初始化仓库映射、仓库记忆和运行上下文。
    """

    configurable: dict[str, Any] = {
        "thread_id": thread_id,
        "task_kind": task_kind,
        "__is_for_execution__": True,
    }
    if repo_url:
        configurable["repo_url"] = repo_url
    return get_agent({"configurable": configurable})


def ensure_repo_memory_for_repo(repo: Any, project_dir: str) -> None:
    """根据固定项目目录初始化仓库级长期记忆文件。

    记忆正文通过 DeepAgents StoreBackend 写入 LangGraph Store，不再额外建立
    SQLite 索引表。已有记忆不会被覆盖。
    """

    created = ensure_repo_memory_initialized(
        store=get_langgraph_store(),
        repo=repo,
        project_dir=project_dir.replace("\\", "/"),
    )
    if created:
        logger.info("已初始化仓库记忆：repo=%s/%s", repo.owner, repo.repo)


def message_content_to_text(content: Any) -> str:
    """把 LangChain message.content 规整成可展示文本。

    LangChain/DeepAgents 的 message.content 可能是普通字符串，也可能是多模态
    content block 列表。runtime 在提取最终回答、技术方案和记忆摘要时，只需要
    文本部分，所以这里统一转换，避免每个调用点重复兼容不同消息结构。
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
    """提取 DeepAgent 最后一条 assistant/ai 消息作为用户可见回答。

    任务结束后，仓库记忆更新只需要最终总结，不应该把中间工具消息、todo 状态或
    调试事件写进长期记忆。因此这里从后往前找最后一条有正文的 AI/assistant 消息。
    """

    for message in reversed(messages):
        message_type = str(message.get("type") or "").lower()
        if message_type not in {"ai", "assistant"}:
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

    这个函数只用于兜底校验和仓库记忆更新。前端实时展示不依赖它，前端看到的正文
    来自 streaming_runtime.py 对 DeepAgents V3 文本 chunk 的实时消费。
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

    - coding：允许修改代码、运行测试、提交并创建或复用 Gitee Pull Request。
    - 非 coding：只能读取仓库、分析、回答或生成方案，禁止写文件和提交。

    如果是用户确认后的 coding 任务，``approved_plan`` 会作为实施依据一并传入。
    ``display_prompt`` 是用户在网页输入框里真正输入的文本。
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
            "必要时修改代码、验证，并创建或复用 Gitee Pull Request。"
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
        f"Gitee 仓库地址：{repo_url}\n\n"
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

    ``previous_plan + revision_prompt`` 用于"修改上一版方案"的场景。
    """

    if previous_plan and revision_prompt:
        return (
            f"Gitee 仓库地址：{repo_url}\n\n"
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
        f"Gitee 仓库地址：{repo_url}\n\n"
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
    """

    for message in reversed(_get_visible_messages(thread_id)):
        if message.get("author") != "agent":
            continue
        content = str(message.get("content") or "").strip()
        if not is_confirmable_plan_text(content):
            continue
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
    return None


def latest_confirmable_plan_message(thread_id: str) -> dict[str, Any] | None:
    """读取当前线程最近一条等待确认的技术方案消息。

    页面历史和确认实施都以 checkpoint 为准。这个封装函数保留了"读取来源"的抽象，
    如果以后要增加归档查询或迁移兼容，可以在这里扩展。
    """

    return latest_confirmable_plan_from_checkpoint(thread_id)


def plan_source_prompt(message: dict[str, Any], fallback: str) -> str:
    """读取方案消息里保存的原始需求。

    首次生成方案时，metadata.source_prompt 保存用户原始需求；方案被多次修订时，
    这里继续沿用上一版 source_prompt，避免只把"再改一下"当成完整开发目标。
    """

    metadata = message_metadata(message)
    source_prompt = str(metadata.get("source_prompt") or "").strip()
    return source_prompt or fallback


def latest_non_approval_user_prompt(thread_id: str, fallback: str) -> str:
    """从 checkpoint 历史读取最近一条不是"确认/开始实施"的用户消息。

    方案确认时，如果 checkpoint 里的方案消息没有携带 source_prompt，
    就从 checkpoint 历史向前找真实用户需求。
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
    """

    revision_prompt = revision_prompt.strip()
    if not revision_prompt:
        return previous_source_prompt
    if "补充/修改要求：" in previous_source_prompt:
        return f"{previous_source_prompt.rstrip()}\n- {revision_prompt}"
    return f"{previous_source_prompt.rstrip()}\n\n补充/修改要求：\n- {revision_prompt}"
