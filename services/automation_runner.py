"""把循环接到真实 Agent 运行时上。

只做一件事：给 :func:`services.automation_loop.run_cycle` 提供一个真正的「跑一轮」
实现，内部走 :func:`agent.runtime.task_router.run_agent_task`。

**当前只放行只读任务。** 任务类型由 :func:`utils.intent_utils.classify_task_kind`
判定，只有项目分析 / 问题解答 / 代码审查 / 仓库同步 / 工作区查看这几类会真正执行；
「开发实现」与「方案规划」会被拒掉。原因是现有交互路径会把首轮的 coding 需求转成
planning 并停下来等用户确认 —— 对无人值守的循环，那等于永远停在等确认那一步。
等循环有了自己的写入路径（或者想清楚它该不该直接改代码）再放开。

Note:
    所有 ``agent.*`` 的 import 都放在函数内部，这是项目既有约定：``services`` 与
    ``agent.runtime`` 之间存在环（``services/__init__`` → ``streaming_service`` →
    ``agent.runtime.task_router`` → ``agent.runtime._helpers`` → ``server`` →
    ``agent.middleware``），模块级导入会炸。

Note:
    这一轮消耗的 token 数从模型返回消息的 ``usage_metadata`` 里累加得到（见
    :func:`_tokens_used`），随 :func:`config.automation_state.record_run` 计入预算，
    ``maxTokens`` 上限据此生效。拿不到用量时按 0 记 —— 统计不上不该把一轮好活
    判成失败。

Note:
    本模块不负责「什么时候跑」。触发时机由
    :mod:`services.automation_loop` 与调用它的接口决定。
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path

from services.automation_loop import RoundRequest, RoundResult, RoundRunner

logger = logging.getLogger(__name__)


def _awaiting_approval_summary(thread_id: str) -> str:
    """说明这一轮为什么停在等人工。

    停在等人工审批时，最后一条助手消息通常只有工具调用、没有文字，
    :func:`utils.message_utils.extract_final_assistant_text` 于是返回空串 ——
    状态文件里留下一轮**空 summary**，看的人完全不知道它在等什么。
    把待处理的命令列出来，用户才知道要点什么。

    Args:
        thread_id: 这一轮的会话 id。

    Returns:
        给人看的说明，含待处理的命令。
    """
    from config.approval_runtime import get_pending_approval

    try:
        pending = get_pending_approval(thread_id) or {}
    except Exception:
        logger.exception("读取待审批内容失败：thread_id=%s", thread_id)
        pending = {}

    commands = [
        str(action.get("command") or "").strip()
        for action in pending.get("actions") or []
        if isinstance(action, Mapping)
    ]
    commands = [item for item in commands if item]
    if not commands:
        return "这一轮停在等待人工审批，需要有人处理之后才会继续"
    return "这一轮停在等待人工审批，需要人工处理：" + "；".join(commands[:5])

READ_ONLY_KINDS = frozenset({"analysis", "qa", "sync", "inspect", "review"})
"""允许在循环里自动执行的任务类型。

其余类型（``coding`` / ``planning``）会被 :func:`read_only_refusal` 拒掉。

与 :func:`utils.intent_utils.is_read_only_task` 有两处**刻意的**不同，别顺手改成一致：

- **少 ``planning``**：项目判定里方案规划算只读（它确实不改文件），但现有路径会把方案
  停下来等用户确认，无人值守的循环会永远停在那一步，等于白跑。
- **多 ``sync``**：项目那套判的是「能不能改文件、提交、push」，``sync`` 只拉取不产出改动；
  「定时拉最新代码」正是循环的典型用途。
