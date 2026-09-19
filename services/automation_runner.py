"""把循环接到真实 Agent 运行时上。

只做一件事：给 :func:`services.automation_loop.run_cycle` 提供一个真正的「跑一轮」
实现，内部走 :func:`agent.runtime.task_router.run_agent_task`。

**任务范围**：只读任务（项目分析 / 问题解答 / 代码审查 / 仓库同步 / 工作区查看）
与**写入任务**（``coding``）都能跑。写入任务有两条额外规矩：

1. **必须配验证检查项**。循环改完代码之后没有人在旁边看，唯一的判断依据就是检查项；
   一条都没配就拒掉，而不是「先改了再说」。判定只看「配没配」，不看命令内容 ——
   命令能不能跑由验证门自己判。
2. **走一条不经过方案确认的路径**（``skip_plan_confirmation``）。交互路径会把首轮的
   开发需求转成 planning 并停下来等用户确认，而无人值守的循环没人会去点那个确认，
   于是每轮都停在同一步。这条路只给循环用，交互路径的行为不变。

``planning`` 仍然拒掉：它按设计就是产出方案等确认，放进循环等于空转一轮。

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from services.automation_loop import (
    MAX_APPROVAL_VOTES_PER_ROUND,
    RoundRequest,
    RoundResult,
    RoundRunner,
)
from services.automation_verification import ContainerExec

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
"""允许在循环里自动执行的只读任务类型。

与 :func:`utils.intent_utils.is_read_only_task` 有两处**刻意的**不同，别顺手改成一致：

- **少 ``planning``**：项目判定里方案规划算只读（它确实不改文件），但现有路径会把方案
  停下来等用户确认，无人值守的循环会永远停在那一步，等于白跑。
- **多 ``sync``**：项目那套判的是「能不能改文件、提交、push」，``sync`` 只拉取不产出改动；
  「定时拉最新代码」正是循环的典型用途。
"""

WRITE_KINDS = frozenset({"coding"})
"""允许在循环里自动执行的写入任务类型。

写入任务能在无人值守时改代码，代价是「改坏了也没人当场看见」，所以额外要求
**必须配验证检查项**（见 :func:`refusal_for_kind`）—— 检查项是这种任务唯一的
独立判断依据。
"""

REFUSED_KINDS = frozenset({"planning"})
"""明确拒掉的任务类型。

``planning`` 按设计就是「产出方案 → 停下来等用户确认」。交互路径里这是对的，
放进循环就是每轮都停在同一步、还把它记成 ``blocked``（不算失败），
``maxConsecutiveFailures`` 永远不会触发。要跑这类需求，把任务指令写成明确的
实现内容，它会判成 ``coding``。
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


def refusal_for_kind(
    kind: str, *, has_checks: bool, auto_approve: bool = False
) -> str | None:
    """判断一个已经判好的任务类型能不能在循环里跑。

    Args:
        kind: 任务类型取值。
        has_checks: 这个任务有没有配验证检查项。
        auto_approve: 是否开启全流程自动化表决。开启后由表决流程承担无人值守
            的安全闸门，不再因为缺少验证命令拒绝写入任务。

    Returns:
        不能跑时返回给人看的原因；可以执行时返回 ``None``。
    """
    if kind in READ_ONLY_KINDS:
        return None
    if kind in WRITE_KINDS:
        if has_checks or auto_approve:
            return None
        return (
            "写入任务必须至少配一条验证检查项：循环改完代码时没有人在旁边看，"
            "检查项是唯一的独立判断依据。请先补上「跑什么命令才算改对了」再运行。"
        )
    from agent.runtime.task_router import TASK_KIND_LABELS

    label = TASK_KIND_LABELS.get(kind, kind)
    if kind in REFUSED_KINDS:
        return (
            f"「{label}」会停下来等用户确认方案，无人值守的循环跑不出结果："
            "请把任务指令写成明确的实现内容，它会被识别为开发实现并直接执行。"
        )
    return f"暂不支持的任务类型：{label}"


def loop_scope_refusal(
    instruction: str, *, has_checks: bool, auto_approve: bool = False
) -> str | None:
    """判断一条任务指令能不能在循环里跑。

    传进来的应该是**用户写的原始任务指令**，不是拼上循环状态与验证反馈之后的那份 ——
    附加上去的文字会影响任务类型判定。

    Args:
        instruction: 原始任务指令。
        has_checks: 这个任务有没有配验证检查项。
        auto_approve: 是否开启全流程自动化表决。

    Returns:
        不能跑时返回给人看的原因；可以执行时返回 ``None``。
    """
    return refusal_for_kind(
        resolve_task_kind(instruction),
        has_checks=has_checks,
        auto_approve=auto_approve,
    )


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


