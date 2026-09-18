"""把循环接到真实 Agent 运行时上的测试。

这里能测的是**判断**，不是执行 —— 真正跑一轮需要模型和数据库。所以覆盖的是：

1. :func:`services.automation_runner.read_only_refusal` 的放行与拒绝边界；
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
    _outcome_from_status,
    _tokens_used,
    make_agent_round_runner,
    read_only_refusal,
    resolve_backend_type,
    resolve_workspace,
)

ALL_KINDS = ("coding", "planning", "analysis", "qa", "sync", "inspect", "review")
WRITE_KINDS = ("coding", "planning")


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
    """只读类任务放行。"""
    _stub_kind(monkeypatch, kind)
    assert read_only_refusal("随便一句指令") is None


@pytest.mark.parametrize("kind", WRITE_KINDS)
def test_write_kinds_are_refused(monkeypatch: pytest.MonkeyPatch, kind: str) -> None:
    """会改代码或需要等确认的任务拒掉，理由要说清是为什么。"""
    _stub_kind(monkeypatch, kind)
    refusal = read_only_refusal("随便一句指令")
    assert refusal is not None
    assert "只读任务" in refusal
    assert "方案确认" in refusal


def test_refusal_names_the_recognized_kind(monkeypatch: pytest.MonkeyPatch) -> None:
    """理由里要写清被识别成了哪一类，否则用户不知道该怎么改。"""
    _stub_kind(monkeypatch, "coding")
    assert "开发实现" in str(read_only_refusal("随便一句指令"))
    _stub_kind(monkeypatch, "planning")
    assert "方案规划" in str(read_only_refusal("随便一句指令"))


@pytest.mark.parametrize("kind", WRITE_KINDS)
def test_refusal_does_not_leak_the_internal_code(
    monkeypatch: pytest.MonkeyPatch, kind: str
) -> None:
    """给人看的理由里不能出现 ``coding`` 这种内部取值。"""
    _stub_kind(monkeypatch, kind)
    assert kind not in str(read_only_refusal("随便一句指令"))


def test_refusal_judges_the_instruction_it_was_given(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """判定必须拿调用方给的那一句，不能自己另取。"""
    calls = _stub_kind(monkeypatch, "analysis")
    read_only_refusal("分析这个仓库的目录结构")
    assert calls == ["分析这个仓库的目录结构"]


def test_every_known_kind_is_either_allowed_or_refused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """七种类型全都要有明确处置，不能有漏网的。"""
    from agent.runtime.task_router import TASK_KIND_LABELS

    assert set(ALL_KINDS) == set(TASK_KIND_LABELS)
    for kind in ALL_KINDS:
        _stub_kind(monkeypatch, kind)
        allowed = read_only_refusal("随便一句指令") is None
        assert allowed == (kind in READ_ONLY_KINDS)


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
    """超出只读范围时直接返回，不去建会话、不调模型。"""
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
    assert "只读任务" in result.summary
    assert result.thread_id is None


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