"""

BUILTIN_MODEL_SENTINEL = "__builtin__"
"""自动化定义里表示「用系统默认模型」的占位值，传给运行时时要换成 ``None``。"""


def resolve_task_kind(instruction: str) -> str:
    """判定一条任务指令属于哪一类。

    Args:
        instruction: 用户写的**原始**任务指令。

    Returns:
        任务类型取值，例如 ``analysis`` / ``coding``。
    """
    from utils.intent_utils import classify_task_kind

    return str(classify_task_kind(instruction))


def refusal_for_kind(kind: str) -> str | None:
    """判断一个已经判好的任务类型是否超出了「只读」范围。

    Args:
        kind: 任务类型取值。

    Returns:
        超出范围时返回给人看的原因；可以执行时返回 ``None``。
    """
    if kind in READ_ONLY_KINDS:
        return None
    from agent.runtime.task_router import TASK_KIND_LABELS

    label = TASK_KIND_LABELS.get(kind, kind)
    return (
        f"当前只允许只读任务，这一轮的任务被识别为「{label}」。"
        "要让循环自动改代码，得先给它定一条不经过方案确认的路径。"
    )


def read_only_refusal(instruction: str) -> str | None:
    """判断一条任务指令是否超出了「只读」范围。

    传进来的应该是**用户写的原始任务指令**，不是拼上循环状态与验证反馈之后的那份 ——
    附加上去的文字会影响任务类型判定。

    Args:
        instruction: 原始任务指令。

    Returns:
        超出范围时返回给人看的原因；可以执行时返回 ``None``。
    """
    return refusal_for_kind(resolve_task_kind(instruction))


def _outcome_from_status(status: str) -> str:
    """把运行状态映射成循环的结果取值。

    ``completed`` 与 ``failed`` 之外的取值（``pending`` / ``running`` / 空）
    一律算 ``blocked`` —— 这一轮没有明确的成功或失败，需要下一轮接着看。

    Args:
        status: 会话的运行状态。

    Returns:
        ``succeeded`` / ``failed`` / ``blocked``。
    """
    if status == "completed":
        return "succeeded"
    if status == "failed":
        return "failed"
    return "blocked"


def resolve_backend_type(backend_type: str | None = None) -> str:
    """确定这一轮用哪个执行环境。

    Args:
        backend_type: 调用方指定的环境；不是 ``local`` / ``sandbox`` 时按全局配置决定。

    Returns:
        ``"local"`` 或 ``"sandbox"``。
    """
    if backend_type in {"local", "sandbox"}:
        return str(backend_type)
    from config.app_config import get_settings

    return "sandbox" if get_settings().use_sandbox else "local"


def resolve_workspace(thread_id: str, repo_url: str, *, backend_type: str) -> str | None:
    """解析仓库这一轮实际被放在哪个目录，供验证命令当工作目录。

    走的是和这一轮同一个 backend（``server.ensure_backend_for_thread`` 按 thread 缓存），
    因此拿到的目录就是 Agent 真正工作的那个，不会另拼一条可能对不上的路径。

    Args:
        thread_id: 这一轮的会话 id。
        repo_url: 仓库地址。
        backend_type: 执行环境。

    Returns:
        目录存在时返回绝对路径；沙箱模式、解析失败或仓库还没 clone 下来时返回 ``None``。
        返回 ``None`` 时调用方**不执行验证**，不要退回进程当前目录。
    """
    try:
        from agent.graph.graph import get_store
        from agent.runtime.repo_mapping import discover_repo_mapping
        from server import ensure_backend_for_thread

        backend = ensure_backend_for_thread(thread_id, backend_type)
        workspace = getattr(backend, "workspace", None)
        if workspace is None:
            # 沙箱 backend 没有本机工作区，仓库在容器里。
            return None
        mapping = discover_repo_mapping(
            repo_url=repo_url,
            workspace=workspace,
            store=get_store(),
            local_folder_mode=bool(getattr(backend, "local_folder_mode", False)),
        )
    except Exception:
        logger.exception("解析仓库目录失败，这一轮不执行验证：thread_id=%s", thread_id)
        return None

    path = Path(mapping.local_path)
    if not path.is_dir():
        logger.info("仓库目录还不存在，这一轮不执行验证：%s", path)
        return None
    return str(path)


def _tokens_used(messages: Sequence[object]) -> int:
    """把一轮里各条消息的 token 用量加起来。

    消息是 :func:`agent.runtime.streaming_runtime._message_dict` 产出的字典，带上
    了模型返回的 ``usage_metadata``；这里也兼容 LangChain 消息对象，取不到就当这条
    没有用量。

    一轮里通常不止一次模型调用（工具调用要来回几趟），所以要把整轮的消息都过一遍。

    **拿不到用量时按 0 记，不能因此把这一轮判成失败。** 用量是给预算用的旁路信息，
    缺了它只是预算算不准，活儿本身可能干得好好的。

    Args:
        messages: 这一轮结束时线程里的消息。

    Returns:
        累计 token 数；一条都拿不到时为 0。
    """
    total = 0
    for message in messages:
        if isinstance(message, Mapping):
            usage = message.get("usage_metadata")
        else:
            usage = getattr(message, "usage_metadata", None)
        if not isinstance(usage, Mapping):
            continue
        with contextlib.suppress(TypeError, ValueError):
            total += max(0, int(usage.get("total_tokens") or 0))
    return total


def make_agent_round_runner(
    *,
    thread_id: str | None = None,
    backend_type: str | None = None,
) -> RoundRunner:
    """造一个走真实 Agent 运行时的「跑一轮」。

    Args:
        thread_id: 这一轮用哪个会话 id；不传则现场生成。调用方预先指定是为了
            能在触发的那一刻就把 id 记进自动化定义（``lastThreadId``），
            前端也能立刻跳过去看这一轮在干什么。
        backend_type: 执行环境，``"local"`` 或 ``"sandbox"``；``None`` 表示沿用
            全局 ``USE_SANDBOX`` 配置。

    Returns:
        可以直接传给 :func:`services.automation_loop.run_cycle` 的可调用对象。
    """

    def run(request: RoundRequest) -> RoundResult:
        # 判定只看用户写的原始指令。**判完之后要把结论一起传下去** —— 否则
        # run_agent_task 会拿拼上循环状态与验证反馈的那份文本重判一次，结论可能
        # 不一样（反馈里出现「提交」「push」就会从只读翻成开发实现），于是走进
        # 等用户确认的分支停住，而循环还会把这一轮记成「没做完但没报错」。
        instruction = request.instruction or request.prompt
        kind = resolve_task_kind(instruction)
        refusal = refusal_for_kind(kind)
        if refusal:
            return RoundResult(outcome="blocked", summary=refusal)
        if not request.repo:
            return RoundResult(
                outcome="blocked", summary="自动化任务没有配置代码仓库，无法执行"
            )

        from agent.graph.graph import get_store
        from agent.runtime.task_router import initialize_task_record, run_agent_task
        from utils.message_utils import extract_final_assistant_text

        environment = resolve_backend_type(backend_type)
        current_thread = thread_id or str(uuid.uuid4())
        model_id = (
            None if request.model_id == BUILTIN_MODEL_SENTINEL else request.model_id
        )

        # 会话记录里存**原始任务指令**而不是拼上状态与反馈的那份：任务列表是给人看的，
        # 标题里塞进一整段循环状态没有意义。模型那边拿到的仍然是完整指令。
        initialize_task_record(
            repo_url=request.repo,
            prompt=instruction,
            thread_id=current_thread,
            backend_type=environment,
        )
        try:
            result = run_agent_task(
                repo_url=request.repo,
                prompt=request.prompt,
                routing_prompt=instruction,
                task_kind=kind,
                thread_id=current_thread,
                model_id=model_id,
                backend_type=environment,
            )
        except Exception as exc:
            # run_agent_task 自己的 try 只包住了「调用 Agent 执行」那一段。它前面的
            # 准备步骤（连 Store、连 checkpoint、建 Agent 图）出错时会直接把异常抛出来，
            # 而会话状态已经写成 running —— 不补这一刀，界面上就会留下一个永远在跑、
            # 实际早就死了的会话。循环每失败一次就留一个，很快就满屏都是。
            logger.exception("自动化任务 %s 这一轮没能跑完", request.automation_id)
            store = get_store()
            with contextlib.suppress(Exception):
                store.update_thread_status(current_thread, "failed")
            return RoundResult(
                outcome="failed",
                summary=f"这一轮没能跑完：{type(exc).__name__}: {exc}",
                thread_id=current_thread,
                workspace=resolve_workspace(
                    current_thread, request.repo, backend_type=environment
                ),
            )

        store = get_store()
        thread = store.get_thread(current_thread) or {}
        status = str(thread.get("latest_run_status") or "")
        summary = extract_final_assistant_text(list(result.get("messages") or []))
        if status == "failed":
            record = store.get_latest_run(current_thread) or {}
            summary = str(record.get("error") or summary or "这一轮运行失败")
        elif status == "awaiting_approval":
            # 停在等人工审批时拿不到正文，必须自己说明在等什么，否则这一轮在状态文件里
            # 只剩一条空 summary。
            waiting = _awaiting_approval_summary(current_thread)
            summary = f"{summary}\n\n{waiting}" if summary.strip() else waiting

        return RoundResult(
            outcome=_outcome_from_status(status),
            summary=summary,
            # 停在等人工确认要单独记下来。它虽然也落在 blocked 里，但和「状态还没
            # 落定、下一轮接着看」不是一回事 —— 这一条要循环停下来等人。
            waiting_approval=status == "awaiting_approval",
            thread_id=current_thread,
            tokens=_tokens_used(list(result.get("messages") or [])),
            # 跑完才解析：仓库是这一轮才 clone 下来的，跑之前那个目录还不存在。
            workspace=resolve_workspace(
                current_thread, request.repo, backend_type=environment
            ),
        )

    return run