@dataclass(frozen=True)
class _Settled:
    """开着表决时，这一轮撞上的审批处理完之后落到哪。"""

    status: str
    messages: list[Any]
    votes: tuple[dict[str, Any], ...] = ()
    vote_tokens: int = 0
    stalled_reason: str = ""
    """非空表示**开了表决但没得出结论**，这一轮仍然要交回人工。"""


def _reject_message(reason: str) -> str:
    """拼出表决否决时交回给模型的话。

    人工点拒绝时用的是「用户拒绝了这次命令执行」。代行的表决不能沿用那句：它得
    说清是机器投的票、以及否决理由。理由本身就是让模型换做法的依据 —— 不传进去，
    模型只会原样重试同一条命令，然后撞上同一次否决。

    Args:
        reason: 表决给出的否决理由。

    Returns:
        交回给模型的说明。
    """
    return (
        "这次命令没有通过评审表决（无人值守的代行审批，没有人在场）。\n"
        f"否决理由：{reason}\n"
        "不要原样重试同一条命令。这件事如果确实必须做，换一条更安全的做法；"
        "如果做不到，就直接说明卡在哪并结束这一轮。"
    )


def _vote_note(votes: Sequence[Mapping[str, Any]]) -> str:
    """把这一轮的表决结果拼成一句给人看的话。

    Args:
        votes: 各次表决的状态字典。

    Returns:
        说明文本；一次表决都没有时返回空串。
    """
    approved = sum(1 for item in votes if item.get("decision") == "approve")
    rejected = sum(1 for item in votes if item.get("decision") == "reject")
    parts = []
    if approved:
        parts.append(f"{approved} 次放行")
    if rejected:
        parts.append(f"{rejected} 次否决")
    if not parts:
        return ""
    commands = [
        str(command)
        for item in votes
        if item.get("decision") == "approve"
        for command in (item.get("commands") or [])
    ]
    line = "本轮审批表决：" + "、".join(parts)
    return f"{line}（放行的命令：{'；'.join(commands)}）" if commands else line


def _stalled_note(reason: str) -> str:
    """说明「开了表决但这一次没能定下来」。

    Args:
        reason: 没能定下来的原因。

    Returns:
        给人看的说明。
    """
    return f"已开启「审批交给表决」，但这一次没能得出结论，仍需人工处理：{reason}"


def _settle_approvals(
    *,
    thread_id: str,
    instruction: str,
    model_id: str | None,
    status: str,
    messages: list[Any],
    auto_approve: bool,
) -> _Settled:
    """开着表决时，把这一轮撞上的审批处理掉。

    只有「这个任务开了表决」且「这一轮停在等人工」才动手。每次表决都记下来，
    包括**没能得出结论的那几次** —— 「开了全自动但这次没能定」和「表决放行了」
    是两件不同的事，只记后者会让人以为这一轮根本没有人工介入。

    用满 :data:`MAX_APPROVAL_VOTES_PER_ROUND` 次还在撞审批就停手退回人工：模型可以
    一直换做法、一直撞审批，没有上限就是拿模型的调用换一圈空转。

    Args:
        thread_id: 这一轮的会话 id。
        instruction: 用户写的原始任务指令，交给评审判断命令是否符合意图。
        model_id: 评审用的模型。
        status: 这一轮此刻的运行状态。
        messages: 这一轮此刻的消息列表。
        auto_approve: 这个任务有没有开启表决。

    Returns:
        处理之后的落点。
    """
    if not auto_approve or status != "awaiting_approval":
        return _Settled(status=status, messages=messages)

    from agent.graph.graph import get_store
    from agent.runtime.task_router import resume_agent_task
    from config.approval_runtime import get_pending_approval
    from services.approval_vote import vote_on_approval

    store = get_store()
    votes: list[dict[str, Any]] = []
    vote_tokens = 0
    stalled_reason = ""

    for _attempt in range(MAX_APPROVAL_VOTES_PER_ROUND):
        try:
            pending = get_pending_approval(thread_id)
        except Exception:
            logger.exception("读取待审批内容失败：thread_id=%s", thread_id)
            pending = None
        if not pending:
            stalled_reason = "读不到待审批内容"
            break

        outcome = vote_on_approval(pending, instruction=instruction, model_id=model_id)
        votes.append(outcome.as_state_dict())
        vote_tokens += outcome.tokens
        if not outcome.decided:
            # 没能得出结论既不能当同意也不能当否决，交回人工。
            stalled_reason = outcome.reason
            break

        try:
            resumed = resume_agent_task(
                thread_id=thread_id,
                decision="approve" if outcome.approved else "reject",
                approval_id=str(pending.get("id") or ""),
                reject_message=None if outcome.approved else _reject_message(outcome.reason),
            )
        except Exception as exc:
            # 恢复失败不能把这一轮带崩：待审批记录还在，人还能接手继续处理。
            logger.exception("表决之后恢复会话失败：thread_id=%s", thread_id)
            stalled_reason = f"表决之后的恢复失败：{type(exc).__name__}: {exc}"
            break

        messages = list(resumed.get("messages") or [])
        status = str(
            resumed.get("status")
            or (store.get_thread(thread_id) or {}).get("latest_run_status")
            or ""
        )
        if status != "awaiting_approval":
            break
    else:
        stalled_reason = (
            f"这一轮已经表决 {MAX_APPROVAL_VOTES_PER_ROUND} 次还在撞审批，交给人工"
        )

    return _Settled(
        status=status,
        messages=messages,
        votes=tuple(votes),
        vote_tokens=vote_tokens,
        stalled_reason=stalled_reason,
    )


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


