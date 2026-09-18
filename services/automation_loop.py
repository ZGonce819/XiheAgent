"""自动化任务的循环。

把前面两块接起来 —— 状态（:mod:`config.automation_state`）与验证门
（:mod:`services.automation_verification`），再加上「现在该不该跑」的判断，
组成一轮完整循环：

    判断该不该跑 → 读状态 → 拼本轮指令 → 跑一轮 → 独立验证 → 写状态 → 判断要不要停

「跑一轮」由调用方以 ``round_runner`` 注入。本模块**不直接调用 Agent 运行时**，
原因有两条：

1. 自动化任务该走哪条流程是产品决策。现有交互路径会把首轮的 coding 需求转成
   planning 并停下来等用户确认，这对无人值守的循环是错的 —— 得先决定循环里
   走哪条路径，不该由这里替产品猜。
2. 注入之后，整条循环可以在不调用任何模型的情况下被完整测试。

Note:
    本模块不启动后台线程，也不注册定时器。什么时候调用 :func:`run_cycle`
    由外部决定：界面上的手动按钮、外部 cron，或者
    :mod:`services.automation_scheduler` 那个常驻调度器。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config.automation_config import list_automations
from config.automation_state import (
    load_state,
    record_run,
    render_state,
    reset_inner_iterations,
    set_last_feedback,
    stop_status,
)
from services.automation_verification import (
    Check,
    ContainerExec,
    Runner,
    VerificationResult,
    make_container_runner,
    parse_checks,
    run_checks,
    unverified,
)
from services.cron_schedule import CronSchedule, parse_cron


def _no_workspace_reason(backend_type: str, outcome: str) -> str:
    """说明这一轮为什么没法执行验证命令。

    Args:
        backend_type: 执行环境。
        outcome: 这一轮的结果。

    Returns:
        给人看的原因。
    """
    if outcome == "failed":
        # 这一轮自己就没跑完，没什么可验的。别把原因写成「不知道仓库在哪」——
        # 那会让看状态的人以为目录解析坏了。
        return "这一轮没有跑完，没有执行验证"
    if backend_type == "sandbox":
        return (
            "仓库在沙箱容器里，但没能确定容器内的仓库目录，"
            "这一轮没有执行验证"
        )
    return "没有确定仓库在本机的目录，这一轮没有执行验证"


@dataclass(frozen=True)
class RoundRequest:
    """交给「跑一轮」的全部输入。"""

    automation_id: str
    name: str
    prompt: str
    """真正发给模型的那份指令：原始指令 + 循环状态 + 上一轮验证反馈。"""

    instruction: str = ""
    """用户写的**原始**任务指令，不含附加状态与反馈。

    「跑一轮」要判断任务类型时必须用这一份 —— 附加上去的文字会改变判定结果。
    """

    repo: str | None = None
    model_id: str = "__builtin__"
    round_number: int = 1
    auto_approve: bool = False
    """这个任务有没有开启「审批交给表决」。

    开启后，需要审批的命令由三个评审表决决定，不需要人在场
    （见 :mod:`services.approval_vote`）。默认关闭 —— 这是把一处人工闸门交给机器，
    必须是每个任务各自显式开的。
    """

    checks: tuple[Check, ...] = ()
    """这个任务配置的验证检查项（已解析）。

    「跑一轮」要用它判断**这个任务写着写入类需求、却一条检查都没配**的情况 ——
    那等于让循环改完代码之后没有任何人验，必须当场拒掉，而不是跑完再说。
    只看「配了几条」，不看具体命令 —— 命令能跑不能跑由验证门自己判。
    """


@dataclass(frozen=True)
class RoundResult:
    """「跑一轮」交回来的结果。"""

    outcome: str = "succeeded"
    summary: str = ""
    changed: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    tokens: int = 0
    waiting_approval: bool = False
    """这一轮是不是停在等人工确认。

    是的话循环会停下来等人，不再往后跑 —— 无人值守的循环里没人会去点那个确认，
    下一轮只会卡在同一个地方。
    """
    thread_id: str | None = None
    workspace: str | None = None
    """这一轮实际在哪个目录里跑的，供验证命令当工作目录。

    由「跑一轮」自己填：只有它知道仓库被 clone 到了哪。为空表示说不清在哪，
    此时**不执行验证** —— 退回进程当前目录会在后端自己的仓库里跑检查项，
    验证结论与目标仓库无关。
    """

    approval_votes: tuple[dict[str, Any], ...] = ()
    """这一轮里几次「审批交给表决」的过程记录。

    形状见 :meth:`services.approval_vote.VoteOutcome.as_state_dict`。**得不出结论
    的那几次也要记** —— 「开了全自动表决，但这次没能定」和「表决放行了」是两件
    不同的事，只记后者会让人以为这一轮没有任何人工介入。
    """

    run_id: str | None = None
    """这一轮在运行记录里的 id。

    由「跑一轮」在跑完之后填 —— 触发那一刻还不知道它。调用方拿它写回定义里的
    ``lastRunId``，界面上才能从任务直接跳到这一轮的记录。
    """

    container_exec: ContainerExec | None = None
    """仓库在沙箱容器里时，验证命令该怎么进容器执行。

    由「跑一轮」在跑完之后解析（仓库是这一轮才克隆下来的，跑之前那个目录还不
    存在），交给循环去跑检查项。为空表示按本机 ``subprocess`` 验，或者压根验不了。
    """


MAX_APPROVAL_VOTES_PER_ROUND = 5
"""一轮里最多表决几次。

