"""把循环接到真实 Agent 运行时上的测试。

这里能测的是**判断**，不是执行 —— 真正跑一轮需要模型和数据库。所以覆盖的是：

1. :func:`services.automation_runner.loop_scope_refusal` 的放行与拒绝边界；
2. 运行状态到循环结果的映射（未知状态一律算 ``blocked``，不能算成功）；
3. 「跑一轮」在超出范围、没配仓库时**在碰运行时之前就返回**；
4. token 用量的累加（拿不到时按 0 记，不影响这一轮的成败判定）。

Note:
    任务类型判定要调模型（实测一次约 5 秒，且结果随模型变）。所以这里**一律把判定
    打桩**，测的是「拿到某个类型之后怎么处置」，不是「某句话会被判成什么类型」。
    后者是 :mod:`utils.intent_utils` 自己的事。
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from services.automation_loop import RoundRequest
from services.automation_runner import (
    READ_ONLY_KINDS,
    REFUSED_KINDS,
    WRITE_KINDS,
    _latest_run_id,
    _outcome_from_status,
    _tokens_used,
    loop_scope_refusal,
    make_agent_round_runner,
    refusal_for_kind,
    resolve_backend_type,
    resolve_container_exec,
    resolve_workspace,
)

ALL_KINDS = ("coding", "planning", "analysis", "qa", "sync", "inspect", "review")


def _stub_kind(monkeypatch: pytest.MonkeyPatch, kind: str) -> list[str]:
    """把任务类型判定换成固定结果，返回记录调用的列表。"""
    calls: list[str] = []

    def fake(instruction: str) -> str:
        calls.append(instruction)
        return kind

    monkeypatch.setattr("utils.intent_utils.classify_task_kind", fake)
    return calls


@pytest.mark.parametrize("kind", sorted(READ_ONLY_KINDS))
def test_read_only_kinds_are_allowed(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    """只读类任务放行，配不配检查项都一样。"""
    _stub_kind(monkeypatch, kind)
    assert loop_scope_refusal("随便一句指令", has_checks=False) is None


@pytest.mark.parametrize("kind", sorted(WRITE_KINDS))
def test_write_kinds_are_allowed_when_checks_are_configured(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """配了验证检查项的写入任务放行 —— 检查项是它唯一的独立判断依据。"""
    _stub_kind(monkeypatch, kind)
    assert loop_scope_refusal("随便一句指令", has_checks=True) is None


@pytest.mark.parametrize("kind", sorted(WRITE_KINDS))
def test_write_kinds_need_checks(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    """没配检查项的写入任务拒掉，理由要说清「为什么非配不可」。"""
    _stub_kind(monkeypatch, kind)
    refusal = loop_scope_refusal("随便一句指令", has_checks=False)
    assert refusal is not None
    assert "验证检查项" in refusal


@pytest.mark.parametrize("kind", sorted(WRITE_KINDS))
def test_auto_approve_allows_write_kinds_without_checks(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """开启全流程自动化后，表决流程替代验证检查项作为无人值守闸门。"""
    _stub_kind(monkeypatch, kind)
    assert loop_scope_refusal(
        "随便一句指令", has_checks=False, auto_approve=True
    ) is None


@pytest.mark.parametrize("kind", sorted(REFUSED_KINDS))
def test_refused_kinds_say_it_will_stall(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """被明确拒掉的类型要说清是「会停下来等确认」，而不是「不许做」。"""
    _stub_kind(monkeypatch, kind)
    refusal = loop_scope_refusal("随便一句指令", has_checks=True)
    assert refusal is not None
    assert "确认" in refusal


def test_refused_kinds_are_named_in_the_refusal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """理由里要写清被识别成了哪一类，否则用户不知道该怎么改。"""
    _stub_kind(monkeypatch, "planning")
    assert "方案规划" in str(loop_scope_refusal("随便一句指令", has_checks=True))


def test_refusal_does_not_leak_the_internal_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """给人看的理由里不能出现 ``planning`` 这种内部取值。"""
    _stub_kind(monkeypatch, "planning")
    assert "planning" not in str(loop_scope_refusal("随便一句指令", has_checks=True))


def test_an_unknown_kind_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    """冒出来一个没见过的类型时默认拒掉：漏放一个写入类任务的代价比多拒一次大。"""
    _stub_kind(monkeypatch, "什么鬼类型")
    refusal = loop_scope_refusal("随便一句指令", has_checks=True)
    assert refusal is not None


def test_refusal_judges_the_instruction_it_was_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """判定必须拿调用方给的那一句，不能自己另取。"""
    calls = _stub_kind(monkeypatch, "analysis")
    loop_scope_refusal("分析这个仓库的目录结构", has_checks=False)
    assert calls == ["分析这个仓库的目录结构"]


def test_every_known_kind_is_either_allowed_or_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """七种类型全都要有明确处置，不能有漏网的。"""
    from agent.runtime.task_router import TASK_KIND_LABELS

    assert set(ALL_KINDS) == set(TASK_KIND_LABELS)
    assert set(ALL_KINDS) == READ_ONLY_KINDS | WRITE_KINDS | REFUSED_KINDS
    for kind in ALL_KINDS:
        _stub_kind(monkeypatch, kind)
        # 配了检查项时，只有 REFUSED_KINDS 会被拒。
        allowed = loop_scope_refusal("随便一句指令", has_checks=True) is None
        assert allowed == (kind not in REFUSED_KINDS)
        # 没配检查项时，写入类也会被拒。
        allowed_without_checks = (
            loop_scope_refusal("随便一句指令", has_checks=False) is None
        )
        assert allowed_without_checks == (kind in READ_ONLY_KINDS)


def test_refusal_for_kind_and_scope_refusal_agree(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """两条入口（已判好的类型 / 一句指令）必须给出同一个结论。"""
    _stub_kind(monkeypatch, "coding")
    assert refusal_for_kind("coding", has_checks=False) == loop_scope_refusal(
        "随便一句指令", has_checks=False
    )


def test_read_only_kinds_match_the_project_predicate_except_for_sync() -> None:
    """放行名单比项目的「只读任务」判定多一个仓库同步，别的都一样。

    项目自己的 :func:`utils.intent_utils.is_read_only_task` 不含 ``sync``（它管的是
    「能不能改文件、提交、push」）；循环这边额外放行 ``sync``，因为「定时拉最新代码」
    正是无人值守循环的典型用途，而它不产出任何改动。
    """
    from utils.intent_utils import is_read_only_task

    for kind in READ_ONLY_KINDS - {"sync"}:
        assert is_read_only_task(kind), f"{kind} 本该是只读任务"
    assert not is_read_only_task("sync")


def test_planning_is_deliberately_excluded() -> None:
    """方案规划在项目判定里算只读，但循环里不放行。

    它不是不能跑，是**跑不完**：现有路径会把方案停下来等用户确认，无人值守的循环
    会永远停在那一步。
    """
    from utils.intent_utils import is_read_only_task

    assert is_read_only_task("planning")
    assert "planning" not in READ_ONLY_KINDS
    assert "planning" in REFUSED_KINDS
    assert "coding" not in REFUSED_KINDS


def test_completed_maps_to_succeeded() -> None:
    """正常跑完算成功。"""
    assert _outcome_from_status("completed") == "succeeded"


def test_failed_maps_to_failed() -> None:
    """失败算失败。"""
    assert _outcome_from_status("failed") == "failed"


@pytest.mark.parametrize("status", ["pending", "running", "", "什么鬼"])
def test_unknown_status_maps_to_blocked(status: str) -> None:
    """拿不准的状态一律算「没做完」，绝不能算成功 —— 算成功会掩盖问题。"""
    assert _outcome_from_status(status) == "blocked"


def test_runner_refuses_before_touching_the_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """写入任务没配检查项时直接返回，不去建会话、不调模型。"""
    _stub_kind(monkeypatch, "coding")
    runner = make_agent_round_runner()
    result = runner(
        RoundRequest(
            automation_id="a1",
            name="每日分诊",
            prompt="拼好的指令",
            instruction="实现一个登录页并提交代码",
            repo="https://example.com/a/b.git",
        )
    )
    assert result.outcome == "blocked"
    assert "验证检查项" in result.summary
    assert result.thread_id is None


def test_a_write_round_skips_the_plan_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配了检查项的写入任务真的往下跑，并且跳过「先出方案等确认」那一步。

    交互路径会把首轮的开发需求转成 planning 停下来等用户确认；无人值守的循环里
    没有人会点那个确认，所以必须显式跳过 —— 否则每轮都停在同一处。
    """
    seen: dict[str, Any] = {}

    def fake_run_agent_task(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"messages": [], "run_id": "run-9"}

    class FakeStore:
        def get_thread(self, thread_id: str) -> dict[str, str]:
            return {"latest_run_status": "completed"}

    monkeypatch.setattr("agent.runtime.task_router.run_agent_task", fake_run_agent_task)
    monkeypatch.setattr(
        "agent.runtime.task_router.initialize_task_record",
        lambda **kwargs: kwargs["thread_id"],
    )
    monkeypatch.setattr("agent.graph.graph.get_store", lambda: FakeStore())
    monkeypatch.setattr(
        "utils.message_utils.extract_final_assistant_text", lambda messages: "改好了"
    )
    monkeypatch.setattr("services.automation_runner.resolve_workspace", lambda *a, **k: None)
    monkeypatch.setattr(
        "services.automation_runner.resolve_container_exec", lambda *a, **k: None
    )
    _stub_kind(monkeypatch, "coding")

    from services.automation_verification import Check

    runner = make_agent_round_runner(thread_id="t1")
    result = runner(
        RoundRequest(
            automation_id="a1",
            name="每日实现",
            prompt="实现登录页",
            instruction="实现一个登录页",
            repo="https://github.com/owner/repo.git",
            checks=(Check(command="pytest -q", description="回归"),),
        )
    )

    assert result.outcome == "succeeded"
    assert seen["task_kind"] == "coding"
    assert seen["skip_plan_confirmation"] is True
    # 运行 id 要带回来，调用方才能写进定义的 lastRunId。
    assert result.run_id == "run-9"