CONTAINER_REPO_CANDIDATES = (
    "projects/{repo}",
    "/projects/{repo}",
    "/workspace/{repo}",
    "~/projects/{repo}",
    "~/{repo}",
)
"""容器里仓库可能落在的几个位置，按常见程度排列。

沙箱镜像不是我们控制的，容器内的工作目录只有跑起来才知道，所以这里**不猜**：
逐个位置去问 git，问到哪个算哪个；一个都对不上就如实记「没有执行验证」。
"""

CONTAINER_PROBE_TIMEOUT_SECONDS = 30
"""问一次容器内仓库目录的超时。只读命令，30 秒足够；卡住也不能把一轮拖死。"""


def resolve_container_exec(
    thread_id: str, repo_url: str, *, backend_type: str
) -> ContainerExec | None:
    """解析这一轮该怎样在容器里跑验证命令。

    只有沙箱 + 容器里确实存在仓库时才返回结果。**探不到就返回 ``None``** ——
    调用方会记「没有执行验证」，而不是退回本机目录糊一份与目标仓库无关的结论。

    探路命令用 ``git -C <候选目录> rev-parse --show-toplevel``：它是一条只读命令，
    审批策略判 ``allow``，所以既能过沙箱自己的策略闸，也不会凭空多出一条「未识别
    的命令需要审批」。命令正则里 ``-C`` 会被剥掉再匹配，这条正是既有规则专门覆盖
    过的形式。

    Args:
        thread_id: 这一轮的会话 id。
        repo_url: 仓库地址。
        backend_type: 执行环境；不是沙箱时直接返回 ``None``。

    Returns:
        容器执行所需的后端与目录；不需要或探不到时返回 ``None``。
    """
    if backend_type != "sandbox":
        return None

    try:
        from agent.runtime.repo_mapping import parse_repo_url
        from server import ensure_backend_for_thread

        backend = ensure_backend_for_thread(thread_id, backend_type)
        if getattr(backend, "workspace", None) is not None:
            # 后端有本机工作区，说明这一轮其实落在本地，走本机那条路。
            return None
        repo = parse_repo_url(repo_url)
    except Exception:
        logger.exception("解析容器内的仓库目录失败：thread_id=%s", thread_id)
        return None

    for template in CONTAINER_REPO_CANDIDATES:
        candidate = template.format(repo=repo.repo)
        try:
            response = backend.execute(
                f"git -C {candidate} rev-parse --show-toplevel",
                timeout=CONTAINER_PROBE_TIMEOUT_SECONDS,
            )
        except Exception:
            logger.exception("在容器里探测仓库目录失败：candidate=%s", candidate)
            continue
        if int(getattr(response, "exit_code", 1) or 0) != 0:
            continue
        found = str(getattr(response, "output", "") or "").strip().splitlines()
        # 取最后一行：git 的报错也会混在输出里，真正的工作区路径在最后一行。
        resolved = found[-1].strip() if found else ""
        if resolved:
            logger.info("容器内仓库目录：%s", resolved)
            return ContainerExec(backend=backend, repo_dir=resolved)

    logger.info("没能在容器里找到仓库目录，这一轮不执行验证：thread_id=%s", thread_id)
    return None