用满之后这一轮就退回等人工 —— 保留原有的 ``awaiting_approval`` 行为作为兜底。
没有这个上限，模型可以一直换做法、一直撞审批，每撞一次烧三次模型调用。
"""

RoundRunner = Callable[[RoundRequest], RoundResult]


@dataclass(frozen=True)
class CycleResult:
    """一轮循环跑完之后的结论。"""

    automation_id: str
    ran: bool
    outcome: str | None = None
    summary: str = ""
    verification: dict[str, Any] | None = None
    stopped: bool = False
    stop_reason: str | None = None
    skipped_reason: str | None = None
    approval_votes: tuple[dict[str, Any], ...] = ()
    """本轮发生的审批表决，供调度器判断有没有需要写进定义的异常。"""
    run_id: str | None = None
    """本轮在运行记录里的 id，供调用方写回定义的 ``lastRunId``。"""
    inner_iterations: int = 1
    """本次触发内的迭代次数。大于 1 表示内层循环跑了多次（验证未通过后重试）。"""

    def as_dict(self) -> dict[str, Any]:
        """转成可以直接返回给前端的字典。

        Returns:
            含本轮是否执行、结论、验证结果与停止状态的字典。
        """
        return {
            "automationId": self.automation_id,
            "ran": self.ran,
            "outcome": self.outcome,
            "summary": self.summary,
            "verification": self.verification,
            "stopped": self.stopped,
            "stopReason": self.stop_reason,
            "skippedReason": self.skipped_reason,
            "approvalVotes": [dict(item) for item in self.approval_votes],
            "runId": self.run_id,
            "innerIterations": self.inner_iterations,
        }


@dataclass(frozen=True)
class DueScan:
    """一次「谁该跑了」的扫描结果。"""

    due: tuple[dict[str, Any], ...] = ()
    problems: tuple[tuple[str, str], ...] = ()
    """扫不动或者配错了的自动化：``(id, 原因)``。**不静默跳过** ——
    一个写错的执行计划如果没人提，表现为「任务再也没跑过」，很难查。"""


def _parse_moment(value: Any) -> datetime | None:
    """把状态里的时间文本解析成带时区的时刻。

    Args:
        value: ISO 8601 文本；空值或无法解析都返回 ``None``。

    Returns:
        带时区的时刻，或 ``None``。
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment


def is_due(
    schedule: CronSchedule,
    last_triggered_at: Any,
    now: datetime,
) -> bool:
    """判断一个自动化现在该不该跑。

    规则是「当前这一分钟命中，且这一分钟还没跑过」。这样处理有两个好处：
    进程停了几天再起来，不会把错过的每一分钟都补跑一遍；同一个分钟内的重复
    调用也只会触发一次。

    Args:
        schedule: 已解析的执行计划。
        last_triggered_at: 上次触发时间，来自自动化定义；空值表示从未跑过。
        now: 当前时刻。

    Returns:
        该跑返回 ``True``。
    """
    if not schedule.matches(now):
        return False
    previous = _parse_moment(last_triggered_at)
    if previous is None:
        return True
    return previous.replace(second=0, microsecond=0) < now.replace(second=0, microsecond=0)


