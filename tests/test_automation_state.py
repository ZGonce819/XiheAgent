"""自动化任务循环状态的测试。

覆盖三块：

1. 读写与容错——文件不存在、内容损坏、字段类型不对时都不能让循环卡死；
2. 记账语义——轮数 / token 累加、历史上限、游标合并；
3. 停止条件——显式停止、轮数上限、token 上限三个来源要各自生效。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from config.automation_state import (
    MAX_ATTEMPTS_KEPT,
    MAX_CHECKS_KEPT,
    MAX_RUNS_KEPT,
    MAX_SUMMARY_CHARS,
    clear_stopped,
    clear_waiting_approval,
    delete_state,
    failure_streak,
    initial_state,
    load_state,
    mark_stopped,
    record_attempt,
    record_run,
    render_state,
    reset_state,
    save_state,
    set_budget,
    set_cursor,
    state_dir,
    state_path,
    stop_decision,
    stop_status,
)

AUTOMATION_ID = "ab12cd34"


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 ``Settings.data_dir`` 指向临时目录，避免写到开发机的真实数据目录。

    Returns:
        临时的 data 目录。
    """
    from config.app_config import get_settings

    target = tmp_path / "data"
    monkeypatch.setattr(get_settings(), "data_dir", target)
    return target


def test_state_path_lives_under_data_dir(data_dir: Path) -> None:
    """状态文件落在 data_dir 下的独立子目录里，与定义文件分开。"""
    assert state_dir() == data_dir / "automation_state"
    assert state_path(AUTOMATION_ID) == data_dir / "automation_state" / f"{AUTOMATION_ID}.json"


def test_missing_file_returns_initial_state(data_dir: Path) -> None:
    """文件不存在时返回初始状态，而不是抛异常。"""
    state = load_state(AUTOMATION_ID)
    assert state["automationId"] == AUTOMATION_ID
    assert state["runs"] == []
    assert state["attempts"] == []
    assert state["budget"]["runsUsed"] == 0
    assert state["stop"]["reached"] is False


def test_corrupt_file_returns_initial_state(data_dir: Path) -> None:
    """磁盘上是一段坏 JSON 时也要能继续跑，不能把循环卡死。"""
    path = state_path(AUTOMATION_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ 这不是 json", encoding="utf-8")
    assert load_state(AUTOMATION_ID)["runs"] == []


def test_wrong_field_types_are_folded_back(data_dir: Path) -> None:
    """字段类型不对时逐项退回默认值，其余字段仍然读得出来。"""
    path = state_path(AUTOMATION_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "cursor": "不是字典",
                "budget": {"runsUsed": "三次", "maxRuns": 5},
                "stop": "不是字典",
                "runs": ["不是字典", {"outcome": "succeeded"}],
            }
        ),
        encoding="utf-8",
    )
    state = load_state(AUTOMATION_ID)
    assert state["cursor"] == {}
    assert state["budget"]["runsUsed"] == 0
    assert state["budget"]["maxRuns"] == 5
    assert state["stop"]["reached"] is False
    assert state["runs"] == [{"outcome": "succeeded"}]


def test_record_run_round_trips(data_dir: Path) -> None:
    """一轮运行的结果要能完整读回来。"""
    record_run(
        AUTOMATION_ID,
        outcome="succeeded",
        summary="修好了登录页的布局",
        run_id="run-1",
        thread_id="thread-1",
        changed=["src/login.tsx"],
        next_steps=["补一个移动端的回归用例"],
    )
    run = load_state(AUTOMATION_ID)["runs"][0]
    assert run["runId"] == "run-1"
    assert run["threadId"] == "thread-1"
    assert run["outcome"] == "succeeded"
    assert run["changed"] == ["src/login.tsx"]
    assert run["nextSteps"] == ["补一个移动端的回归用例"]
    assert run["finishedAt"]


def test_newest_run_comes_first(data_dir: Path) -> None:
    """历史按新到旧排列，看状态时不用再翻。"""
    record_run(AUTOMATION_ID, outcome="failed", summary="第一轮")
    record_run(AUTOMATION_ID, outcome="succeeded", summary="第二轮")
    runs = load_state(AUTOMATION_ID)["runs"]
    assert [run["summary"] for run in runs] == ["第二轮", "第一轮"]