def _latest_run_id(store: Any, thread_id: str) -> str | None:
    """取这个会话最近一次运行记录的 id。

    读不到就返回 ``None`` —— 运行记录是旁路信息，缺了它只是界面上少一个跳转入口，
    不该让这一轮失败。

    Args:
        store: 业务 Store。
        thread_id: 会话 id。

    Returns:
        运行 id；读不到时返回 ``None``。
    """
    with contextlib.suppress(Exception):
        record = store.get_latest_run(thread_id) or {}
        run_id = record.get("run_id")
        if run_id:
            return str(run_id)
    return None


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
        refusal = refusal_for_kind(
            kind,
            has_checks=bool(request.checks),
            auto_approve=request.auto_approve,
        )
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
        # 手动触发时 API 已经提前建好索引，让详情页在收到回执后立即可读；
        # 调度器直接调用 runner 时仍由这里负责首次建档。
        store = get_store()
        existing_thread = (
            store.get_thread(current_thread)
            if hasattr(store, "get_thread")
            else None
        )
        if existing_thread is None:
            initialize_task_record(
                repo_url=request.repo,
                prompt=instruction,
                thread_id=current_thread,
                backend_type=environment,
                source="schedule",
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
                # 循环专用：写入任务直接执行，不停下来等一个没人会点的方案确认。
                skip_plan_confirmation=True,
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
                run_id=_latest_run_id(store, current_thread),
                workspace=resolve_workspace(
                    current_thread, request.repo, backend_type=environment
                ),
            )

        store = get_store()
        thread = store.get_thread(current_thread) or {}
        # 本次调用返回的状态直接决定是否继续审批，避免额外查询改变判断依据。
        status = str(result.get("status") or thread.get("latest_run_status") or "")
        messages = list(result.get("messages") or [])

        # 开着表决的任务，撞上审批就地处理掉，不再把「等一个没人会点的确认」留给下一轮。
        settled = _settle_approvals(
            thread_id=current_thread,
            instruction=instruction,
            model_id=model_id,
            status=status,
            messages=messages,
            auto_approve=request.auto_approve,
        )
        status = settled.status
        messages = settled.messages

        summary = extract_final_assistant_text(messages)
        if status == "failed":
            record = store.get_latest_run(current_thread) or {}
            summary = str(record.get("error") or summary or "这一轮运行失败")
        elif status == "awaiting_approval":
            # 停在等人工审批时拿不到正文，必须自己说明在等什么，否则这一轮在状态文件里
            # 只剩一条空 summary。
            waiting = _awaiting_approval_summary(current_thread)
            summary = f"{summary}\n\n{waiting}" if summary.strip() else waiting
            if settled.stalled_reason:
                summary = f"{summary}\n\n{_stalled_note(settled.stalled_reason)}"

        note = _vote_note(settled.votes)
        if note:
            # 自己放行了一条需要审批的命令，这一轮就该在摘要里说清是哪一条 ——
            # 只在状态文件里留着，看列表的人不会知道有人（或机器）替他把闸门开了。
            summary = f"{summary}\n\n{note}" if summary.strip() else note

        return RoundResult(
            outcome=_outcome_from_status(status),
            summary=summary,
            # 停在等人工确认要单独记下来。它虽然也落在 blocked 里，但和「状态还没
            # 落定、下一轮接着看」不是一回事 —— 这一条要循环停下来等人。
            waiting_approval=status == "awaiting_approval",
            thread_id=current_thread,
            # 表决是这一轮为了继续跑而额外花的，得算进这一轮的用量；
            # 恢复之后的几段消息都在同一个 state 里，取最终这一份就够，不会重复累加。
            tokens=_tokens_used(messages) + settled.vote_tokens,
            # 跑完才解析：仓库是这一轮才 clone 下来的，跑之前那个目录还不存在。
            workspace=resolve_workspace(
                current_thread, request.repo, backend_type=environment
            ),
            run_id=str(result.get("run_id") or "") or _latest_run_id(store, current_thread),
            # 仓库在容器里时，验证得进容器跑；这里顺手把「怎么进去」也带回去。
            # 没配检查项就不用探 —— 探路本身要往容器里发几条命令，别白跑。
            container_exec=(
                resolve_container_exec(
                    current_thread, request.repo, backend_type=environment
                )
                if request.checks
                else None
            ),
            approval_votes=settled.votes,
        )

    return run
