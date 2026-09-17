"""DeepAgent 工厂模块。

负责组装模型、工具、子 Agent、Backend、记忆、中间件和 Checkpoint，
按 thread 维度创建完整的 DeepAgent 实例。
"""

from __future__ import annotations

import logging
from typing import Any

from deepagents import create_deep_agent
from deepagents.backends import CompositeBackend, StoreBackend
from deepagents.middleware.subagents import GENERAL_PURPOSE_SUBAGENT, SubAgent
from deepagents.middleware.summarization import create_summarization_tool_middleware
from langchain.agents.middleware import ModelCallLimitMiddleware, TodoListMiddleware
from langchain_core.language_models import BaseChatModel
from langgraph.graph.state import RunnableConfig
from langgraph.store.base import BaseStore

from agent.graph.graph import get_checkpointer, get_langgraph_store
from agent.graph.model import make_main_model
from agent.memory.repo import (
    build_repo_memory_namespace,
    ensure_repo_memory_initialized,
    repo_memory_store_key,
    repo_memory_virtual_path,
    repo_project_dir,
)
from agent.middleware import (
    ContextInjectionMiddleware,
    MessageSanitizeMiddleware,
    SanitizeToolInputsMiddleware,
    ToolErrorMiddleware,
    ToolTrackingMiddleware,
)
from agent.runtime.task_intent import TaskKind
from backends import create_backend
from deepagents.backends.sandbox import BaseSandbox
from prompt import get_system_prompt
from tools import (
    add_review_finding,
    fetch_url,
    get_codeup_merge_request_context,
    get_gitee_pull_request_context,
    get_github_pull_request_context,
    get_review_diff_summary,
    list_review_findings,
    load_default_review_rules,
    open_codeup_merge_request,
    open_gitee_pull_request,
    open_github_pull_request,
    publish_codeup_mr_comment,
    publish_gitee_pr_comment,
    publish_github_pr_comment,
    validate_review_finding_location,
    web_search,
)
from config.constants import DEFAULT_RECURSION_LIMIT, MAX_CACHED_BACKENDS, MODEL_CALL_RECURSION_LIMIT, VALID_TASK_KINDS
from utils.platform_utils import parse_repo_url

logger = logging.getLogger(__name__)

_BACKENDS: dict[str, BaseSandbox] = {}


def graph_loaded_for_execution(config: RunnableConfig) -> bool:
    """判断当前 Agent 是否用于真实执行。

    部分框架或调试脚本会探测 Agent 图结构，但不代表需要真正执行任务。
    没有 thread_id 时返回空 Agent，避免误创建工具链和触发文件系统副作用。

    Args:
        config: LangGraph 运行配置，包含 ``configurable`` 字典。

    Returns:
        ``True`` 表示当前为真实执行态，否则为探测或调试调用。
    """
    configurable = (config or {}).get("configurable") or {}
    return bool(configurable.get("__is_for_execution__", False))


def ensure_backend_for_thread(thread_id: str, backend_type: str | None = None) -> BaseSandbox:
    """获取或创建绑定到 thread 的 Backend。

    优先使用前端传入的 ``backend_type`` 决定使用沙盒还是本地 Backend，
    未传入时回退到全局 ``USE_SANDBOX`` 配置。
    负责复用当前机器上的工作区，按 thread 维度缓存 Backend 实例，
    同一会话内可重复使用。超过缓存上限时淘汰最久未使用的实例。

    Args:
        thread_id: 会话唯一标识。
        backend_type: 可选的运行环境类型，``"local"`` 或 ``"sandbox"``。

    Returns:
        缓存或新建的 Backend 实例。
    """
    backend = _BACKENDS.get(thread_id)
    if backend is None:
        if len(_BACKENDS) >= MAX_CACHED_BACKENDS:
            oldest_key = next(iter(_BACKENDS))
            logger.info("Backend 缓存已满，淘汰最旧实例：%s", oldest_key)
            _BACKENDS.pop(oldest_key)
        backend = create_backend(thread_id=thread_id, backend_type=backend_type)
        logger.info("为 thread 创建 %s（backend_type=%s）：%s", type(backend).__name__, backend_type, thread_id)
        _BACKENDS[thread_id] = backend
    else:
        logger.info("复用 thread 的 %s：%s", type(backend).__name__, thread_id)
    return backend


