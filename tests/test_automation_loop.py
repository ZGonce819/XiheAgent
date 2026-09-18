"""自动化任务循环的测试。

覆盖四块：

1. :func:`services.automation_loop.is_due` 的触发语义 —— 尤其是「同一分钟不重复触发」
   与「进程停了几天不补跑」；
2. :func:`services.automation_loop.due_automations` 的扫描 —— 停用的要跳过，
   执行计划写错的要**报出来**而不是静默略过；
3. :func:`services.automation_loop.build_round_prompt` 把状态与上一轮反馈拼进指令；
4. :func:`services.automation_loop.run_cycle` 的完整一轮，以及失败反馈如何被带进下一轮。

用例不需要模型：``round_runner`` 与 ``check_runner`` 都是注入的假对象。
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from config.automation_config import (
    create_automation,
    mark_automation_triggered,
    update_automation,
)
from config.automation_state import load_state, record_run, set_budget, set_cursor
from services.automation_loop import (
    CycleResult,
    RoundRequest,
    RoundResult,
    build_round_prompt,
    due_automations,
    is_due,
    run_cycle,
)
from services.automation_verification import VerificationResult
from services.cron_schedule import parse_cron

AUTOMATION_ID = "ab12cd34"
PASSING_CHECK = "pytest tests/"
FAILING_CHECK = "pytest tests/test_login.py"
NEEDS_APPROVAL_CHECK = "frobnicate --now"


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 ``Settings.data_dir`` 指向临时目录，隔离自动化定义与审批设置。

    Returns:
        临时的 data 目录。
    """
    from config.app_config import get_settings

    target = tmp_path / "data"
    monkeypatch.setattr(get_settings(), "data_dir", target)
    return target


def make_automation(**overrides: Any) -> dict[str, Any]:
    """造一份自动化定义。

    Returns:
        与 :mod:`config.automation_config` 同构的字典。
    """
    automation: dict[str, Any] = {
        "id": AUTOMATION_ID,
        "name": "每日分诊",
        "prompt": "读昨天的 CI 失败，整理成 TODO",
        "schedule": "0 9 * * 1-5",
        "repo": None,
        "checks": [],
        "model": "__builtin__",
        "effort": "default",
        "enabled": True,
        "lastTriggeredAt": None,
    }
    automation.update(overrides)
    return automation


def make_round_runner(
    result: RoundResult | None = None,
) -> tuple[Callable[[RoundRequest], RoundResult], list[RoundRequest]]:
    """造一个记录入参的假「跑一轮」。

    Returns:
        ``(跑一轮, 收到的请求列表)``。
    """
    requests: list[RoundRequest] = []

    def run(request: RoundRequest) -> RoundResult:
        requests.append(request)
        return result if result is not None else RoundResult()

    return run, requests


def make_check_runner(
    failing: tuple[str, ...] = (),
) -> Callable[[str, int], tuple[int, str]]:
    """造一个假的验证命令执行器。

    Args:
        failing: 这些命令返回退出码 1，其余返回 0。

    Returns:
        符合验证门 ``Runner`` 协议的可调用对象。
    """

    def run(command: str, timeout_seconds: int) -> tuple[int, str]:
        if command in failing:
            return 1, f"{command} 失败"
        return 0, ""

    return run


def test_never_run_is_due_on_a_matching_minute() -> None:
    """从没跑过的自动化，命中就该跑。"""
    schedule = parse_cron("0 9 * * 1-5")
    assert is_due(schedule, None, datetime(2026, 9, 18, 9, 0, tzinfo=UTC)) is True


def test_same_minute_is_not_due_twice() -> None:
    """同一分钟内重复调用只触发一次 —— 这是靠 lastTriggeredAt 实现的。"""
    schedule = parse_cron("0 9 * * 1-5")
    now = datetime(2026, 9, 18, 9, 0, 30, tzinfo=UTC)
    assert is_due(schedule, "2026-09-18T09:00:05+00:00", now) is False


def test_earlier_trigger_is_due_again() -> None:
    """上一分钟跑过的不影响这一分钟。"""
    schedule = parse_cron("* 9 * * 1-5")
    now = datetime(2026, 9, 18, 9, 5, tzinfo=UTC)
    assert is_due(schedule, "2026-09-18T09:04:00+00:00", now) is True