def test_runs_are_capped_and_drop_oldest(data_dir: Path) -> None:
    """超过上限后丢最旧的，保留最近的 N 轮。"""
    for index in range(MAX_RUNS_KEPT + 5):
        record_run(AUTOMATION_ID, outcome="succeeded", summary=f"第 {index} 轮")
    runs = load_state(AUTOMATION_ID)["runs"]
    assert len(runs) == MAX_RUNS_KEPT
    assert runs[0]["summary"] == f"第 {MAX_RUNS_KEPT + 4} 轮"
    assert all(run["summary"] != "第 0 轮" for run in runs)


def test_attempts_are_capped_and_keep_newest(data_dir: Path) -> None:
    """「试过没成功」的记录同样有上限，且保留最近的。"""
    for index in range(MAX_ATTEMPTS_KEPT + 3):
        record_attempt(AUTOMATION_ID, tried=f"做法 {index}", result="无效")
    attempts = load_state(AUTOMATION_ID)["attempts"]
    assert len(attempts) == MAX_ATTEMPTS_KEPT
    assert attempts[0]["tried"] == f"做法 {MAX_ATTEMPTS_KEPT + 2}"


def test_record_run_counts_usage(data_dir: Path) -> None:
    """每记一轮就把轮数加一，token 累加。"""
    record_run(AUTOMATION_ID, outcome="succeeded", tokens=1200)
    record_run(AUTOMATION_ID, outcome="failed", tokens=800)
    budget = load_state(AUTOMATION_ID)["budget"]
    assert budget["runsUsed"] == 2
    assert budget["tokensUsed"] == 2000


def test_record_run_rejects_unknown_outcome(data_dir: Path) -> None:
    """结果取值必须是约定的三种之一，写错就报错而不是静默存下。"""
    with pytest.raises(ValueError, match="未知的运行结果"):
        record_run(AUTOMATION_ID, outcome="差不多完成了")


def test_long_summary_is_clipped(data_dir: Path) -> None:
    """单条摘要超长要截断，避免状态文件无上限膨胀。"""
    record_run(AUTOMATION_ID, outcome="succeeded", summary="很" * (MAX_SUMMARY_CHARS + 50))
    summary = load_state(AUTOMATION_ID)["runs"][0]["summary"]
    assert len(summary) == MAX_SUMMARY_CHARS + 3
    assert summary.endswith("...")


def test_set_cursor_merges_keys(data_dir: Path) -> None:
    """游标只覆盖传进来的键，之前记下的键不能被抹掉。"""
    set_cursor(AUTOMATION_ID, {"lastCommit": "abc123"})
    set_cursor(AUTOMATION_ID, {"lastRunAt": "2026-09-18T00:00:00+00:00"})
    cursor = load_state(AUTOMATION_ID)["cursor"]
    assert cursor == {
        "lastCommit": "abc123",
        "lastRunAt": "2026-09-18T00:00:00+00:00",
    }


def test_stop_status_is_false_by_default(data_dir: Path) -> None:
    """没设任何限制时循环可以继续。"""
    assert stop_status(AUTOMATION_ID) == (False, None)


def test_mark_stopped_and_clear(data_dir: Path) -> None:
    """显式停止能生效，也能被清掉。"""
    mark_stopped(AUTOMATION_ID, "没有待处理的问题了")
    stopped, reason = stop_status(AUTOMATION_ID)
    assert stopped is True
    assert reason == "没有待处理的问题了"

    clear_stopped(AUTOMATION_ID)
    assert stop_status(AUTOMATION_ID) == (False, None)


def test_mark_stopped_rejects_empty_reason(data_dir: Path) -> None:
    """停止原因会展示给人看，不能是空的。"""
    with pytest.raises(ValueError, match="停止原因不能为空"):
        mark_stopped(AUTOMATION_ID, "   ")


def test_max_runs_stops_the_loop(data_dir: Path) -> None:
    """轮数上限到了就该停，且原因里带上具体数字。"""
    set_budget(AUTOMATION_ID, max_runs=2)
    record_run(AUTOMATION_ID, outcome="succeeded")
    assert stop_status(AUTOMATION_ID)[0] is False

    record_run(AUTOMATION_ID, outcome="succeeded")
    stopped, reason = stop_status(AUTOMATION_ID)
    assert stopped is True
    assert "2" in str(reason)