def test_the_round_run_id_falls_back_to_the_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """运行时没回 run_id 时，从运行记录里补一个 —— 界面靠它跳转到这一轮。"""

    def fake_run_agent_task(**kwargs: Any) -> dict[str, Any]:
        return {"messages": []}

    class FakeStore:
        def get_thread(self, thread_id: str) -> dict[str, str]:
            return {"latest_run_status": "completed"}

        def get_latest_run(self, thread_id: str) -> dict[str, str]:
            return {"run_id": "run-from-store"}

    monkeypatch.setattr("agent.runtime.task_router.run_agent_task", fake_run_agent_task)
    monkeypatch.setattr(
        "agent.runtime.task_router.initialize_task_record",
        lambda **kwargs: kwargs["thread_id"],
    )
    monkeypatch.setattr("agent.graph.graph.get_store", lambda: FakeStore())
    monkeypatch.setattr(
        "utils.message_utils.extract_final_assistant_text", lambda messages: ""
    )
    monkeypatch.setattr("services.automation_runner.resolve_workspace", lambda *a, **k: None)
    monkeypatch.setattr(
        "services.automation_runner.resolve_container_exec", lambda *a, **k: None
    )
    _stub_kind(monkeypatch, "analysis")

    runner = make_agent_round_runner(thread_id="t1")
    result = runner(
        RoundRequest(
            automation_id="a1",
            name="每日分诊",
            prompt="读一遍仓库概况",
            instruction="读一遍仓库概况",
            repo="https://github.com/owner/repo.git",
        )
    )

    assert result.run_id == "run-from-store"