def test_missed_minutes_are_not_caught_up() -> None:
    """进程停了三天再起来，只在命中的那一分钟跑，不补跑错过的。"""
    schedule = parse_cron("0 9 * * 1-5")
    assert is_due(schedule, "2026-09-15T09:00:00+00:00", datetime(2026, 9, 18, 9, 1, tzinfo=UTC)) is False


def test_non_matching_minute_is_not_due() -> None:
    """没命中就不跑。"""
    schedule = parse_cron("0 9 * * 1-5")
    assert is_due(schedule, None, datetime(2026, 9, 18, 9, 3, tzinfo=UTC)) is False


def test_unreadable_last_triggered_at_is_treated_as_never_run() -> None:
    """触发时间读不出来时当作从没跑过，而不是永远不跑。"""
    schedule = parse_cron("0 9 * * 1-5")
    assert is_due(schedule, "不是时间", datetime(2026, 9, 18, 9, 0, tzinfo=UTC)) is True


def test_naive_last_triggered_at_is_treated_as_utc() -> None:
    """不带时区的历史值按 UTC 处理。"""
    schedule = parse_cron("* 9 * * 1-5")
    now = datetime(2026, 9, 18, 9, 30, tzinfo=UTC)
    assert is_due(schedule, "2026-09-18T09:30:00", now) is False


def test_due_automations_skips_disabled_ones(data_dir: Path) -> None:
    """停用的自动化不参与扫描。"""
    create_automation({"name": "开着的", "prompt": "做点事", "schedule": "0 9 * * 1-5"})
    paused = create_automation({"name": "停用的", "prompt": "做点事", "schedule": "0 9 * * 1-5"})
    update_automation(paused["id"], {"enabled": False})

    scan = due_automations(datetime(2026, 9, 18, 9, 0, tzinfo=UTC))
    assert [item["name"] for item in scan.due] == ["开着的"]
    assert scan.problems == ()


def test_due_automations_reports_broken_schedules(data_dir: Path) -> None:
    """执行计划写错要报出来 —— 静默略过会表现为「任务再也没跑过」，很难查。"""
    broken = create_automation({"name": "写错的", "prompt": "做点事", "schedule": "0 9 * *"})
    scan = due_automations(datetime(2026, 9, 18, 9, 0, tzinfo=UTC))
    assert scan.due == ()
    assert len(scan.problems) == 1
    assert scan.problems[0][0] == broken["id"]
    assert "5 段" in scan.problems[0][1]


def test_mark_automation_triggered_fills_the_previously_dead_fields(data_dir: Path) -> None:
    """触发后要写 lastTriggeredAt 等字段 —— 它们此前一直是 None。"""
    created = create_automation({"name": "每日", "prompt": "做点事", "schedule": "0 9 * * 1-5"})
    assert created["lastTriggeredAt"] is None
    assert created["lastRunId"] is None

    updated = mark_automation_triggered(created["id"], thread_id="t-1", run_id="r-1")
    assert updated is not None
    assert updated["lastTriggeredAt"]
    assert updated["lastThreadId"] == "t-1"
    assert updated["lastRunId"] == "r-1"
    assert updated["lastError"] is None


def test_mark_automation_triggered_records_and_clears_errors(data_dir: Path) -> None:
    """失败原因要记下来，下次成功时清掉。"""
    created = create_automation({"name": "每日", "prompt": "做点事", "schedule": "0 9 * * 1-5"})
    failed = mark_automation_triggered(created["id"], error="仓库不存在")
    assert failed is not None
    assert failed["lastError"] == "仓库不存在"
    assert failed["lastErrorAt"]

    recovered = mark_automation_triggered(created["id"])
    assert recovered is not None
    assert recovered["lastError"] is None
    assert recovered["lastErrorAt"] is None


def test_mark_automation_triggered_returns_none_for_unknown_id(data_dir: Path) -> None:
    """id 不存在时返回 None，不抛异常。"""
    assert mark_automation_triggered("nope") is None