def test_max_tokens_stops_the_loop(data_dir: Path) -> None:
    """token 上限到了同样要停。"""
    set_budget(AUTOMATION_ID, max_tokens=1000)
    record_run(AUTOMATION_ID, outcome="succeeded", tokens=400)
    assert stop_status(AUTOMATION_ID)[0] is False

    record_run(AUTOMATION_ID, outcome="succeeded", tokens=700)
    stopped, reason = stop_status(AUTOMATION_ID)
    assert stopped is True
    assert "1000" in str(reason)


def test_zero_budget_means_unlimited(data_dir: Path) -> None:
    """上限为 0 表示不限制，不能把它当成「跑满 0 轮」。"""
    set_budget(AUTOMATION_ID, max_runs=0, max_tokens=0)
    for _ in range(5):
        record_run(AUTOMATION_ID, outcome="succeeded", tokens=10_000)
    assert stop_status(AUTOMATION_ID) == (False, None)


def test_set_budget_rejects_negative(data_dir: Path) -> None:
    """负的上限没有意义，直接报错。"""
    with pytest.raises(ValueError, match="不能为负数"):
        set_budget(AUTOMATION_ID, max_runs=-1)
    with pytest.raises(ValueError, match="不能为负数"):
        set_budget(AUTOMATION_ID, max_tokens=-1)


def test_reset_state_clears_history_but_keeps_file(data_dir: Path) -> None:
    """重置后历史清空，文件仍在。"""
    record_run(AUTOMATION_ID, outcome="succeeded", summary="跑过一轮")
    set_cursor(AUTOMATION_ID, {"lastCommit": "abc123"})

    reset_state(AUTOMATION_ID)
    state = load_state(AUTOMATION_ID)
    assert state["runs"] == []
    assert state["cursor"] == {}
    assert state["budget"]["runsUsed"] == 0
    assert state_path(AUTOMATION_ID).exists()


def test_delete_state_reports_whether_anything_was_removed(data_dir: Path) -> None:
    """删一个不存在的状态返回 False，删存在的返回 True。"""
    assert delete_state(AUTOMATION_ID) is False
    record_run(AUTOMATION_ID, outcome="succeeded")
    assert delete_state(AUTOMATION_ID) is True
    assert state_path(AUTOMATION_ID).exists() is False


def test_two_automations_do_not_share_state(data_dir: Path) -> None:
    """不同自动化各存各的，不会互相覆盖。"""
    record_run("aaaa1111", outcome="succeeded", summary="甲")
    record_run("bbbb2222", outcome="failed", summary="乙")
    assert load_state("aaaa1111")["runs"][0]["summary"] == "甲"
    assert load_state("bbbb2222")["runs"][0]["summary"] == "乙"


def test_illegal_ids_are_rejected(data_dir: Path) -> None:
    """id 会被拼进文件名，必须挡掉路径字符。"""
    for bad in ("", "   ", "../escape", "a/b", "a\\b", "带中文", "x" * 65):
        with pytest.raises(ValueError, match="id 非法"):
            state_path(bad)


def test_save_state_leaves_no_temp_file(data_dir: Path) -> None:
    """原子写用的临时文件不能留在磁盘上。"""
    save_state(AUTOMATION_ID, initial_state(AUTOMATION_ID))
    leftovers = list(state_dir().glob("*.tmp"))
    assert leftovers == []


def test_render_state_shows_runs_attempts_and_next_steps(data_dir: Path) -> None:
    """给人看的文本里要有最近几轮、待办和试过没成功的做法。"""
    record_run(
        AUTOMATION_ID,
        outcome="blocked",
        summary="登录页修好了，回归用例还没补",
        next_steps=["补移动端回归用例"],
    )
    record_attempt(AUTOMATION_ID, tried="加大超时时间", result="无效", reason="不是超时问题")
    set_cursor(AUTOMATION_ID, {"lastCommit": "abc123"})

    text = render_state(AUTOMATION_ID)
    assert "登录页修好了" in text
    assert "补移动端回归用例" in text
    assert "加大超时时间" in text
    assert "不是超时问题" in text
    assert "abc123" in text
    assert "可继续" in text