def test_reading_the_run_id_never_fails_the_round() -> None:
    """运行记录读不到就返回 None —— 它是旁路信息，不该把一轮好活带崩。"""

    class BrokenStore:
        def get_latest_run(self, thread_id: str) -> dict[str, str]:
            raise RuntimeError("库连不上")

    assert _latest_run_id(BrokenStore(), "t1") is None


def test_runner_refuses_without_a_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    """没有代码仓库时不执行，理由要写清。"""
    _stub_kind(monkeypatch, "analysis")
    runner = make_agent_round_runner()
    result = runner(
        RoundRequest(
            automation_id="a1",
            name="每日分诊",
            prompt="拼好的指令",
            instruction="分析这个仓库的目录结构",
            repo=None,
        )
    )
    assert result.outcome == "blocked"
    assert "代码仓库" in result.summary


def test_runner_judges_the_original_instruction_not_the_assembled_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """判定用的是原始指令，拼上去的循环状态与反馈不参与判定。"""
    calls = _stub_kind(monkeypatch, "coding")
    runner = make_agent_round_runner()
    runner(
        RoundRequest(
            automation_id="a1",
            name="每日分诊",
            prompt="原始指令\n\n【循环状态】已经跑了 3 轮",
            instruction="原始指令",
            repo="https://example.com/a/b.git",
        )
    )
    assert calls == ["原始指令"]


