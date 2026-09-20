"""验证门的测试。

重点覆盖四条安全性质：

1. **没配置检查项时结论是不通过**，不是通过；
2. **需要审批的命令在无人值守时不会被偷偷执行**（策略判 ``deny`` 与 ``approval``
   都记为跳过，执行器一次都不该被调用）；
3. 执行器抛异常、命令超时都不能把循环带崩，只记为一条「执行出错」；
4. **「没跑成」不能报成「没通过」**：一条检查都没执行时，结论、反馈与落盘字段都
   要说清是「没能验」，因为默认配置下每一轮都是这种情形。

用例里的审批结论依赖 ``LOCAL_DEFAULTS`` / ``SANDBOX_DEFAULTS``，所以必须把
``Settings.data_dir`` 指向临时目录，避免开发机上真实的 ``command_approval.json``
覆盖默认值把测试结果改掉。
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from services.automation_verification import (
    MAX_OUTPUT_CHARS,
    Check,
    VerificationResult,
    parse_checks,
    run_checks,
    unverified,
)

ALLOWED_COMMAND = "git status"
"""命中「永远允许」名单，任何 backend 下都是 ``allow``。"""

TEST_COMMAND = "pytest tests/"
"""本地默认 ``run_tests=allow``，用来当「会真正执行」的命令。"""

LOCAL_ONLY_APPROVAL = "ruff check ."
"""本地默认 ``run_build=approval``、沙箱默认 ``allow``，用来验证 backend_type 真的生效。"""

DENIED_COMMAND = "sudo rm -rf /"
"""命中硬性拒绝规则。"""

UNKNOWN_COMMAND = "frobnicate --now"
"""未识别命令，策略一律判需要审批。"""


@pytest.fixture(autouse=True)
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """把 ``Settings.data_dir`` 指向临时目录，隔离审批设置。

    **故意设成 autouse**：模块开头就写了「用例里的审批结论依赖
    ``LOCAL_DEFAULTS`` / ``SANDBOX_DEFAULTS``，所以必须把 ``Settings.data_dir``
    指向临时目录」，但夹具只定义、没人请求，于是开发机上真实的
    ``data/command_approval.json`` 会漏进来 —— 它把 ``local.run_build`` 改成
    ``allow`` 之后，``ruff check .`` 就从「需要审批」变成「直接执行」，
    ``test_backend_type_actually_changes_the_decision`` 因此一直失败。
    改成 autouse 是为了让以后新增的用例也不会漏掉这一步。

    Returns:
        临时的 data 目录。
    """
    from config.app_config import get_settings

    target = tmp_path / "data"
    monkeypatch.setattr(get_settings(), "data_dir", target)
    return target


def make_runner(
    results: dict[str, Any] | None = None,
) -> tuple[Callable[[str, int], tuple[int, str]], list[str]]:
    """造一个记录调用的假执行器。

    Args:
        results: 命令到 ``(退出码, 输出)`` 的映射；值也可以是异常实例，用来
            模拟执行器抛错。未列出的命令默认成功且无输出。

    Returns:
        ``(执行器, 被调用的命令列表)``。
    """
    calls: list[str] = []
    table = results or {}

    def run(command: str, timeout_seconds: int) -> tuple[int, str]:
        calls.append(command)
        value = table.get(command, (0, ""))
        if isinstance(value, BaseException):
            raise value
        return value

    return run, calls


def test_all_checks_pass() -> None:
    """全部通过时结论是通过。"""
    runner, calls = make_runner()
    result = run_checks(
        (Check(command=ALLOWED_COMMAND), Check(command=TEST_COMMAND)),
        runner=runner,
    )
    assert result.passed is True
    assert result.summary == "2 项检查全部通过"
    assert calls == [ALLOWED_COMMAND, TEST_COMMAND]


def test_one_failure_fails_the_whole_verification() -> None:
    """只要有一项没过，整体就是不通过。"""
    runner, _ = make_runner({TEST_COMMAND: (1, "1 failed, 3 passed")})
    result = run_checks(
        (Check(command=ALLOWED_COMMAND), Check(command=TEST_COMMAND)), runner=runner
    )
    assert result.passed is False
    assert result.executed is True
    assert result.summary == "1 项未通过（共 2 项）"


def test_no_checks_is_not_a_pass() -> None:
    """没配置检查项 ≠ 检查通过，这是这套东西最容易自欺的地方。"""
    result = run_checks(())
    assert result.passed is False
    assert result.summary == "没有配置检查项，无法确认结果"


def test_skipped_check_prevents_a_pass() -> None:
    """被跳过的检查项算「没确认」，不能因为别的项过了就整体通过。"""
    runner, _ = make_runner()
    result = run_checks(
        (Check(command=ALLOWED_COMMAND), Check(command=UNKNOWN_COMMAND)), runner=runner
    )
    assert result.passed is False


def test_denied_command_is_skipped_and_never_executed() -> None:
    """策略判 deny 的命令不执行，记为跳过并说明原因。"""
    runner, calls = make_runner()
    result = run_checks((Check(command=DENIED_COMMAND),), runner=runner)
    outcome = result.outcomes[0]
    assert outcome.status == "skipped"
    assert "审批策略禁止执行" in outcome.note
    assert calls == []


def test_approval_command_is_skipped_when_unattended() -> None:
    """需要审批的命令在无人值守时也不执行 —— 静默跑掉就等于绕过审批。"""
    runner, calls = make_runner()
    result = run_checks((Check(command=UNKNOWN_COMMAND),), runner=runner)
    outcome = result.outcomes[0]
    assert outcome.status == "skipped"
    assert "人工审批" in outcome.note
    assert calls == []


def test_backend_type_actually_changes_the_decision() -> None:
    """同一条命令在本地要审批、在沙箱里允许 —— 证明 backend_type 真的传下去了。"""
    local_runner, local_calls = make_runner()
    local = run_checks(
        (Check(command=LOCAL_ONLY_APPROVAL),), backend_type="local", runner=local_runner
    )
    assert local.outcomes[0].status == "skipped"
    assert local_calls == []

    sandbox_runner, sandbox_calls = make_runner()
    sandbox = run_checks(
        (Check(command=LOCAL_ONLY_APPROVAL),), backend_type="sandbox", runner=sandbox_runner
    )
    assert sandbox.outcomes[0].status == "passed"
    assert sandbox_calls == [LOCAL_ONLY_APPROVAL]


def test_runner_exception_becomes_error_instead_of_crashing() -> None:
    """执行器抛异常只记一条「执行出错」，不能让循环崩掉。"""
    runner, _ = make_runner({TEST_COMMAND: OSError("找不到可执行文件")})
    result = run_checks((Check(command=TEST_COMMAND),), runner=runner)
    outcome = result.outcomes[0]
    assert outcome.status == "error"
    assert "OSError" in outcome.note
    assert result.passed is False


def test_timeout_becomes_error_with_the_limit_in_the_note() -> None:
    """超时同样只记一条出错，并把超时时间写清楚。"""
    runner, _ = make_runner(
        {TEST_COMMAND: subprocess.TimeoutExpired(cmd=TEST_COMMAND, timeout=7)}
    )
    result = run_checks((Check(command=TEST_COMMAND, timeout_seconds=7),), runner=runner)
    outcome = result.outcomes[0]
    assert outcome.status == "error"
    assert "7" in outcome.note


def test_every_check_runs_even_after_a_failure() -> None:
    """不在第一条失败时就中断 —— 一次拿全反馈，下一轮才能一次改到位。"""
    runner, calls = make_runner({ALLOWED_COMMAND: (1, "boom")})
    run_checks(
        (Check(command=ALLOWED_COMMAND), Check(command=TEST_COMMAND)), runner=runner
    )
    assert calls == [ALLOWED_COMMAND, TEST_COMMAND]


def test_long_output_keeps_the_tail() -> None:
    """输出超长时保留末尾 —— 报错和失败断言都在最后。"""
    runner, _ = make_runner({TEST_COMMAND: (1, "开" * 2000 + "真正有用的报错")})
    result = run_checks((Check(command=TEST_COMMAND),), runner=runner)
    output = result.outcomes[0].output
    assert "真正有用的报错" in output
    assert "已省略" in output
    assert len(output) < MAX_OUTPUT_CHARS + 100


def test_short_output_is_kept_whole() -> None:
    """输出没超长时原样保留，不加省略提示。"""
    runner, _ = make_runner({TEST_COMMAND: (1, "简短报错")})
    result = run_checks((Check(command=TEST_COMMAND),), runner=runner)
    assert result.outcomes[0].output == "简短报错"


def test_feedback_only_mentions_unconfirmed_checks() -> None:
    """反馈里只写没过、没跑、出错的项，通过的项不必再提。"""
    runner, _ = make_runner({TEST_COMMAND: (1, "1 failed")})
    result = run_checks(
        (Check(command=ALLOWED_COMMAND), Check(command=TEST_COMMAND)), runner=runner
    )
    feedback = result.feedback()
    assert TEST_COMMAND in feedback
    assert "退出码 1" in feedback
    assert "1 failed" in feedback
    assert ALLOWED_COMMAND not in feedback


def test_feedback_gives_no_repair_advice() -> None:
    """反馈只陈述事实，不替下一轮编修复建议。"""
    runner, _ = make_runner({TEST_COMMAND: (1, "1 failed")})
    feedback = run_checks((Check(command=TEST_COMMAND),), runner=runner).feedback()
    assert "建议" not in feedback


def test_state_dict_carries_conclusions_not_output() -> None:
    """写进循环状态的只有结论，不带测试输出正文。"""
    runner, _ = make_runner({TEST_COMMAND: (1, "一大段测试输出")})
    payload = run_checks(
        (Check(command=TEST_COMMAND, description="跑测试"),), runner=runner
    ).as_state_dict()

    assert payload["passed"] is False
    assert payload["executed"] is True
    assert payload["summary"] == "1 项未通过（共 1 项）"
    assert payload["checks"] == [
        {
            "command": TEST_COMMAND,
            "description": "跑测试",
            "status": "failed",
            "exitCode": 1,
        }
    ]
    assert "一大段测试输出" not in str(payload)


def test_empty_result_has_no_checks_in_state_dict() -> None:
    """空结果写进状态时 ``checks`` 是空列表，不能变成缺键。"""
    payload = VerificationResult(()).as_state_dict()
    assert payload["passed"] is False
    assert payload["executed"] is False
    assert payload["checks"] == []


def test_nothing_ran_is_not_reported_as_a_failure() -> None:
    """一条都没跑成时说「没有执行」，不能说「未通过」。

    「没能验证」和「验证没通过」是两件事：前者该去修环境，后者该去改代码。
    说成一样的话，下一轮会去修一个从没运行过的东西。
    """
    runner, _ = make_runner()
    result = run_checks((Check(command=UNKNOWN_COMMAND),), runner=runner)

    assert result.passed is False
    assert result.executed is False
    assert "未通过" not in result.summary
    assert "没有执行" in result.summary


def test_a_check_that_blew_up_is_also_not_a_verdict() -> None:
    """执行出错也算「没跑成」—— 它同样说明不了活儿干得怎么样。"""
    runner, _ = make_runner({TEST_COMMAND: OSError("找不到可执行文件")})
    result = run_checks((Check(command=TEST_COMMAND),), runner=runner)

    assert result.executed is False
    assert "未通过" not in result.summary


def test_summary_separates_the_kinds_of_not_passing() -> None:
    """跑过没过、跑起来出错、压根没跑，三种分开数。"""
    runner, _ = make_runner({TEST_COMMAND: (1, "1 failed")})
    result = run_checks(
        (Check(command=TEST_COMMAND), Check(command=UNKNOWN_COMMAND)), runner=runner
    )

    assert result.executed is True
    assert result.passed is False
    assert result.summary == "1 项未通过、1 项未执行（共 2 项）"


def test_nothing_ran_but_passed_checks_still_counts_as_executed() -> None:
    """有项目真跑过就轮到验证下判断，哪怕别的项被跳过。"""
    runner, _ = make_runner()
    result = run_checks(
        (Check(command=ALLOWED_COMMAND), Check(command=UNKNOWN_COMMAND)), runner=runner
    )
    assert result.executed is True
    assert result.summary == "1 项未执行（共 2 项）"


def test_summary_carries_the_reason_when_nothing_ran() -> None:
    """一条都没跑成时把原因带上 —— 否则看的人得自己翻每一条备注。"""
    result = unverified(
        "仓库在沙箱容器里，还没有接通容器内执行验证的通道",
        (Check(command=TEST_COMMAND),),
    )
    assert "沙箱" in result.summary


def test_feedback_does_not_claim_a_failure_when_nothing_ran() -> None:
    """反馈第一句不能写「验证未通过」—— 那会让下一轮去修没运行过的东西。"""
    result = unverified("说不清仓库在哪", (Check(command=TEST_COMMAND),))
    first_line = result.feedback().splitlines()[0]

    assert "未通过" not in first_line
    assert "说不清仓库在哪" in first_line
    # 原因还是要落在每一条上，反馈里翻得到。
    assert "未执行" in result.feedback()


def test_parse_checks_accepts_plain_strings() -> None:
    """条目可以只写一条命令。"""
    checks = parse_checks(["pytest tests/", "ruff check ."])
    assert [item.command for item in checks] == ["pytest tests/", "ruff check ."]
    assert all(item.timeout_seconds > 0 for item in checks)


def test_parse_checks_accepts_dicts_with_options() -> None:
    """条目也可以带上说明和超时。"""
    checks = parse_checks(
        [{"command": "pytest tests/", "description": "跑测试", "timeoutSeconds": 30}]
    )
    assert checks[0].description == "跑测试"
    assert checks[0].timeout_seconds == 30


def test_parse_checks_accepts_a_single_entry() -> None:
    """只配一条时不必再包一层列表。"""
    assert len(parse_checks("pytest tests/")) == 1
    assert len(parse_checks({"command": "pytest tests/"})) == 1


def test_parse_checks_none_is_empty() -> None:
    """没配检查项时返回空元组，由调用方去面对「无法确认」这个结论。"""
    assert parse_checks(None) == ()


def test_parse_checks_rejects_empty_command() -> None:
    """空命令要报错，不能静默丢掉 —— 少一条检查会让「通过」不可信。"""
    with pytest.raises(ValueError, match="命令不能为空"):
        parse_checks(["   "])


def test_parse_checks_rejects_bad_timeout() -> None:
    """超时必须是正整数。"""
    with pytest.raises(ValueError, match="超时时间必须是整数"):
        parse_checks([{"command": "pytest", "timeoutSeconds": "很久"}])
    with pytest.raises(ValueError, match="超时时间必须大于 0"):
        parse_checks([{"command": "pytest", "timeoutSeconds": 0}])


def test_parse_checks_rejects_wrong_shapes() -> None:
    """整体不是列表、条目类型不对，都要报错并指出第几项。"""
    with pytest.raises(ValueError, match="必须是一个列表"):
        parse_checks(123)
    with pytest.raises(ValueError, match="第 2 项检查必须是字符串或字典"):
        parse_checks(["pytest tests/", 42])


def test_real_local_runner_reports_exit_code(tmp_path: Path) -> None:
    """默认执行器要能真的跑命令并拿到退出码与输出。"""
    from services.automation_verification import _make_local_runner

    runner = _make_local_runner(tmp_path)
    code, output = runner("echo hello", 30)
    assert code == 0
    assert "hello" in output


def _fake_sandbox(responses: dict[str, tuple[int | None, str]]) -> Any:
    """造一个假沙箱后端，用来检查容器执行器的退出码映射。"""
    from types import SimpleNamespace

    calls: list[str] = []

    class FakeBackend:
        def execute(self, command: str, *, timeout: int | None = None) -> Any:
            calls.append(command)
            code, output = responses.get(command, (0, ""))
            return SimpleNamespace(exit_code=code, output=output)

    return SimpleNamespace(backend=FakeBackend(), calls=calls)


def test_container_runner_prefixes_the_repo_directory() -> None:
    """命令要在容器内的仓库目录里跑：沙箱的 execute 没有工作目录参数。"""
    from services.automation_verification import ContainerExec, make_container_runner

    fake = _fake_sandbox({})
    execute = make_container_runner(
        ContainerExec(backend=fake.backend, repo_dir="/projects/r")
    )

    execute("pytest -q", 60)

    assert fake.calls == ["cd /projects/r && pytest -q"]


def test_container_runner_keeps_the_exit_code_and_output() -> None:
    """正常返回时把退出码与输出原样交给验证门。"""
    from types import SimpleNamespace

    from services.automation_verification import ContainerExec, make_container_runner

    class FakeBackend:
        def execute(self, command: str, *, timeout: int | None = None) -> Any:
            return SimpleNamespace(exit_code=2, output="1 failed")

    execute = make_container_runner(ContainerExec(backend=FakeBackend(), repo_dir="."))

    assert execute("pytest -q", 60) == (2, "1 failed")


def test_container_timeout_is_not_reported_as_a_failed_check() -> None:
    """超时的协议退出码要转成超时异常，不能记成「验证没通过」。"""
    from services.automation_verification import ContainerExec, make_container_runner

    fake = _fake_sandbox({"cd . && pytest -q": (124, "命令在 60 秒后超时")})
    execute = make_container_runner(ContainerExec(backend=fake.backend, repo_dir="."))

    with pytest.raises(subprocess.TimeoutExpired):
        execute("pytest -q", 60)


def test_container_rejection_is_not_reported_as_a_failed_check() -> None:
    """沙箱自己拒掉的协议退出码同样要转成异常。"""
    from services.automation_verification import ContainerExec, make_container_runner

    fake = _fake_sandbox({"cd . && pytest -q": (126, "命令被拒绝：禁止执行空命令")})
    execute = make_container_runner(ContainerExec(backend=fake.backend, repo_dir="."))

    with pytest.raises(RuntimeError, match="拒绝"):
        execute("pytest -q", 60)


def test_container_transport_error_is_not_reported_as_a_failed_check() -> None:
    """沙箱连不上时返回的是退出码 1 + 固定前缀，必须与「命令返回 1」分开。

    分不开的后果是把「没能验证」记成「验证没通过」—— 下一轮会去修一个根本没跑过
    的东西，正是本模块最忌讳的那件事。
    """
    from services.automation_verification import ContainerExec, make_container_runner

    fake = _fake_sandbox({"cd . && pytest -q": (1, "执行命令时出错：connection refused")})
    execute = make_container_runner(ContainerExec(backend=fake.backend, repo_dir="."))

    with pytest.raises(RuntimeError, match="没有执行"):
        execute("pytest -q", 60)


def test_a_real_exit_code_one_is_still_a_failed_check() -> None:
    """命令自己返回 1（测试真的没过）要照常记成未通过。"""
    from services.automation_verification import ContainerExec, make_container_runner

    fake = _fake_sandbox({"cd . && pytest -q": (1, "1 failed")})
    execute = make_container_runner(ContainerExec(backend=fake.backend, repo_dir="."))

    assert execute("pytest -q", 60) == (1, "1 failed")


def test_a_container_timeout_becomes_a_timeout_note() -> None:
    """走完整条链路时，容器超时在结论里是「执行出错」而不是「未通过」。"""
    from services.automation_verification import ContainerExec, make_container_runner

    fake = _fake_sandbox({f"cd . && {TEST_COMMAND}": (124, "")})
    result = run_checks(
        (Check(command=TEST_COMMAND),),
        backend_type="sandbox",
        runner=make_container_runner(ContainerExec(backend=fake.backend, repo_dir=".")),
    )

    assert result.outcomes[0].status == "error"
    assert "秒仍未结束" in result.outcomes[0].note


def test_a_missing_exit_code_is_never_assumed_to_be_zero() -> None:
    """沙箱没回退出码时绝不能补 0 —— 补 0 等于替它宣布检查通过。"""
    from types import SimpleNamespace

    from services.automation_verification import ContainerExec, make_container_runner

    class FakeBackend:
        def execute(self, command: str, *, timeout: int | None = None) -> Any:
            return SimpleNamespace(output="没有任何退出码")

    execute = make_container_runner(ContainerExec(backend=FakeBackend(), repo_dir="."))

    with pytest.raises(RuntimeError, match="没有返回退出码"):
        execute("pytest -q", 60)