def test_render_state_mentions_stop_reason(data_dir: Path) -> None:
    """停下时文本里要写清为什么停。"""
    mark_stopped(AUTOMATION_ID, "没有待处理的问题了")
    assert "没有待处理的问题了" in render_state(AUTOMATION_ID)


def test_render_state_works_without_history(data_dir: Path) -> None:
    """一次都没跑过时也要给出一份可读的骨架，而不是空串。"""
    text = render_state(AUTOMATION_ID)
    assert "还没有任何运行记录" in text
    assert AUTOMATION_ID in text


def test_render_state_does_not_write_anything(data_dir: Path) -> None:
    """渲染是只读操作，不能顺手把状态文件建出来。"""
    render_state(AUTOMATION_ID)
    assert state_path(AUTOMATION_ID).exists() is False


def verification(
    passed: bool,
    *,
    summary: str = "",
    checks: list[dict[str, Any]] | None = None,
    executed: bool | None = None,
) -> dict[str, Any]:
    """造一份验证结论。

    不传 ``checks`` 时默认造一条**真的跑过**的检查 —— 这是常见情形，也让
    「有验证结论就以它为准」这类用例测的是本该测的东西。要造「一条都没跑成」
    的结论，把 ``checks`` 传成 ``status="skipped"`` 的那一种，或者显式传 ``executed``。

    Args:
        passed: 整体是否通过。
        summary: 一句话结论；不传按 ``passed`` 编一句。
        checks: 每一条检查的结果；不传则按 ``passed`` 造一条跑过的检查。
        executed: 有没有检查真的跑过；不传则交给 ``_normalize_verification``
            按 ``checks`` 的状态推。

    Returns:
        与 ``VerificationResult.as_state_dict()`` 同构的字典。
    """
    if checks is None:
        checks = [
            {
                "command": "pytest tests/",
                "description": "跑测试",
                "status": "passed" if passed else "failed",
                "exitCode": 0 if passed else 1,
            }
        ]
    payload: dict[str, Any] = {
        "passed": passed,
        "summary": summary or ("检查通过" if passed else "检查未通过"),
        "checks": checks,
    }
    if executed is not None:
        payload["executed"] = executed
    return payload


def test_run_keeps_the_verification_conclusion(data_dir: Path) -> None:
    """验证结论要跟着这一轮一起落盘。"""
    record_run(
        AUTOMATION_ID,
        outcome="succeeded",
        verification=verification(
            True,
            checks=[
                {
                    "command": "pytest tests/",
                    "description": "跑测试",
                    "status": "passed",
                    "exitCode": 0,
                }
            ],
        ),
    )
    stored = load_state(AUTOMATION_ID)["runs"][0]["verification"]
    assert stored["passed"] is True
    assert stored["summary"] == "检查通过"
    assert stored["checks"][0]["exitCode"] == 0


def test_run_without_verification_keeps_it_empty(data_dir: Path) -> None:
    """没做验证时要看得出来是「没做」，不能编一个结论出来。"""
    record_run(AUTOMATION_ID, outcome="succeeded")
    assert load_state(AUTOMATION_ID)["runs"][0]["verification"] is None


def test_verification_without_passed_flag_is_dropped(data_dir: Path) -> None:
    """形状不对的验证结论一律丢掉，避免被当成「已确认」。"""
    record_run(AUTOMATION_ID, outcome="succeeded", verification={"summary": "看着还行"})
    assert load_state(AUTOMATION_ID)["runs"][0]["verification"] is None


def test_verification_checks_are_capped(data_dir: Path) -> None:
    """检查项很多时只留前若干条，防止状态文件膨胀。"""
    record_run(
        AUTOMATION_ID,
        outcome="failed",
        verification=verification(
            False,
            checks=[
                {"command": f"check-{index}", "status": "failed", "exitCode": 1}
                for index in range(MAX_CHECKS_KEPT + 5)
            ],
        ),
    )
    stored = load_state(AUTOMATION_ID)["runs"][0]["verification"]
    assert len(stored["checks"]) == MAX_CHECKS_KEPT


