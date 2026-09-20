"""命令审批策略的测试。

这块此前**一条测试都没有**，所以下面这个洞活了很久：所有规则都锚定 ``git`` 后面紧跟
子命令，而 git 允许把全局选项写在前面。后果是两头的 ——

- ``git -C <路径> status`` 匹配不上只读白名单，掉进「未识别 → 需要人工确认」。
  无人值守的自动化循环会停在这里等一个永远不会有人点的确认；
- ``git -C <路径> push --force`` 匹配不上硬拒绝，从 ``deny`` 掉到「未识别 → 审批」。

核心不变量：**在 ``git`` 后面插全局选项，不改变判定结果。** 下面用「带选项 / 不带选项」
成对比较来钉住它 —— 这样无论偏好怎么配，比较都成立。
"""

from __future__ import annotations

import pytest

from backends.command_policy import evaluate_command

BACKENDS = ("local", "sandbox")


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(
    ("plain", "with_options"),
    [
        ("git status", "git -C xihe-agent-test status"),
        ("git status", "git --no-pager status"),
        ("git log --oneline -5", "git -C xihe-agent-test log --oneline -5"),
        ("git diff", "git -C /e/ai_workspace/projects/xihe-agent-test diff"),
        ("git diff --stat", "git -C xihe-agent-test --no-pager diff --stat"),
        ("git show HEAD", "git --git-dir=/x/.git show HEAD"),
        ("git rev-parse HEAD", "git -C xihe-agent-test rev-parse HEAD"),
        ("git ls-files", "git -C xihe-agent-test ls-files"),
        ("git remote -v", "git -C xihe-agent-test remote -v"),
        ("git push origin main", "git -C xihe-agent-test push origin main"),
        ("git commit -m x", "git -C xihe-agent-test commit -m x"),
        ("git checkout main", "git -c advice.detachedHead=false checkout main"),
        ("git push --force origin main", "git -C xihe-agent-test push --force origin main"),
        ("git reset --hard", "git -C xihe-agent-test reset --hard"),
        ("git clean -fd", "git -c core.pager=cat clean -fd"),
        ("git clean -fd", "git --no-pager clean -fd"),
    ],
)
def test_git_global_options_do_not_change_the_verdict(
    plain: str, with_options: str, backend: str, data_root: object
) -> None:
    """同一个子命令，前面插不插全局选项，判定必须一致。"""
    before = evaluate_command(plain, backend)
    after = evaluate_command(with_options, backend)
    assert (after.action, after.category) == (before.action, before.category)


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(
    "command",
    [
        "git -C xihe-agent-test status",
        "git -C xihe-agent-test log --oneline -10",
        "git --no-pager diff",
        "git -C /e/ai_workspace/projects/xihe-agent-test rev-parse HEAD",
        "git --git-dir=/x/.git ls-files",
    ],
)
def test_read_only_git_with_options_is_allowed(
    command: str, backend: str, data_root: object
) -> None:
    """只读的 git 命令要直接放行，不能掉进「未识别」。

    这一条是自动化循环能不能无人值守跑下去的关键：掉进「未识别」就会停在等人工确认。
    """
    result = evaluate_command(command, backend)
    assert result.action == "allow", f"{command} 被判成 {result.action}（{result.category}）"


@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(
    "command",
    [
        "git -C xihe-agent-test push --force origin main",
        "git -C xihe-agent-test reset --hard",
        "git -C xihe-agent-test clean -fd",
        "git --no-pager reset --hard HEAD~1",
        "git -c core.pager=cat clean -fdx",
    ],
)
def test_destructive_git_with_options_is_still_denied(
    command: str, backend: str, data_root: object
) -> None:
    """破坏性的 git 命令插了全局选项也必须被硬拒绝。

    这是那个绕过的回归测试：修之前这些命令只落到「未识别 → 审批」，
    审批是可以被点过去的，硬拒绝不行。
    """
    result = evaluate_command(command, backend)
    assert result.action == "deny", f"{command} 被判成 {result.action}（{result.category}）"
    assert result.category == "git_destructive"


@pytest.mark.parametrize("backend", BACKENDS)
def test_unrecognized_commands_still_need_approval(
    backend: str, data_root: object
) -> None:
    """剥全局选项不能把「未识别」也一起放过去。"""
    for command in ("git -C xihe-agent-test frobnicate", "some-unknown-tool --go"):
        assert evaluate_command(command, backend).action == "approval"


@pytest.mark.parametrize("backend", BACKENDS)
def test_compound_command_takes_the_strictest_segment(
    backend: str, data_root: object
) -> None:
    """复合命令里只要有一段要审批或该拒绝，整条就按那段算。"""
    assert (
        evaluate_command("git -C xihe-agent-test status && ls -la", backend).action
        == "allow"
    )
    assert (
        evaluate_command(
            "git -C xihe-agent-test status && git -C xihe-agent-test push origin main",
            backend,
        ).action
        == "approval"
    )
    assert (
        evaluate_command(
            "git -C xihe-agent-test status && git -C xihe-agent-test reset --hard",
            backend,
        ).action
        == "deny"
    )


@pytest.mark.parametrize("backend", BACKENDS)
def test_plain_commands_are_untouched(backend: str, data_root: object) -> None:
    """没有全局选项时，基本盘行为与改动前一致。"""
    assert evaluate_command("git status", backend).action == "allow"
    assert evaluate_command("ls -la", backend).action == "allow"
    assert evaluate_command("", backend).action == "deny"
    assert evaluate_command("sudo rm -rf /", backend).action == "deny"


@pytest.mark.parametrize("backend", BACKENDS)
def test_maven_dependency_commands_use_build_policy(
    backend: str, data_root: object
) -> None:
    """Maven 依赖检查不能因未识别而无条件落到人工审批。"""
    result = evaluate_command(
        "cd /workspace/projects/xihe-agent-test; mvn -B dependency:list "
        "-DincludeScope=runtime -DoutputFile=/tmp/deps.txt",
        backend,
    )
    assert result.action == ("approval" if backend == "local" else "allow")
    assert result.category == "run_build"