def _general_purpose_subagent(model: BaseChatModel) -> SubAgent:
    """构建通用分析子 Agent。

    子 Agent 只负责阅读、分析、总结和给主 Agent 提供建议，
    不能直接修改 /projects 下的源码，保持主 Agent 的最终执行权。

    Args:
        model: 子 Agent 使用的语言模型实例。

    Returns:
        配置完成的通用子 Agent 字典，包含名称、描述、系统提示词、
        模型、技能路径和文件系统权限。
    """
    return {
        "name": GENERAL_PURPOSE_SUBAGENT["name"],
        "description": GENERAL_PURPOSE_SUBAGENT["description"],
        "system_prompt": GENERAL_PURPOSE_SUBAGENT["system_prompt"],
        "model": model,
        "skills": ["/skills/"],
    }


def _code_reviewer_subagent(model: BaseChatModel) -> SubAgent:
    """构建只读代码审查子 Agent。

    职责：读取规则、读取 SCM PR/MR 上下文、分析 diff、记录结构化 finding，
    并输出中文审查报告。不允许修改源码或执行 Git 写操作。

    Args:
        model: 子 Agent 使用的语言模型实例。

    Returns:
        配置完成的代码审查子 Agent 字典，包含名称、描述、系统提示词、
        模型、工具列表、技能路径和文件系统权限。
    """
    return {
        "name": "code_reviewer",
        "description": (
            "用于审查 Pull Request / Merge Request 或本地分支 diff 的子 Agent。"
            "它会读取审查规则、PR 上下文和变更文件，记录结构化 finding，"
            "最后输出中文审查报告。"
        ),
        "system_prompt": (
            "你是羲和的代码审查子 Agent，只负责 review，不负责修改代码。\n"
            "你必须使用中文输出，代码标识符、路径、命令和 API 名称可以保留英文。\n"
            "审查流程：\n"
            "1. 按 code-review skill 先用 read_file 读取工作区规则和仓库规则；"
            "读不到时调用 load_default_review_rules。\n"
            "2. 如果用户提供 PR/MR 编号，根据平台调用对应的上下文获取工具"
            "（get_gitee_pull_request_context / get_github_pull_request_context / "
            "get_codeup_merge_request_context）读取详情、提交、文件和评论。\n"
            "3. 调用 get_review_diff_summary 获取本地 diff 摘要和变更行号。\n"
            "4. 只记录会导致真实风险的问题，不记录纯风格偏好。\n"
            "5. finding 必须包含 file、line、severity、title、description。\n"
            "6. 记录 finding 前，尽量确认文件属于本次 diff；"
            "无法确认行号时使用文件级 finding。\n"
            "7. 使用 add_review_finding 保存结构化发现，"
            "再用 list_review_findings 汇总。\n"
            "8. 最终报告必须包含结论、阻塞问题、高风险问题、一般建议和测试建议。\n"
            "9. 不要修改文件、不要提交、不要 push、不要创建 Pull Request。\n"
        ),
        "model": model,
        "tools": [
            get_codeup_merge_request_context,
            get_gitee_pull_request_context,
            get_github_pull_request_context,
            load_default_review_rules,
            get_review_diff_summary,
            validate_review_finding_location,
            add_review_finding,
            list_review_findings,
        ],
        "skills": ["/skills/"],
    }


def _task_kind_from_config(configurable: dict[str, Any]) -> TaskKind:
    """从 config 中读取任务类型，非法值统一回退为 coding。

    Args:
        configurable: LangGraph ``configurable`` 字典，
            可包含 ``task_kind`` 键。

    Returns:
        解析后的任务类型；当值不在合法枚举中时返回 ``"coding"``。
    """
    value = configurable.get("task_kind", "coding")
    if value in VALID_TASK_KINDS:
        return value  # type: ignore[return-value]
    return "coding"


def create_repo_backend(
    *,
    local_backend: BaseSandbox,
    store: BaseStore,
    owner: str,
    repo: str,
) -> CompositeBackend:
    """创建当前仓库专用的 CompositeBackend。

    - /projects、/skills、/runtimes 和 execute() 继续走主 Backend。
    - /memories/ 走 DeepAgents StoreBackend，底层由 LangGraph Store 持久化。

    Args:
        local_backend: 本地 Shell/Sandbox Backend 实例。
        store: LangGraph Store，用于记忆持久化。
        owner: 仓库 owner（命名空间）。
        repo: 仓库名称。

    Returns:
        路由后的 CompositeBackend 实例。
    """
    namespace = build_repo_memory_namespace(owner, repo)
    return CompositeBackend(
        default=local_backend,
        routes={
            "/memories/": StoreBackend(
                store=store,
                namespace=lambda _rt, _namespace=namespace: _namespace,
            ),
        },
    )