def due_automations(now: datetime | None = None) -> DueScan:
    """扫一遍全部自动化，挑出现在该跑的。

    停用的（``enabled`` 为假）直接跳过；执行计划写错的记进 ``problems``。

    Args:
        now: 当前时刻；不传则取当前 UTC 时间。

    Returns:
        该跑的自动化列表与配置问题列表。
    """
    moment = now or datetime.now(UTC)
    due: list[dict[str, Any]] = []
    problems: list[tuple[str, str]] = []

    for automation in list_automations():
        if not automation.get("enabled", True):
            continue
        identifier = str(automation.get("id") or "")
        try:
            schedule = parse_cron(str(automation.get("schedule") or ""))
        except ValueError as exc:
            problems.append((identifier, str(exc)))
            continue
        if is_due(schedule, automation.get("lastTriggeredAt"), moment):
            due.append(automation)

    return DueScan(due=tuple(due), problems=tuple(problems))


def build_round_prompt(
    prompt: str,
    *,
    round_number: int,
    state_text: str = "",
    feedback: str = "",
) -> str:
    """拼出这一轮真正发给模型的任务指令。

    在用户写的任务指令后面附上循环状态与上一轮的验证反馈。附状态是为了让这一轮
    知道自己上一轮干到哪、哪些做法已经证明没用；附反馈是为了让它先解决没过的那一项。

    Args:
        prompt: 用户在自动化定义里写的任务指令。
        round_number: 这是第几轮。
        state_text: 循环状态的文本，通常来自
            :func:`config.automation_state.render_state`。
        feedback: 上一轮的验证反馈，通常来自
            :meth:`services.automation_verification.VerificationResult.feedback`。

    Returns:
        拼好的多行指令。
    """
    parts = [prompt.strip(), "", "---", f"这是这个自动任务的第 {round_number} 轮。"]
    if state_text.strip():
        parts += ["", "循环状态（供你判断进度，已完成的事不要重复做）：", "", state_text.strip()]
    if feedback.strip():
        parts += ["", "上一轮的验证没有通过，反馈如下：", "", feedback.strip()]
    return "\n".join(parts)