def test_runner_falls_back_to_the_full_prompt_when_instruction_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没单独给原始指令时退回用完整指令判断，不能当成「没限制」。"""
    calls = _stub_kind(monkeypatch, "coding")
    runner = make_agent_round_runner()
    result = runner(
        RoundRequest(
            automation_id="a1",
            name="每日分诊",
            prompt="实现一个登录页并提交代码",
            repo="https://example.com/a/b.git",
        )
    )
    assert result.outcome == "blocked"
    assert calls == ["实现一个登录页并提交代码"]


def test_runner_hands_the_decided_kind_to_the_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """判定结论要一起传给运行时，不能让它拿拼好的文本重判一次。

    这是那个「循环卡住还看不出来」的缺陷的回归用例：只读闸按原始指令判成
    ``analysis``，但拼上循环状态与验证反馈之后那份文本里出现了「提交」「修改代码」，
    运行时若重判就会翻成 ``coding``，走进等确认的分支停住。
    """
    seen: dict[str, Any] = {}
    assembled = (
        "分析这个仓库的目录结构\n\n---\n这是这个自动任务的第 3 轮。\n"
        "上一轮的验证没有通过，反馈如下：\n需要修改代码后重新提交"
    )

    def fake_run_agent_task(**kwargs: Any) -> dict[str, Any]:
        seen.update(kwargs)
        return {"messages": []}

    class FakeStore:
        def get_thread(self, thread_id: str) -> dict[str, str]:
            return {"latest_run_status": "completed"}

    monkeypatch.setattr("agent.runtime.task_router.run_agent_task", fake_run_agent_task)
    monkeypatch.setattr(
        "agent.runtime.task_router.initialize_task_record",
        lambda **kwargs: kwargs["thread_id"],
    )
    monkeypatch.setattr("agent.graph.graph.get_store", lambda: FakeStore())
    monkeypatch.setattr(
        "utils.message_utils.extract_final_assistant_text", lambda messages: ""
    )
    monkeypatch.setattr("services.automation_runner.resolve_workspace", lambda *a, **k: None)
    _stub_kind(monkeypatch, "analysis")

    runner = make_agent_round_runner(thread_id="t1")
    result = runner(
        RoundRequest(
            automation_id="a1",
            name="每日分诊",
            prompt=assembled,
            instruction="分析这个仓库的目录结构",
            repo="https://github.com/owner/repo.git",
        )
    )

    assert result.outcome == "succeeded"
    assert seen["task_kind"] == "analysis"
    assert seen["routing_prompt"] == "分析这个仓库的目录结构"
    assert seen["prompt"] == assembled


def test_a_round_waiting_for_approval_says_what_it_is_waiting_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """停在等人工审批的轮次，summary 必须说清在等什么。

    实测：停在等审批时最后一条助手消息只有工具调用、没有文字，
    ``extract_final_assistant_text`` 返回空串，状态文件里就留下一轮空 summary ——
    定时任务在列表上看起来一切正常，实际每轮都停在等一个没人会点的确认。
    """

    def fake_run_agent_task(**kwargs: Any) -> dict[str, Any]:
        return {"messages": []}

    class FakeStore:
        def get_thread(self, thread_id: str) -> dict[str, str]:
            return {"latest_run_status": "awaiting_approval"}

    monkeypatch.setattr("agent.runtime.task_router.run_agent_task", fake_run_agent_task)
    monkeypatch.setattr(
        "agent.runtime.task_router.initialize_task_record",
        lambda **kwargs: kwargs["thread_id"],
    )
    monkeypatch.setattr("agent.graph.graph.get_store", lambda: FakeStore())
    monkeypatch.setattr(
        "utils.message_utils.extract_final_assistant_text", lambda messages: ""
    )
    monkeypatch.setattr("services.automation_runner.resolve_workspace", lambda *a, **k: None)
    monkeypatch.setattr(
        "config.approval_runtime.get_pending_approval",
        lambda thread_id: {
            "actions": [
                {"name": "execute", "command": "git -C repo status"},
                {"name": "execute", "command": "git -C repo log --oneline -10"},
            ]
        },
    )
    _stub_kind(monkeypatch, "analysis")

    runner = make_agent_round_runner(thread_id="t1")
    result = runner(
        RoundRequest(
            automation_id="a1",
            name="每日分诊",
            prompt="读一遍仓库概况",
            instruction="读一遍仓库概况",
            repo="https://github.com/owner/repo.git",
        )
    )

    assert result.outcome == "blocked"
    # 只记成 blocked 还不够 —— 循环要靠这个标记才知道该停下来等人，否则它会
    # 以为这一轮「没做完但可以接着做」，下一轮又卡在同一条命令上。
    assert result.waiting_approval is True
    assert "等待人工审批" in result.summary
    assert "git -C repo status" in result.summary
    assert "git -C repo log --oneline -10" in result.summary


def test_reading_the_pending_approval_never_breaks_the_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """读待审批内容失败时也要给出一句能看懂的说明，不能让这一轮再炸一次。"""

    def fake_run_agent_task(**kwargs: Any) -> dict[str, Any]:
        return {"messages": []}

    class FakeStore:
        def get_thread(self, thread_id: str) -> dict[str, str]:
            return {"latest_run_status": "awaiting_approval"}

    def boom(thread_id: str) -> Any:
        raise RuntimeError("读不到")

    monkeypatch.setattr("agent.runtime.task_router.run_agent_task", fake_run_agent_task)
    monkeypatch.setattr(
        "agent.runtime.task_router.initialize_task_record",
        lambda **kwargs: kwargs["thread_id"],
    )
    monkeypatch.setattr("agent.graph.graph.get_store", lambda: FakeStore())
    monkeypatch.setattr(
        "utils.message_utils.extract_final_assistant_text", lambda messages: ""
    )
    monkeypatch.setattr("services.automation_runner.resolve_workspace", lambda *a, **k: None)
    monkeypatch.setattr("config.approval_runtime.get_pending_approval", boom)
    _stub_kind(monkeypatch, "analysis")

    result = make_agent_round_runner(thread_id="t1")(
        RoundRequest(
            automation_id="a1",
            name="每日分诊",
            prompt="读一遍仓库概况",
            instruction="读一遍仓库概况",
            repo="https://github.com/owner/repo.git",
        )
    )

    assert result.outcome == "blocked"
    assert "等待人工审批" in result.summary


def test_runner_marks_the_thread_failed_when_the_runtime_blows_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """运行时抛异常时要把会话标成失败。

    ``run_agent_task`` 自己的 ``try`` 只包住「调用 Agent 执行」那一段，它前面的
    准备步骤（连 Store、连 checkpoint、建 Agent 图）出错时会把异常直接抛出来，
    而会话状态已经写成 ``running``。不补这一刀，界面上会留下一个永远在跑、
    实际早就死了的会话；循环每失败一次留一个。
    """
    marked: list[tuple[str, str]] = []

    class FakeStore:
        def update_thread_status(self, thread_id: str, status: str) -> None:
            marked.append((thread_id, status))

    def boom(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("Redis 要密码")

    monkeypatch.setattr("agent.runtime.task_router.run_agent_task", boom)
    monkeypatch.setattr(
        "agent.runtime.task_router.initialize_task_record",
        lambda **kwargs: kwargs["thread_id"],
    )
    monkeypatch.setattr("agent.graph.graph.get_store", lambda: FakeStore())
    monkeypatch.setattr("services.automation_runner.resolve_workspace", lambda *a, **k: None)
    _stub_kind(monkeypatch, "analysis")

    runner = make_agent_round_runner(thread_id="t1")
    result = runner(
        RoundRequest(
            automation_id="a1",
            name="每日分诊",
            prompt="拼好的指令",
            instruction="分析这个仓库的目录结构",
            repo="https://github.com/owner/repo.git",
        )
    )

    assert result.outcome == "failed"
    assert "Redis 要密码" in result.summary
    assert marked == [("t1", "failed")]


def test_runner_does_not_blow_up_when_marking_failed_also_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """连标记失败都失败时，仍然要交回一个失败轮次，不能把异常再抛出去。"""
    store_calls: list[str] = []

    class FakeStore:
        def update_thread_status(self, thread_id: str, status: str) -> None:
            store_calls.append(status)
            raise RuntimeError("Store 也连不上")

    def boom(**kwargs: Any) -> dict[str, Any]:
        raise RuntimeError("原始错误")

    monkeypatch.setattr("agent.runtime.task_router.run_agent_task", boom)
    monkeypatch.setattr(
        "agent.runtime.task_router.initialize_task_record",
        lambda **kwargs: kwargs["thread_id"],
    )
    monkeypatch.setattr("agent.graph.graph.get_store", lambda: FakeStore())
    monkeypatch.setattr("services.automation_runner.resolve_workspace", lambda *a, **k: None)
    _stub_kind(monkeypatch, "analysis")

    runner = make_agent_round_runner(thread_id="t1")
    result = runner(
        RoundRequest(
            automation_id="a1",
            name="每日分诊",
            prompt="拼好的指令",
            instruction="分析这个仓库的目录结构",
            repo="https://github.com/owner/repo.git",
        )
    )

    assert store_calls == ["failed"]
    assert result.outcome == "failed"
    assert "原始错误" in result.summary


def test_resolve_backend_type_follows_the_caller_then_the_settings() -> None:
    """调用方指定就听调用方的，没指定才看全局配置。"""
    assert resolve_backend_type("sandbox") == "sandbox"
    assert resolve_backend_type("local") == "local"
    assert resolve_backend_type(None) in {"local", "sandbox"}
    assert resolve_backend_type("什么鬼") in {"local", "sandbox"}


def test_resolve_workspace_returns_none_in_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """沙箱里仓库在容器内，没有本机目录 —— 要说不知道，不能猜一个。"""
    monkeypatch.setattr(
        "server.ensure_backend_for_thread", lambda thread_id, backend_type=None: object()
    )
    assert (
        resolve_workspace("t1", "https://github.com/o/r.git", backend_type="sandbox")
        is None
    )


def test_resolve_workspace_returns_none_when_the_repo_is_not_there(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """仓库还没 clone 下来时返回 None，让调用方跳过验证，而不是退回当前目录。"""
    missing = tmp_path / "还没clone"
    monkeypatch.setattr(
        "server.ensure_backend_for_thread",
        lambda thread_id, backend_type=None: SimpleNamespace(workspace=tmp_path),
    )
    monkeypatch.setattr("agent.graph.graph.get_store", lambda: object())
    monkeypatch.setattr(
        "agent.runtime.repo_mapping.discover_repo_mapping",
        lambda **kwargs: SimpleNamespace(local_path=str(missing)),
    )
    assert (
        resolve_workspace("t1", "https://github.com/o/r.git", backend_type="local")
        is None
    )


def test_resolve_workspace_returns_the_directory_when_it_exists(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """目录真的在，就把绝对路径交出去。"""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    monkeypatch.setattr(
        "server.ensure_backend_for_thread",
        lambda thread_id, backend_type=None: SimpleNamespace(workspace=tmp_path),
    )
    monkeypatch.setattr("agent.graph.graph.get_store", lambda: object())
    monkeypatch.setattr(
        "agent.runtime.repo_mapping.discover_repo_mapping",
        lambda **kwargs: SimpleNamespace(local_path=str(repo_dir)),
    )
    assert (
        resolve_workspace("t1", "https://github.com/o/r.git", backend_type="local")
        == str(repo_dir)
    )


def test_resolve_workspace_swallows_lookup_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """解析失败不能让整轮炸掉，返回 None 让验证走「没执行」。"""

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("数据库连不上")

    monkeypatch.setattr("server.ensure_backend_for_thread", boom)
    assert (
        resolve_workspace("t1", "https://github.com/o/r.git", backend_type="local")
        is None
    )


def test_token_usage_is_summed_across_the_round() -> None:
    """一轮里模型可能被调用多次，用量要全加起来 —— 只算最后一条会严重低估。

    消息的实际形状是 ``{"type": ..., "content": ..., "usage_metadata": ...}``，
    由 ``agent.runtime.streaming_runtime._message_dict`` 产出。
    """
    messages = [
        {"type": "ai", "content": "先看看仓库", "usage_metadata": {"total_tokens": 120}},
        {"type": "tool", "content": "…"},
        {"type": "ai", "content": "总结", "usage_metadata": {"total_tokens": 35}},
    ]
    assert _tokens_used(messages) == 155


def test_token_usage_reads_langchain_objects_too() -> None:
    """也兼容 LangChain 消息对象 —— 消息形状变了不该静默变成 0。"""
    assert _tokens_used([SimpleNamespace(usage_metadata={"total_tokens": 7})]) == 7


def test_missing_token_usage_counts_as_zero() -> None:
    """拿不到用量按 0 记 —— 统计是旁路信息，缺了不该把一轮好活判成失败。"""
    assert _tokens_used([]) == 0
    assert _tokens_used([{"type": "ai", "content": "没有用量"}]) == 0
    assert _tokens_used([{"usage_metadata": None}]) == 0
    assert _tokens_used([{"usage_metadata": "不是字典"}]) == 0
    assert _tokens_used([{"usage_metadata": {"total_tokens": "坏值"}}]) == 0
    assert _tokens_used([{"usage_metadata": {"total_tokens": -5}}]) == 0


APPROVAL_COMMAND = 'git commit -m "自动提交"'
"""造出来的待审批命令。内容不影响判定 —— 表决在这组用例里是打桩的。"""


def _stub_store(statuses: list[str]) -> tuple[Any, list[int]]:
    """造一个按顺序吐运行状态的 Store。

    每读一次往后走一格，最后一格一直重复：表决恢复之后要能读到新状态，而
    「一直卡在等审批」正是上限那条用例需要的情形。

    返回的实例**必须被复用**（``agent.graph.graph.get_store`` 本来就是单例）：
    每次调用都造一个新的，读指针就会各自从头开始，恢复之后读到的还是「等审批」。

    Args:
        statuses: 依次要返回的 ``latest_run_status``。

    Returns:
        ``(Store, 读次数)``。
    """
    reads = [0]

    class FakeStore:
        def get_thread(self, thread_id: str) -> dict[str, str]:
            # runner 会先探测手动触发接口已经建好的会话；这次读不属于运行
            # 状态采样，不能消耗 statuses，否则会把 awaiting_approval 跳过去。
            if reads[0] == 0:
                reads[0] += 1
                return {"thread_id": thread_id, "latest_run_status": "running"}
            index = min(reads[0] - 1, len(statuses) - 1)
            reads[0] += 1
            return {"latest_run_status": statuses[index]}

    return FakeStore(), reads


def _stub_vote(
    monkeypatch: pytest.MonkeyPatch,
    decisions: list[str],
    *,
    tokens: int = 0,
) -> list[str]:
    """把表决打桩成固定的结论序列。

    Args:
        monkeypatch: pytest 的打桩器。
        decisions: 依次要返回的表决结论；用完之后一直复用最后一个。
        tokens: 每次表决报出来的用量。

    Returns:
        记录每次实际用了哪个结论的列表。
    """
    from services.approval_vote import VoteCast, VoteOutcome

    calls: list[str] = []

    def fake_vote(pending: dict[str, Any], **kwargs: Any) -> Any:
        decision = decisions[min(len(calls), len(decisions) - 1)]
        calls.append(decision)
        return VoteOutcome(
            commands=(APPROVAL_COMMAND,),
            votes=(
                VoteCast(
                    role="irreversible",
                    label="不可逆损失",
                    approve=decision == "approve",
                    reason="会把改动推到远端",
                ),
            ),
            decision=decision,  # type: ignore[arg-type]
            reason=f"表决结论：{decision}；会把改动推到远端",
            tokens=tokens,
        )

    monkeypatch.setattr("services.approval_vote.vote_on_approval", fake_vote)
    return calls


def _run_approval_round(
    monkeypatch: pytest.MonkeyPatch,
    *,
    auto_approve: bool,
    statuses: list[str],
    resume_error: Exception | None = None,
) -> tuple[Any, dict[str, Any]]:
    """跑一轮「停在等审批」的轮次，线路全部打桩。

    Args:
        monkeypatch: pytest 的打桩器。
        auto_approve: 这一轮的任务开没开表决。
        statuses: Store 依次返回的运行状态。
        resume_error: 让恢复调用抛出的异常；``None`` 表示恢复成功。

    Returns:
        ``(RoundResult, 调用痕迹)``；痕迹里 ``resume`` 是恢复调用、
        ``pending`` 是读待审批内容的次数。
    """
    traces: dict[str, Any] = {"resume": [], "pending": 0}

    def fake_run_agent_task(**kwargs: Any) -> dict[str, Any]:
        return {"messages": []}

    def fake_resume(**kwargs: Any) -> dict[str, Any]:
        traces["resume"].append((kwargs["decision"], kwargs.get("reject_message")))
        if resume_error is not None:
            raise resume_error
        return {"messages": []}

    def fake_pending(thread_id: str) -> dict[str, Any]:
        traces["pending"] += 1
        return {
            "id": "approval-1",
            "threadId": thread_id,
            "backendType": "local",
            "actions": [{"name": "execute", "command": APPROVAL_COMMAND}],
        }

    store, _reads = _stub_store(statuses)
    monkeypatch.setattr("agent.runtime.task_router.run_agent_task", fake_run_agent_task)
    monkeypatch.setattr("agent.runtime.task_router.resume_agent_task", fake_resume)
    monkeypatch.setattr(
        "agent.runtime.task_router.initialize_task_record",
        lambda **kwargs: kwargs["thread_id"],
    )
    monkeypatch.setattr("agent.graph.graph.get_store", lambda: store)
    monkeypatch.setattr("config.approval_runtime.get_pending_approval", fake_pending)
    monkeypatch.setattr(
        "utils.message_utils.extract_final_assistant_text", lambda messages: ""
    )
    monkeypatch.setattr("services.automation_runner.resolve_workspace", lambda *a, **k: None)
    _stub_kind(monkeypatch, "analysis")

    result = make_agent_round_runner(thread_id="t1")(
        RoundRequest(
            automation_id="a1",
            name="每日分诊",
            prompt="读一遍仓库概况",
            instruction="读一遍仓库概况",
            repo="https://github.com/owner/repo.git",
            auto_approve=auto_approve,
        )
    )
    return result, traces


def test_a_task_without_the_switch_never_votes(monkeypatch: pytest.MonkeyPatch) -> None:
    """没开表决的任务，一次表决都不该发。

    这是默认路径，也是最要紧的一条：开关没开还去问评审，等于把人工闸门默认交出去。
    """

    def boom(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("没开表决却调了表决")

    monkeypatch.setattr("services.approval_vote.vote_on_approval", boom)

    result, traces = _run_approval_round(
        monkeypatch, auto_approve=False, statuses=["awaiting_approval"]
    )

    assert result.waiting_approval is True
    assert "等待人工审批" in result.summary
    assert result.approval_votes == ()
    assert traces["resume"] == []
    # 读过一次待审批内容，那是给**人**看的那句说明用的（`_awaiting_approval_summary`），
    # 不是表决。这条一并钉住，是因为「读了一次」和「问过评审」很容易被混为一谈。
    assert traces["pending"] == 1


def test_the_switch_settles_the_approval_and_keeps_the_round_going(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """开着表决时，审批就地处理掉，这一轮接着跑完。"""
    _stub_vote(monkeypatch, ["approve"], tokens=42)

    result, traces = _run_approval_round(
        monkeypatch, auto_approve=True, statuses=["awaiting_approval", "completed"]
    )

    assert result.outcome == "succeeded"
    assert result.waiting_approval is False
    assert [item["decision"] for item in result.approval_votes] == ["approve"]
    assert traces["resume"] == [("approve", None)]
    # 自己放行了一条需审批的命令，摘要里要说清是哪一条 —— 只在状态文件里留着，
    # 看列表的人不会知道有人（或机器）替他把闸门开了。
    assert "本轮审批表决：1 次放行" in result.summary
    assert APPROVAL_COMMAND in result.summary
    # 表决是这一轮为了继续跑额外花的，得算进这一轮的用量。
    assert result.tokens == 42


def test_a_rejection_is_explained_to_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    """表决否决时，交回模型的话必须说明是机器投的票、以及否决理由。

    人工点拒绝说的是「用户拒绝了这次命令执行」。表决沿用那句有两个问题：模型会
    以为有人在看着它，而且拿不到让它换做法的信息，于是原样重试同一条命令、
    再撞上同一次否决。
    """
    _stub_vote(monkeypatch, ["reject"])

    _result, traces = _run_approval_round(
        monkeypatch, auto_approve=True, statuses=["awaiting_approval", "completed"]
    )

    decision, message = traces["resume"][0]
    assert decision == "reject"
    assert "评审表决" in message
    assert "会把改动推到远端" in message
    assert "用户拒绝" not in message


def test_a_vote_that_cannot_decide_is_safely_rejected_without_human_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """表决没结论时按安全拒绝恢复，不能违背全流程自动化语义去等人。"""
    _stub_vote(monkeypatch, ["undecided"])

    result, traces = _run_approval_round(
        monkeypatch, auto_approve=True, statuses=["awaiting_approval", "completed"]
    )

    assert result.outcome == "succeeded"
    assert result.waiting_approval is False
    decision, message = traces["resume"][0]
    assert decision == "reject"
    assert "按安全策略拒绝" in message
    assert "表决结论：undecided" in message


def test_the_round_fails_after_the_vote_cap_without_waiting_for_a_human(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """用满表决次数仍撞审批就明确失败，不能留下人工审批。"""
    from services.automation_loop import MAX_APPROVAL_VOTES_PER_ROUND

    calls = _stub_vote(monkeypatch, ["approve"])

    result, _traces = _run_approval_round(
        monkeypatch, auto_approve=True, statuses=["awaiting_approval"]
    )

    assert len(calls) == MAX_APPROVAL_VOTES_PER_ROUND
    assert result.outcome == "failed"
    assert result.waiting_approval is False
    assert len(result.approval_votes) == MAX_APPROVAL_VOTES_PER_ROUND
    assert "仍反复触发审批" in result.summary


def test_a_failing_resume_fails_the_round_without_human_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """恢复基础设施失败时本轮失败，并且不能留下待人工处理的审批。"""
    _stub_vote(monkeypatch, ["approve"])

    result, _traces = _run_approval_round(
        monkeypatch,
        auto_approve=True,
        statuses=["awaiting_approval"],
        resume_error=RuntimeError("checkpoint 读不到"),
    )

    assert result.outcome == "failed"
    assert result.waiting_approval is False
    assert "checkpoint 读不到" in result.summary


def _fake_sandbox_backend(
    responses: dict[str, tuple[int, str]], *, has_workspace: bool = False
) -> Any:
    """造一个假沙箱后端：按命令返回预设的退出码与输出。"""
    calls: list[tuple[str, int | None]] = []

    class FakeBackend:
        def execute(self, command: str, *, timeout: int | None = None) -> Any:
            calls.append((command, timeout))
            code, output = responses.get(command, (128, ""))
            return SimpleNamespace(exit_code=code, output=output)

    backend = FakeBackend()
    if has_workspace:
        backend.workspace = "/tmp/fake-workspace"
    return SimpleNamespace(backend=backend, calls=calls)


def _stub_backend(monkeypatch: pytest.MonkeyPatch, backend: Any) -> None:
    """把取 backend 的那一步换掉，避免真去连沙箱。"""
    monkeypatch.setattr(
        "server.ensure_backend_for_thread", lambda thread_id, backend_type: backend
    )


def test_container_exec_is_not_resolved_for_the_local_backend() -> None:
    """本地执行环境不需要容器执行器。"""
    assert resolve_container_exec("t1", "https://github.com/o/r.git", backend_type="local") is None


def test_container_repo_dir_is_discovered_by_asking_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """容器里的仓库目录是**问出来的**，不是猜的：问到哪条算哪条。"""
    fake = _fake_sandbox_backend(
        {
            "git -C projects/r rev-parse --show-toplevel": (128, "fatal: not a git repository"),
            "git -C /projects/r rev-parse --show-toplevel": (0, "/projects/r\n"),
        }
    )
    _stub_backend(monkeypatch, fake.backend)

    resolved = resolve_container_exec(
        "t1", "https://github.com/owner/r.git", backend_type="sandbox"
    )

    assert resolved is not None
    assert resolved.repo_dir == "/projects/r"
    assert resolved.backend is fake.backend
    # 第一个候选问失败了才会去问第二个。
    assert fake.calls[0][0] == "git -C projects/r rev-parse --show-toplevel"
    assert fake.calls[1][0] == "git -C /projects/r rev-parse --show-toplevel"


def test_container_probe_command_passes_the_policy(monkeypatch: pytest.MonkeyPatch) -> None:
    """探路命令必须是审批策略判「放行」的只读命令。

    沙箱的 ``execute`` 只拦 ``deny``，判成 ``approval`` 的命令会**直接跑掉** ——
    那就等于循环自己在无人值守时放行了一条本该等人确认的命令。所以这条探路命令
    必须落在只读白名单里。
    """
    from backends.command_policy import evaluate_command

    for candidate in ("projects/r", "/projects/r", "/workspace/r", "~/r"):
        command = f"git -C {candidate} rev-parse --show-toplevel"
        result = evaluate_command(command, "sandbox")
        assert result.action == "allow", f"{command} 被判成 {result.action}"


def test_container_exec_is_none_when_no_candidate_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一个候选都对不上时返回 None —— 调用方会记「没有执行验证」，不糊一份结论。"""
    fake = _fake_sandbox_backend({})
    _stub_backend(monkeypatch, fake.backend)

    assert (
        resolve_container_exec(
            "t1", "https://github.com/owner/r.git", backend_type="sandbox"
        )
        is None
    )