def test_trigger_marking_and_scanning_share_one_clock(data_dir: Path) -> None:
    """扫描与标记用同一个时刻时，同一分钟不会被重复判定为「该跑」。

    这两件事必须用同一个时钟 —— 一个用模拟时刻、另一个用真实时间的话，
    「同一分钟只触发一次」这条保证会静默失效。
    """
    created = create_automation({"name": "每日", "prompt": "做点事", "schedule": "0 9 * * 1-5"})
    monday_nine = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)

    assert len(due_automations(monday_nine).due) == 1
    mark_automation_triggered(created["id"], run_id="r-1", at=monday_nine)
    assert len(due_automations(monday_nine).due) == 0
    assert len(due_automations(monday_nine + timedelta(minutes=1)).due) == 0
    assert len(due_automations(monday_nine + timedelta(days=1)).due) == 1


def test_mark_automation_triggered_accepts_naive_time(data_dir: Path) -> None:
    """不带时区的时刻按 UTC 处理。"""
    created = create_automation({"name": "每日", "prompt": "做点事", "schedule": "0 9 * * 1-5"})
    updated = mark_automation_triggered(created["id"], at=datetime(2026, 9, 21, 9, 0))
    assert updated is not None
    assert updated["lastTriggeredAt"] == "2026-09-21T09:00:00+00:00"


def test_build_round_prompt_includes_the_task_and_round_number() -> None:
    """最基本的：任务指令 + 第几轮。"""
    text = build_round_prompt("整理 CI 失败", round_number=3)
    assert "整理 CI 失败" in text
    assert "第 3 轮" in text


def test_build_round_prompt_includes_state_and_feedback() -> None:
    """给了状态和反馈就都附上。"""
    text = build_round_prompt(
        "整理 CI 失败", round_number=2, state_text="上次处理到哪：abc123", feedback="登录页测试没过"
    )
    assert "abc123" in text
    assert "登录页测试没过" in text


def test_build_round_prompt_omits_empty_sections() -> None:
    """没有状态和反馈时不留下空标题。"""
    text = build_round_prompt("整理 CI 失败", round_number=1)
    assert "循环状态" not in text
    assert "验证没有通过" not in text


def test_run_cycle_skips_when_the_automation_has_no_id(data_dir: Path) -> None:
    """定义缺 id 时直接跳过，不调用跑一轮。"""
    runner, requests = make_round_runner()
    result = run_cycle({"prompt": "做点事"}, runner)
    assert result.ran is False
    assert result.skipped_reason == "自动化任务缺少 id"
    assert requests == []


def test_run_cycle_skips_when_already_stopped(data_dir: Path) -> None:
    """上一轮已经把循环跑停了，这一轮不该再启动。"""
    set_budget(AUTOMATION_ID, max_runs=1)
    record_run(AUTOMATION_ID, outcome="succeeded")

    runner, requests = make_round_runner()
    result = run_cycle(make_automation(), runner)
    assert result.ran is False
    assert result.stopped is True
    assert requests == []


def test_run_cycle_skips_when_checks_are_malformed(data_dir: Path) -> None:
    """检查项配错时跳过并说明原因，而不是带着半套检查去跑。"""
    runner, requests = make_round_runner()
    result = run_cycle(make_automation(checks=[{"command": "  "}]), runner)
    assert result.ran is False
    assert "检查项配置有误" in str(result.skipped_reason)
    assert requests == []


def test_run_cycle_records_the_round(data_dir: Path) -> None:
    """跑完一轮要把结果写进状态。"""
    runner, requests = make_round_runner(
        RoundResult(
            outcome="succeeded",
            summary="整理出 3 条待办",
            changed=("TODO.md",),
            next_steps=("跟进第 2 条",),
            tokens=5000,
            thread_id="t-1",
        )
    )
    result = run_cycle(make_automation(), runner)

    assert result.ran is True
    assert result.outcome == "succeeded"
    assert len(requests) == 1
    assert requests[0].round_number == 1
    assert requests[0].automation_id == AUTOMATION_ID

    state = load_state(AUTOMATION_ID)
    assert state["budget"]["runsUsed"] == 1
    assert state["budget"]["tokensUsed"] == 5000
    assert state["runs"][0]["summary"] == "整理出 3 条待办"
    assert state["runs"][0]["threadId"] == "t-1"
    assert state["runs"][0]["changed"] == ["TODO.md"]