def test_failure_streak_counts_from_the_latest_round(data_dir: Path) -> None:
    """连续未通过要从最近一轮往前数。"""
    record_run(AUTOMATION_ID, outcome="failed")
    record_run(AUTOMATION_ID, outcome="failed")
    record_run(AUTOMATION_ID, outcome="failed")
    assert failure_streak(AUTOMATION_ID) == 3


def test_failure_streak_stops_at_the_first_pass(data_dir: Path) -> None:
    """中间一旦有一轮通过，连续计数就断掉。"""
    record_run(AUTOMATION_ID, outcome="failed")
    record_run(AUTOMATION_ID, outcome="succeeded")
    record_run(AUTOMATION_ID, outcome="failed")
    assert failure_streak(AUTOMATION_ID) == 1


def test_failure_streak_is_zero_without_history(data_dir: Path) -> None:
    """一次都没跑过时不算连续失败。"""
    assert failure_streak(AUTOMATION_ID) == 0


def test_verification_verdict_beats_the_outcome(data_dir: Path) -> None:
    """有验证结论时以它为准：outcome 写 succeeded 但验证没过，仍算未通过。"""
    record_run(
        AUTOMATION_ID, outcome="succeeded", verification=verification(False)
    )
    assert failure_streak(AUTOMATION_ID) == 1


def test_blocked_round_does_not_count_as_a_failure(data_dir: Path) -> None:
    """``blocked`` 是「没做完但也没报错」，不算验证失败。"""
    record_run(AUTOMATION_ID, outcome="blocked")
    assert failure_streak(AUTOMATION_ID) == 0


def skipped_check(command: str = "pytest tests/") -> list[dict[str, Any]]:
    """造一条「没跑成」的检查结果。

    Returns:
        与状态文件里同构的检查结果列表。
    """
    return [{"command": command, "description": "跑测试", "status": "skipped", "exitCode": None}]


def test_a_round_that_could_not_be_verified_is_not_a_failure(data_dir: Path) -> None:
    """一条检查都没跑成的轮次不算未通过。

    「没能验证」说的是环境不通（默认配置下仓库在沙箱容器里就是这种），不是活儿
    没干好。算成未通过的话，``maxConsecutiveFailures`` 会数着环境问题把任务停掉，
    理由还写着「未通过验证」，把人指去改代码。
    """
    record_run(
        AUTOMATION_ID,
        outcome="succeeded",
        verification=verification(
            False,
            summary="1 项检查没有执行，无法确认结果",
            checks=skipped_check(),
        ),
    )
    assert failure_streak(AUTOMATION_ID) == 0


def test_unverified_rounds_never_reach_the_failure_limit(data_dir: Path) -> None:
    """连着多少轮「没验成」都不会触发「连续未通过就停」。"""
    set_budget(AUTOMATION_ID, max_consecutive_failures=1)
    for _ in range(3):
        record_run(
            AUTOMATION_ID,
            outcome="succeeded",
            verification=verification(False, checks=skipped_check()),
        )
    assert stop_status(AUTOMATION_ID) == (False, None)


def test_a_state_file_without_the_executed_key_still_judges_correctly(
    data_dir: Path,
) -> None:
    """加 ``executed`` 之前写下的状态文件也要判对：从每条的结论推得出来。"""
    state = load_state(AUTOMATION_ID)
    state["runs"] = [
        {
            "outcome": "succeeded",
            "verification": {
                "passed": False,
                "summary": "1 项检查没有执行，无法确认结果",
                "checks": skipped_check(),
            },
        }
    ]
    save_state(AUTOMATION_ID, state)
    assert failure_streak(AUTOMATION_ID) == 0


def test_a_verdict_from_a_check_that_ran_is_still_a_failure(data_dir: Path) -> None:
    """真跑过且没过的结论仍然算未通过 —— 别把这条一起放走了。"""
    record_run(
        AUTOMATION_ID,
        outcome="succeeded",
        verification=verification(
            False,
            checks=[
                {
                    "command": "pytest tests/",
                    "description": "跑测试",
                    "status": "failed",
                    "exitCode": 1,
                }
            ],
        ),
    )
    assert failure_streak(AUTOMATION_ID) == 1