def test_container_exec_skips_backends_that_have_a_local_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """后端有本机工作区就说明这一轮落在本地，不该走容器那条路。"""
    fake = _fake_sandbox_backend({}, has_workspace=True)
    _stub_backend(monkeypatch, fake.backend)

    assert (
        resolve_container_exec(
            "t1", "https://github.com/owner/r.git", backend_type="sandbox"
        )
        is None
    )
    assert fake.calls == []


def test_a_broken_probe_does_not_break_the_round(monkeypatch: pytest.MonkeyPatch) -> None:
    """连不上沙箱时返回 None，而不是把异常抛进这一轮。"""

    class BrokenBackend:
        def execute(self, command: str, *, timeout: int | None = None) -> Any:
            raise RuntimeError("沙箱连不上")

    _stub_backend(monkeypatch, BrokenBackend())

    assert (
        resolve_container_exec(
            "t1", "https://github.com/owner/r.git", backend_type="sandbox"
        )
        is None
    )


def test_a_broken_backend_lookup_does_not_break_the_round(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """取 backend 这一步就炸了也要返回 None，原因记日志。"""

    def boom(thread_id: str, backend_type: str) -> Any:
        raise RuntimeError("沙箱创建失败")

    monkeypatch.setattr("server.ensure_backend_for_thread", boom)

    assert (
        resolve_container_exec(
            "t1", "https://github.com/owner/r.git", backend_type="sandbox"
        )
        is None
    )


def _stub_a_round_that_reaches_the_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    """把一轮里除「登记」之外的东西全换成替身，返回记录器。

    ``fake_run_agent_task`` 里那几行就是「准备仓库还没结束、Agent 事件循环还没进」
    的时刻 —— 也正是本组用例要观测的那一点。

    Returns:
        记录器，含 ``cancel_accepted``（那一刻停止请求受不受理）。
    """
    seen: dict[str, Any] = {}

    def fake_run_agent_task(**kwargs: Any) -> dict[str, Any]:
        from agent.runtime.cancellation import request_cancel

        seen["cancel_accepted"] = request_cancel(kwargs["thread_id"])
        return {"messages": [], "status": "completed"}

    class FakeStore:
        def get_thread(self, thread_id: str) -> dict[str, str]:
            return {"latest_run_status": "completed"}

        def get_latest_run(self, thread_id: str) -> dict[str, str]:
            return {"run_id": "run-1"}

    monkeypatch.setattr("agent.runtime.task_router.run_agent_task", fake_run_agent_task)
    monkeypatch.setattr("agent.graph.graph.get_store", lambda: FakeStore())
    monkeypatch.setattr(
        "utils.message_utils.extract_final_assistant_text", lambda messages: ""
    )
    monkeypatch.setattr(
        "services.automation_runner.resolve_workspace", lambda *a, **k: None
    )
    monkeypatch.setattr(
        "services.automation_runner.resolve_container_exec", lambda *a, **k: None
    )
    _stub_kind(monkeypatch, "analysis")
    return seen


def _run_one_round(thread_id: str) -> Any:
    """按既有用例的写法跑一轮最小可用的自动化任务。"""
    return make_agent_round_runner(thread_id=thread_id)(
        RoundRequest(
            automation_id="a1",
            name="每日分诊",
            prompt="读一遍仓库概况",
            instruction="读一遍仓库概况",
            repo="https://github.com/owner/repo.git",
        )
    )


def test_the_round_is_registered_before_the_agent_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """准备仓库那段也要能停。

    ``run_agent_task`` 前面的准备步骤（连 Store、clone 仓库、建 Agent 图）发生在
    Agent 事件循环之前。那时没有登记的话，用户点「停止」会拿到 409 —— 明明在跑，
    接口却说没有正在运行的任务。而准备仓库恰恰是最该能停的一段。
    """
    seen = _stub_a_round_that_reaches_the_agent(monkeypatch)

    _run_one_round("t1")

    assert seen["cancel_accepted"] is True


def test_a_finished_round_leaves_no_pending_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """收尾要把停止标志清干净。

    留着的话下一轮一开跑就会在第一个事件被停 —— 用户看到的是「任务自己莫名停了」。
    """
    from agent.runtime.cancellation import is_cancel_requested

    _stub_a_round_that_reaches_the_agent(monkeypatch)

    _run_one_round("t2")

    assert is_cancel_requested("t2") is False