def test_run_cycle_without_checks_has_no_verification(data_dir: Path) -> None:
    """没配检查项时不编验证结论 —— 状态里要看得出来「没做验证」。"""
    runner, _ = make_round_runner()
    result = run_cycle(make_automation(), runner)
    assert result.verification is None
    assert load_state(AUTOMATION_ID)["runs"][0]["verification"] is None


def test_run_cycle_hands_the_reported_directory_to_verification(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """验证要用「跑一轮」报上来的目录，不是后端进程自己的当前目录。

    退回到进程当前目录会在后端自己的仓库里跑 ``pytest``，得到一份与目标仓库
    无关的结论 —— 那比「没验证」更糟，因为它看起来像验过了。
    """
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    captured: dict[str, Any] = {}

    def fake_run_checks(
        checks: Any, *, backend_type: str, workspace: Any, runner: Any
    ) -> VerificationResult:
        captured.update(backend_type=backend_type, workspace=workspace)
        return VerificationResult(outcomes=())

    monkeypatch.setattr("services.automation_loop.run_checks", fake_run_checks)
    runner, _ = make_round_runner(RoundResult(workspace=str(repo_dir)))
    run_cycle(make_automation(checks=[PASSING_CHECK]), runner, backend_type="sandbox")

    assert captured["workspace"] == str(repo_dir)
    assert captured["backend_type"] == "sandbox"


def test_explicit_workspace_wins_over_the_reported_one(
    data_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """调用方显式指定工作目录时以它为准。"""
    reported = tmp_path / "reported"
    reported.mkdir()
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    captured: dict[str, Any] = {}

    def fake_run_checks(
        checks: Any, *, backend_type: str, workspace: Any, runner: Any
    ) -> VerificationResult:
        captured["workspace"] = workspace
        return VerificationResult(outcomes=())

    monkeypatch.setattr("services.automation_loop.run_checks", fake_run_checks)
    runner, _ = make_round_runner(RoundResult(workspace=str(reported)))
    run_cycle(
        make_automation(checks=[PASSING_CHECK]), runner, workspace=str(explicit)
    )

    assert captured["workspace"] == str(explicit)


def test_run_cycle_does_not_run_checks_without_a_workspace(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """说不清该在哪跑就**不跑**，并把这个事实写进反馈。"""
    called: list[Any] = []
    monkeypatch.setattr(
        "services.automation_loop.run_checks", lambda *a, **k: called.append(a)
    )
    runner, _ = make_round_runner()
    result = run_cycle(make_automation(checks=[PASSING_CHECK]), runner)

    assert called == []
    assert result.verification is not None
    assert result.verification["passed"] is False
    assert "没有执行验证" in load_state(AUTOMATION_ID)["lastFeedback"]


def test_skipped_checks_in_sandbox_say_why(data_dir: Path) -> None:
    """沙箱模式下的跳过原因要说清是环境限制，不能和「没目录」混成一句。"""
    runner, _ = make_round_runner()
    run_cycle(
        make_automation(checks=[PASSING_CHECK]), runner, backend_type="sandbox"
    )
    feedback = load_state(AUTOMATION_ID)["lastFeedback"]
    assert "沙箱" in feedback
    assert "容器内执行验证" in feedback


def test_injected_check_runner_still_runs_without_a_workspace(
    data_dir: Path,
) -> None:
    """执行器是调用方注入的时不拦 —— 那种执行器不依赖本机目录（例如走容器）。"""
    runner, _ = make_round_runner()
    result = run_cycle(
        make_automation(checks=[PASSING_CHECK]),
        runner,
        check_runner=make_check_runner(),
    )
    assert result.verification is not None
    assert result.verification["passed"] is True


def test_run_cycle_records_a_passing_verification(data_dir: Path) -> None:
    """配了检查项且全过时，验证结论要落盘。"""
    runner, _ = make_round_runner()
    result = run_cycle(
        make_automation(checks=[PASSING_CHECK]),
        runner,
        check_runner=make_check_runner(),
    )
    assert result.verification is not None
    assert result.verification["passed"] is True
    assert load_state(AUTOMATION_ID)["runs"][0]["verification"]["passed"] is True


def test_failing_verification_is_carried_into_the_next_round(data_dir: Path) -> None:
    """上一轮没过时，反馈要出现在下一轮发出去的指令里。"""
    runner, requests = make_round_runner()
    automation = make_automation(checks=[FAILING_CHECK])

    run_cycle(automation, runner, check_runner=make_check_runner(failing=(FAILING_CHECK,)))
    state = load_state(AUTOMATION_ID)
    assert state["lastFeedback"]
    assert FAILING_CHECK in state["lastFeedback"]

    run_cycle(automation, runner, check_runner=make_check_runner(failing=(FAILING_CHECK,)))
    assert "上一轮的验证没有通过" in requests[1].prompt
    assert FAILING_CHECK in requests[1].prompt


def test_passing_verification_clears_the_previous_feedback(data_dir: Path) -> None:
    """这一轮过了就把旧反馈清掉，别让下一轮还在修已经修好的东西。"""
    runner, _ = make_round_runner()
    automation = make_automation(checks=[FAILING_CHECK])

    run_cycle(automation, runner, check_runner=make_check_runner(failing=(FAILING_CHECK,)))
    assert load_state(AUTOMATION_ID)["lastFeedback"]

    run_cycle(automation, runner, check_runner=make_check_runner())
    assert load_state(AUTOMATION_ID)["lastFeedback"] == ""


def test_skipped_checks_count_as_not_passed(data_dir: Path) -> None:
    """需要审批的检查项算「没确认」，所以整体不通过、反馈里会说明没执行。"""
    runner, _ = make_round_runner()
    result = run_cycle(
        make_automation(checks=[NEEDS_APPROVAL_CHECK]), runner, check_runner=make_check_runner()
    )
    assert result.verification is not None
    assert result.verification["passed"] is False
    assert "人工审批" in load_state(AUTOMATION_ID)["lastFeedback"]


def test_run_cycle_stops_after_consecutive_failures(data_dir: Path) -> None:
    """连续未通过到上限时停下，并在结论里给出原因。"""
    set_budget(AUTOMATION_ID, max_consecutive_failures=2)
    runner, _ = make_round_runner()
    automation = make_automation(checks=[FAILING_CHECK])
    check_runner = make_check_runner(failing=(FAILING_CHECK,))

    first = run_cycle(automation, runner, check_runner=check_runner)
    assert first.stopped is False

    second = run_cycle(automation, runner, check_runner=check_runner)
    assert second.stopped is True
    assert "人工介入" in str(second.stop_reason)

    third = run_cycle(automation, runner, check_runner=check_runner)
    assert third.ran is False
    assert third.stopped is True


def test_run_cycle_prompt_carries_the_state_text(data_dir: Path) -> None:
    """第一轮之后的指令里要带上循环状态。"""
    runner, requests = make_round_runner()
    set_cursor(AUTOMATION_ID, {"lastCommit": "9f3c1ab"})
    run_cycle(make_automation(), runner)
    assert "9f3c1ab" in requests[0].prompt


def test_cycle_result_is_json_friendly() -> None:
    """结论要能直接返回给前端。"""
    assert CycleResult(automation_id="a", ran=False).as_dict() == {
        "automationId": "a",
        "ran": False,
        "outcome": None,
        "summary": "",
        "verification": None,
        "stopped": False,
        "stopReason": None,
        "skippedReason": None,
    }


def test_round_request_defaults_match_the_definition_shape(data_dir: Path) -> None:
    """定义里的 model / effort 要原样带进请求。"""
    runner, requests = make_round_runner()
    run_cycle(make_automation(model="claude", effort="high"), runner)
    assert requests[0].model_id == "claude"
    assert requests[0].effort == "high"


def test_due_scan_can_be_replayed_for_a_specific_moment(data_dir: Path) -> None:
    """扫描接受指定时刻，方便补跑和排查。"""
    create_automation({"name": "每日", "prompt": "做点事", "schedule": "0 9 * * 1-5"})
    monday = datetime(2026, 9, 21, 9, 0, tzinfo=UTC)
    assert len(due_automations(monday).due) == 1
    assert len(due_automations(monday - timedelta(minutes=1)).due) == 0