def test_max_consecutive_failures_stops_the_loop(data_dir: Path) -> None:
    """连续失败到上限就停下交给人，并把原因写清楚。"""
    set_budget(AUTOMATION_ID, max_consecutive_failures=2)
    record_run(AUTOMATION_ID, outcome="failed")
    assert stop_status(AUTOMATION_ID)[0] is False

    record_run(AUTOMATION_ID, outcome="failed")
    stopped, reason = stop_status(AUTOMATION_ID)
    assert stopped is True
    assert "人工介入" in str(reason)


def test_a_pass_resets_the_consecutive_failure_stop(data_dir: Path) -> None:
    """中间成功一轮之后，连续失败的停止条件就不该再拦着。"""
    set_budget(AUTOMATION_ID, max_consecutive_failures=2)
    record_run(AUTOMATION_ID, outcome="failed")
    record_run(AUTOMATION_ID, outcome="succeeded")
    record_run(AUTOMATION_ID, outcome="failed")
    assert stop_status(AUTOMATION_ID) == (False, None)


def test_set_budget_rejects_negative_consecutive_failures(data_dir: Path) -> None:
    """负的连续失败上限没有意义，直接报错。"""
    with pytest.raises(ValueError, match="不能为负数"):
        set_budget(AUTOMATION_ID, max_consecutive_failures=-1)


def test_render_state_shows_verification_and_streak(data_dir: Path) -> None:
    """给人看的文本里要有每轮的验证结论和当前的连续失败次数。"""
    record_run(
        AUTOMATION_ID,
        outcome="failed",
        summary="测试还是没过",
        verification=verification(False, summary="1 项未通过（共 2 项）"),
    )
    text = render_state(AUTOMATION_ID)
    assert "验证未通过：1 项未通过（共 2 项）" in text
    assert "已连续 1 轮未通过验证" in text


def test_render_state_marks_a_verification_that_never_ran(data_dir: Path) -> None:
    """没跑成要单独标一个说法，不能和「跑过但没过」混成一句。"""
    record_run(
        AUTOMATION_ID,
        outcome="succeeded",
        verification=verification(
            False,
            summary="1 项检查没有执行，无法确认结果：说不清仓库在哪",
            checks=skipped_check(),
        ),
    )
    text = render_state(AUTOMATION_ID)
    assert "验证未执行：1 项检查没有执行，无法确认结果：说不清仓库在哪" in text
    # 没验成当然不算通过，但也不能混进「连续未通过」。
    assert "已连续" not in text


def test_render_state_marks_a_passing_verification(data_dir: Path) -> None:
    """通过的那一轮也要标出来，不然看不出「跑过了」。"""
    record_run(AUTOMATION_ID, outcome="succeeded", verification=verification(True))
    text = render_state(AUTOMATION_ID)
    assert "验证通过：检查通过" in text
    assert "已连续" not in text


def test_render_state_lists_the_failure_limit(data_dir: Path) -> None:
    """预算里配了连续失败上限时要在文本里体现。"""
    set_budget(AUTOMATION_ID, max_consecutive_failures=3)
    assert "连续 3 轮未通过就停" in render_state(AUTOMATION_ID)


def test_stop_decision_names_the_source(data_dir: Path) -> None:
    """停止来源要能分清 —— 五个来源的恢复方式不一样。

    显式标记和「停在等人工确认」能直接清掉；轮数、token、连续未通过这三条得改预算
    上限才有效。分不清来源，界面就只能给一个点了没反应的「继续运行」。
    """
    assert stop_decision(AUTOMATION_ID) == (None, None)

    mark_stopped(AUTOMATION_ID, "手动停的")
    assert stop_decision(AUTOMATION_ID) == ("marked", "手动停的")
    # 两个函数对「停没停」的判断必须一致，不能各算各的。
    assert stop_status(AUTOMATION_ID) == (True, "手动停的")

    clear_stopped(AUTOMATION_ID)
    set_budget(AUTOMATION_ID, max_runs=1)
    record_run(AUTOMATION_ID, outcome="succeeded")
    source, reason = stop_decision(AUTOMATION_ID)
    assert source == "max_runs"
    assert "最多运行轮数" in str(reason)