def _prepare_repo_backend_context(
    *,
    repo_url: Any,
    backend: BaseSandbox,
) -> tuple[Any, list[str] | None, str | None]:
    """为指定仓库准备 repo 级 Backend 和长期记忆。

    如果任务绑定了 SCM 仓库，将 /memories/ 路径挂到 DeepAgents StoreBackend，
    同时读取记忆文件内容返回，避免后续 middleware 重复查询数据库。

    Args:
        repo_url: 仓库 URL 字符串；非字符串或空值时直接返回原始 Backend。
        backend: 当前 thread 的本地 Backend 实例。

    Returns:
        (CompositeBackend, memory_virtual_paths, memory_content | None)
    """
    if not isinstance(repo_url, str) or not repo_url.strip():
        return backend, None, None

    repo = parse_repo_url(repo_url)
    langgraph_store = get_langgraph_store()
    project_dir = repo_project_dir(repo)
    ensure_repo_memory_initialized(
        store=langgraph_store,
        repo=repo,
        project_dir=project_dir,
    )

    memory_path = repo_memory_virtual_path(repo.owner, repo.repo)
    memory_content: str | None = None
    memory_item = langgraph_store.get(
        build_repo_memory_namespace(repo.owner, repo.repo),
        repo_memory_store_key(repo.owner, repo.repo),
    )
    if memory_item is not None:
        content = str(memory_item.value.get("content") or "").strip()
        if content:
            memory_content = content

    return (
        create_repo_backend(
            local_backend=backend,
            store=langgraph_store,
            owner=repo.owner,
            repo=repo.repo,
        ),
        [memory_path],
        memory_content,
    )


def get_agent(config: RunnableConfig):
    """按照指定 thread 构建 DeepAgent。

    根据 config 中的 thread_id、task_kind、repo_url 等参数，
    组装模型、工具、子 Agent、Backend、记忆、中间件和 Checkpoint，
    返回一个可直接调用的 Agent runnable。

    Args:
        config: LangGraph 运行配置，``configurable`` 中需包含
            ``thread_id``、可选 ``task_kind`` 和 ``repo_url``。

    Returns:
        配置完成的 Agent runnable 实例。
    """
    config = dict(config or {})
    configurable = dict(config.get("configurable") or {})
    thread_id = configurable.get("thread_id")
    config["configurable"] = configurable
    config["recursion_limit"] = config.get("recursion_limit", DEFAULT_RECURSION_LIMIT)

    if (
        not isinstance(thread_id, str)
        or not thread_id
        or not graph_loaded_for_execution(config)
    ):
        logger.info("没有 thread_id 或不是执行态，返回空 Agent")
        return create_deep_agent(system_prompt="", tools=[]).with_config(config)

    task_kind = _task_kind_from_config(configurable)
    backend_type = configurable.get("backend_type")
    backend = ensure_backend_for_thread(thread_id, backend_type=backend_type)
    repo_url = configurable.get("repo_url")
    langgraph_store = get_langgraph_store()

    agent_backend, memory_paths, repo_memory_content = _prepare_repo_backend_context(
        repo_url=repo_url,
        backend=backend,
    )
    if repo_memory_content:
        configurable["_repo_memory_content"] = repo_memory_content

    main_model = make_main_model(configurable.get("model_id"))
    subagent_model = make_main_model(configurable.get("model_id"))

    logger.info(
        "返回带 Backend 的 Agent：thread_id=%s task_kind=%s",
        thread_id,
        task_kind,
    )

    return create_deep_agent(
        model=main_model,
        tools=[
            web_search,
            fetch_url,
            open_gitee_pull_request,
            publish_gitee_pr_comment,
            get_gitee_pull_request_context,
            open_github_pull_request,
            publish_github_pr_comment,
            get_github_pull_request_context,
            open_codeup_merge_request,
            publish_codeup_mr_comment,
            get_codeup_merge_request_context,
            load_default_review_rules,
            get_review_diff_summary,
            validate_review_finding_location,
            add_review_finding,
            list_review_findings,
        ],
        system_prompt=get_system_prompt(task_kind),
        subagents=[
            _general_purpose_subagent(subagent_model),
            _code_reviewer_subagent(subagent_model),
        ],
        backend=agent_backend,
        middleware=[
            ContextInjectionMiddleware(),
            MessageSanitizeMiddleware(),
            SanitizeToolInputsMiddleware(backend=backend),
            TodoListMiddleware(),
            ToolTrackingMiddleware(backend=backend),
            create_summarization_tool_middleware(main_model, agent_backend),
            ModelCallLimitMiddleware(
                run_limit=MODEL_CALL_RECURSION_LIMIT,
                exit_behavior="end",
            ),
            ToolErrorMiddleware(backend=backend),
        ],
        skills=["/skills/"],
        memory=memory_paths,
        checkpointer=get_checkpointer(),
        store=langgraph_store,
    ).with_config(config)