def run_cycle(
    automation: Mapping[str, Any],
    round_runner: RoundRunner,
    *,
    backend_type: str = "local",
    check_runner: Runner | None = None,
    workspace: Path | str | None = None,
) -> CycleResult:
    """跑一轮完整循环（含内层反馈迭代）。

    顺序是：先看该不该停 → 读状态 → **循环** {拼指令 → 跑一轮 → 独立验证 →
    判断是否继续} → 写状态 → 再看该不该停。内层循环在验证未通过时立即把反馈
    交回模型重试，而不是等下一次 cron 触发。

    **跑之前也要检查一次**：上一轮可能已经把循环跑停了，这一轮不该再启动。

    Args:
        automation: 自动化定义，字段与 :mod:`config.automation_config` 一致。
        round_runner: 「跑一轮」的实现，由调用方注入。
        backend_type: 执行环境，``"local"`` 或 ``"sandbox"``，用于审批策略判断。
        check_runner: 验证命令的执行器；不传时用本机 ``subprocess``。
        workspace: 验证命令的工作目录。

    Returns:
        本轮结论。

    Raises:
        ValueError: ``round_runner`` 返回了未知的 ``outcome``。
    """
    identifier = str(automation.get("id") or "")
    if not identifier:
        return CycleResult(automation_id="", ran=False, skipped_reason="自动化任务缺少 id")

    # 每次触发开始时先重置内层迭代计数 —— 必须在 stop_status 之前，否则上一次触发
    # 留下的 innerIterationsUsed 会让 max_inner_iterations 这条停止条件误判，
    # 把这一次触发直接拦在门外。
    reset_inner_iterations(identifier)

    stopped, reason = stop_status(identifier)
    if stopped:
        return CycleResult(
            automation_id=identifier, ran=False, stopped=True, stop_reason=reason
        )

    try:
        checks: tuple[Check, ...] = parse_checks(automation.get("checks"))
    except ValueError as exc:
        return CycleResult(
            automation_id=identifier, ran=False, skipped_reason=f"检查项配置有误：{exc}"
        )

    state = load_state(identifier)
    round_number = int(state["budget"]["runsUsed"]) + 1
    instruction = str(automation.get("prompt") or "")
    initial_feedback = str(state.get("lastFeedback") or "")
    state_text = render_state(identifier)

    # 内层循环：执行→验证→反馈→再执行
    result: RoundResult | None = None
    verification: VerificationResult | None = None
    feedback = initial_feedback
    total_tokens = 0
    inner_iterations = 0

    while True:
        inner_iterations += 1

        request = RoundRequest(
            automation_id=identifier,
            name=str(automation.get("name") or ""),
            prompt=build_round_prompt(
                instruction,
                round_number=round_number,
                state_text=state_text,
                feedback=feedback,
            ),
            instruction=instruction,
            repo=automation.get("repo") or None,
            model_id=str(automation.get("model") or "__builtin__"),
            round_number=round_number,
            # 开关只在定义里显式为真时才算开；缺字段、字符串 "false" 之类一律按关。
            auto_approve=automation.get("autoApprove") is True,
            checks=checks,
        )

        try:
            result = round_runner(request)
        except Exception as exc:
            # 「跑一轮」可能连着真实的 Agent 运行时，任何异常都不该让状态停在半路。
            # 记成一次失败，交给连续未通过次数这条停止条件去升级。
            result = RoundResult(
                outcome="failed",
                summary=f"这一轮没能跑完：{type(exc).__name__}: {exc}",
            )

        total_tokens += result.tokens
        verification = None
        if checks:
            # 验证命令必须在**目标仓库**里跑。调用方没指定工作目录时用「跑一轮」报上来的
            # 那个；两者都没有就**不跑** —— 退回进程当前目录会在后端自己的仓库里跑检查项，
            # 得到一份与目标仓库无关的结论，比「没验证」更糟。
            effective_workspace = workspace or result.workspace
            executor = check_runner
            if executor is None and result.container_exec is not None:
                # 仓库在沙箱容器里：本机 subprocess 到不了，改在容器内、容器里的仓库目录下执行。
                executor = make_container_runner(result.container_exec)
            if effective_workspace is None and executor is None:
                verification = unverified(
                    _no_workspace_reason(backend_type, result.outcome), checks
                )
            else:
                verification = run_checks(
                    checks,
                    backend_type=backend_type,
                    workspace=effective_workspace,
                    runner=executor,
                )

        # 判断是否继续内层循环
        should_continue = False
        if verification is not None and not verification.passed:
            # 验证未通过：检查是否应该继续迭代
            if result.outcome not in ("failed", "blocked") and not result.waiting_approval:
                # 更新反馈给下一次迭代
                feedback = verification.feedback()
                # 用本地计数器判断内层上限 —— stop_status 读的是磁盘上的
                # innerIterationsUsed，而那个值要等循环退出后才由 record_run 写入，
                # 循环期间它一直是 0，靠它判断会永远不到上限。
                # max_inner 为 0 时不启用内层循环，退回「跑一次就返回」的原有行为。
                max_inner = int(state["budget"].get("maxInnerIterations") or 0)
                if max_inner <= 0:
                    should_continue = False
                elif inner_iterations >= max_inner:
                    should_continue = False
                else:
                    stopped_now, _ = stop_status(identifier)
                    if not stopped_now:
                        should_continue = True

        if not should_continue:
            break

    # 循环结束，写入最终结果
    assert result is not None
    record_run(
        identifier,
        outcome=result.outcome,
        summary=result.summary,
        thread_id=result.thread_id,
        changed=result.changed,
        next_steps=result.next_steps,
        verification=verification.as_state_dict() if verification else None,
        tokens=total_tokens,
        waiting_approval=result.waiting_approval,
        approval_votes=result.approval_votes,
        inner_iterations=inner_iterations,
    )
    set_last_feedback(
        identifier,
        ""
        if verification is None or verification.passed
        else verification.feedback(),
    )

    stopped, reason = stop_status(identifier)
    return CycleResult(
        automation_id=identifier,
        ran=True,
        outcome=result.outcome,
        summary=result.summary,
        verification=verification.as_state_dict() if verification else None,
        stopped=stopped,
        stop_reason=reason,
        approval_votes=result.approval_votes,
        run_id=result.run_id,
        inner_iterations=inner_iterations,
    )