def test_stop_decision_marks_a_failure_stop(data_dir: Path) -> None:
    """连续未通过导致的停止，来源要认成 ``failures`` 而不是预算。"""
    set_budget(AUTOMATION_ID, max_consecutive_failures=1)
    record_run(AUTOMATION_ID, outcome="failed")
    source, reason = stop_decision(AUTOMATION_ID)
    assert source == "failures"
    assert "人工介入" in str(reason)


def test_stop_decision_reports_waiting_approval(data_dir: Path) -> None:
    """停在等人工确认要单独认出来。

    它和「状态还没落定、下一轮接着看」都落在 ``blocked`` 里，但这一条需要有人动手，
    循环必须停下来 —— 无人值守时没人会去点那个确认，下一轮只会卡在同一个地方。
    """
    record_run(
        AUTOMATION_ID,
        outcome="blocked",
        summary="这一轮停在等待人工审批，需要人工处理：git push origin main",
        waiting_approval=True,
    )
    source, reason = stop_decision(AUTOMATION_ID)
    assert source == "awaiting_approval"
    assert "git push origin main" in str(reason)


def test_stop_decision_ignores_a_plain_blocked_round(data_dir: Path) -> None:
    """没在等确认的 ``blocked`` 不该让循环停下 —— 它本来就该下一轮接着做。"""
    record_run(AUTOMATION_ID, outcome="blocked", summary="这一轮还没跑完")
    assert stop_decision(AUTOMATION_ID) == (None, None)


def test_waiting_approval_outranks_the_budget(data_dir: Path) -> None:
    """同时撞上限时先报等确认。

    报「额度用完了」会把人引去调上限，调完还是卡在同一个地方。
    """
    set_budget(AUTOMATION_ID, max_runs=1)
    record_run(AUTOMATION_ID, outcome="blocked", waiting_approval=True)
    source, _ = stop_decision(AUTOMATION_ID)
    assert source == "awaiting_approval"


def test_clear_waiting_approval_lets_the_loop_run_again(data_dir: Path) -> None:
    """处理完审批之后清掉标记，循环要能重新跑起来。"""
    record_run(AUTOMATION_ID, outcome="blocked", waiting_approval=True)
    assert stop_decision(AUTOMATION_ID)[0] == "awaiting_approval"

    clear_waiting_approval(AUTOMATION_ID)
    assert stop_decision(AUTOMATION_ID) == (None, None)


def test_record_run_defaults_to_not_waiting(data_dir: Path) -> None:
    """没显式说在等确认的轮次不能被误标 —— 否则循环会凭空停下来。"""
    record_run(AUTOMATION_ID, outcome="succeeded")
    assert load_state(AUTOMATION_ID)["runs"][0]["waitingApproval"] is False
    assert stop_decision(AUTOMATION_ID) == (None, None)


APPROVAL_COMMAND = 'git commit -m "自动提交"'
"""造出来的待审批命令。"""


def vote(
    decision: str,
    *,
    reason: str = "2/3 票同意放行",
    commands: list[str] | None = None,
    approve: int = 2,
    reject: int = 1,
) -> dict[str, Any]:
    """造一条表决记录。

    Args:
        decision: 表决结论。
        reason: 给人看的结论说明。
        commands: 这次表决涉及的命令。
        approve: 同意票数。
        reject: 反对票数。

    Returns:
        与 ``services.approval_vote.VoteOutcome.as_state_dict()`` 同构的字典。
    """
    return {
        "commands": [APPROVAL_COMMAND] if commands is None else commands,
        "approve": approve,
        "reject": reject,
        "decision": decision,
        "reason": reason,
        "votes": [
            {
                "role": "irreversible",
                "label": "不可逆损失",
                "approve": True,
                "reason": "只是本地提交",
                "error": "",
            },
            {
                "role": "safer_alternative",
                "label": "更安全的等价做法",
                "approve": False,
                "reason": "可以先只暂存再提交",
                "error": "",
            },
        ],
    }


def test_a_vote_is_recorded_with_every_reason(data_dir: Path) -> None:
    """表决记录要连理由一起留下 —— 它决定的是「要不要执行一条命令」，事后必须能追。"""
    record_run(
        AUTOMATION_ID,
        outcome="succeeded",
        approval_votes=[vote("approve")],
    )

    stored = load_state(AUTOMATION_ID)["runs"][0]["approvalVotes"]
    assert len(stored) == 1
    assert stored[0]["decision"] == "approve"
    assert stored[0]["approve"] == 2
    assert stored[0]["reject"] == 1
    assert [item["label"] for item in stored[0]["votes"]] == [
        "不可逆损失",
        "更安全的等价做法",
    ]
    assert stored[0]["votes"][1]["reason"] == "可以先只暂存再提交"


def test_rendered_state_shows_the_vote_and_the_command(data_dir: Path) -> None:
    """给人看的文本里要有这次表决的结论、说明与放行的命令。"""
    record_run(
        AUTOMATION_ID,
        outcome="succeeded",
        approval_votes=[vote("approve")],
    )

    text = render_state(AUTOMATION_ID)
    assert "审批表决放行" in text
    assert "2/3 票同意放行" in text
    assert APPROVAL_COMMAND in text


def test_a_vote_that_could_not_decide_is_not_rendered_as_a_rejection(
    data_dir: Path,
) -> None:
    """没能得出结论的表决不能渲染成「否决」。

    写成「否决」会让人以为有人看过并且否掉了这条命令，实际该看的是「为什么表决
    没能得出结论」—— 和「没跑成验证」不能写成「验证没通过」是同一条道理。
    """
    record_run(
        AUTOMATION_ID,
        outcome="blocked",
        approval_votes=[
            vote("undecided", reason="有评审没能给出结论，交给人工：任务意图：超时"),
            vote("refused", reason="按当前策略属于硬拒绝，不能表决放行"),
        ],
    )

    text = render_state(AUTOMATION_ID)
    assert "审批表决未定" in text
    assert "审批表决未进行" in text
    assert "审批表决否决" not in text


def test_unrecognized_vote_records_are_dropped(data_dir: Path) -> None:
    """认不出来的记录整条丢掉，别留一条字段残缺的。

    表决记录是用来追责的，一条看不出谁投了什么的记录比没有更糟。
    """
    record_run(
        AUTOMATION_ID,
        outcome="succeeded",
        approval_votes=[
            "不是字典",
            {"decision": "maybe"},
            {"commands": [APPROVAL_COMMAND]},
            vote("reject"),
        ],
    )

    stored = load_state(AUTOMATION_ID)["runs"][0]["approvalVotes"]
    assert len(stored) == 1
    assert stored[0]["decision"] == "reject"


def test_votes_are_capped_per_round(data_dir: Path) -> None:
    """一轮里保留的表决条数有上限，状态文件不能无边界膨胀。"""
    from config.automation_state import MAX_APPROVAL_VOTES_KEPT

    record_run(
        AUTOMATION_ID,
        outcome="succeeded",
        approval_votes=[vote("approve") for _ in range(MAX_APPROVAL_VOTES_KEPT + 3)],
    )

    stored = load_state(AUTOMATION_ID)["runs"][0]["approvalVotes"]
    assert len(stored) == MAX_APPROVAL_VOTES_KEPT


def test_a_vote_with_a_broken_shape_does_not_break_anything(data_dir: Path) -> None:
    """表压根不是列表、或者是 ``None`` 时按「没有表决」处理。"""
    for broken in (None, "坏了", 3, {"decision": "approve"}):
        record_run(AUTOMATION_ID, outcome="succeeded", approval_votes=broken)
        assert load_state(AUTOMATION_ID)["runs"][0]["approvalVotes"] == []


def test_an_old_state_file_without_votes_still_reads_and_renders(
    data_dir: Path,
) -> None:
    """老状态文件里没有 ``approvalVotes``，读回与渲染都要照常工作。

    ``runs`` 在读侧是**原样透传**的（不经过归一化），所以这里不能假设字段存在。
    缺了就是「那一轮没有表决」，本来就是事实，不需要像 ``executed`` 那样推。
    """
    record_run(AUTOMATION_ID, outcome="succeeded", summary="旧的一轮")
    path = state_path(AUTOMATION_ID)
    raw = json.loads(path.read_text(encoding="utf-8"))
    for run in raw["runs"]:
        run.pop("approvalVotes", None)
    path.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")

    state = load_state(AUTOMATION_ID)
    assert "approvalVotes" not in state["runs"][0]
    assert "旧的一轮" in render_state(AUTOMATION_ID)
