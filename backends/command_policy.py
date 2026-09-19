"""Unified command classification for local and OpenSandbox execution."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from config.approval_config import preference_for

PolicyAction = Literal["allow", "approval", "deny"]


@dataclass(frozen=True)
class CommandPolicyResult:
    action: PolicyAction
    category: str
    reason: str


_HARD_DENY: tuple[tuple[str, str, str], ...] = (
    (r"(?:^|\s)(?:sudo|runas|pkexec)(?:\s|$)", "privilege_escalation", "禁止提升系统权限"),
    (r"\b(?:format|diskpart|mkfs(?:\.[a-z0-9]+)?|fdisk|bcdedit)\b", "disk_management", "禁止修改磁盘或启动配置"),
    (r"\b(?:shutdown|reboot|restart-computer|stop-computer|set-executionpolicy)\b", "system_configuration", "禁止修改或关闭宿主系统"),
    (r"\breg\s+(?:delete|add|import|save|restore)\b", "system_configuration", "禁止修改系统注册表"),
    (r"\bgit\s+(?:push\b[^\r\n]*(?:--force|-f\b)|reset\s+--hard|clean\s+-[^\r\n]*f)", "git_destructive", "禁止强制推送或破坏性 Git 清理"),
    (r"(?:^|[\\/])(?:\.env|\.ssh|\.aws|\.gnupg|\.secrets)(?:[\\/\s]|$)", "protected_secrets", "禁止读取或传递受保护的密钥文件"),
    (r"\b(?:docker|podman)\b[^\r\n]*(?:--privileged|/var/run/docker\.sock)", "sandbox_escape", "禁止使用特权容器或宿主 Docker 套接字"),
    (r"\b(?:setx|export)\b[^\r\n]*(?:token|secret|password|api[_-]?key)", "credential_injection", "禁止通过命令注入凭据"),
)

_EXTERNAL_WRITE = re.compile(
    r"^(?:git\s+push\b(?![^\r\n]*(?:--force|-f\b))|"
    r"(?:curl|wget|http|invoke-webrequest)\b[^\r\n]*(?:\s-X\s*(?:POST|PUT|PATCH|DELETE)|--data(?:-raw|-binary)?\b|-d\s))",
    re.IGNORECASE,
)

_CATEGORY_PATTERNS: tuple[tuple[str, str], ...] = (
    ("delete_workspace_files", r"^(?:rm|del|erase|rmdir|remove-item)\b"),
    ("modify_workspace_files", r"^(?:cp|copy|xcopy|robocopy|mv|move|ren|rename|mkdir|md|touch|new-item)\b"),
    ("install_dependencies", r"^(?:(?:python(?:3)?\s+-m\s+)?pip(?:3)?\s+(?:install|uninstall)|npm\s+(?:install|i|uninstall|ci)|pnpm\s+(?:install|add|remove)|yarn\s+(?:install|add|remove))\b"),
    ("run_tests", r"^(?:pytest\b|python(?:3)?\s+-m\s+pytest\b|npm\s+(?:test|run\s+test)\b|pnpm\s+test\b|yarn\s+test\b)"),
    # Maven/Gradle commands cover both dependency inspection and project builds.
    # They are still governed by the user's backend preference for run_build.
    ("run_build", r"^(?:mvn|mvnw|gradle|gradlew)\b|^(?:ruff|mypy|flake8|black|isort|tsc|vite|webpack|rollup)\b|^(?:npm|pnpm|yarn)\s+(?:run\s+)?(?:build|lint|format|typecheck)\b"),
    ("run_project_script", r"^(?:python(?:3)?\s+(?!-m\s+(?:pip|pytest)\b).+|node\s+.+|(?:npm|pnpm|yarn)\s+run\s+.+)$"),
    ("git_commit", r"^git\s+(?:add|commit|merge|rebase|cherry-pick|revert|stash)\b"),
    ("git_branch_change", r"^git\s+(?:checkout|switch|pull|fetch|branch|tag)\b"),
    ("network_read", r"^(?:curl|wget|http|invoke-webrequest|ping|tracert|nslookup|dig)\b"),
    ("process_management", r"^(?:ps|tasklist|get-process|start-process|kill|taskkill|stop-process)\b"),
)

_ALWAYS_ALLOW = re.compile(
    r"^(?:git\s+(?:status|log|diff|show|ls-files|rev-parse|describe|remote\s+-v)\b|"
    r"dir\b|ls\b|tree\b|type\b|cat\b|more\b|less\b|head\b|tail\b|"
    r"echo\b|pwd\b|cd\b|whoami\b|hostname\b|date\b|time\b|"
    r"find\b|grep\b|rg\b|where\b|which\b|"
    r"(?:python(?:3)?\s+-m\s+)?pip(?:3)?\s+(?:list|show|freeze|check)\b)",
    re.IGNORECASE,
)


_GIT_GLOBAL_OPTION = re.compile(
    r"""
    ^git\s+
    (?:
        (?:-C|-c|--git-dir|--work-tree|--namespace|--exec-path|--config-env)
        (?:\s*=\s*|\s+)\S+\s+
      |
        --(?:no-pager|paginate|bare|no-replace-objects|literal-pathspecs|
             glob-pathspecs|noglob-pathspecs|icase-pathspecs|no-optional-locks)\s+
      |
        -[pP]\s+
    )*
    """,
    re.IGNORECASE | re.VERBOSE,
)
"""``git`` 与子命令之间的全局选项。

git 允许把全局选项写在子命令前面（``git -C <路径> status``、``git --no-pager diff``），
而下面所有规则都锚定在 ``git`` 后面紧跟子命令。不先剥掉这些选项，匹配就会失配：
只读命令掉进「未识别 → 需要人工确认」（无人值守的循环会停在这里），
破坏性命令则**绕过硬拒绝**。
"""


def _parts(command: str) -> list[str]:
    """Split common compound shell forms so every segment is classified."""
    return [part.strip() for part in re.split(r"\s*(?:&&|\|\||;|\|)\s*", command) if part.strip()]


def _strip_git_global_options(part: str) -> str:
    """剥掉 ``git`` 后面的全局选项，只留下子命令供匹配。

    ``git -C <路径> status`` 与 ``git status`` 是同一条只读命令；
    ``git -C <路径> push --force`` 与 ``git push --force`` 同样是破坏性的。
    两者必须得到同样的判定。

    Args:
        part: 一条简单命令（已经按 ``&&`` / ``|`` / ``;`` 拆开）。

    Returns:
        剥掉全局选项后的命令；不是 git 命令时原样返回。
    """
    if not part.lower().startswith("git"):
        return part
    return _GIT_GLOBAL_OPTION.sub("git ", part)


def evaluate_command(command: str, backend_type: str) -> CommandPolicyResult:
    """Apply immutable rules first, then preferences and backend defaults."""
    normalized = command.strip()
    if not normalized:
        return CommandPolicyResult("deny", "invalid", "禁止执行空命令")

    for pattern, category, reason in _HARD_DENY:
        if re.search(pattern, normalized, re.IGNORECASE):
            return CommandPolicyResult("deny", category, reason)

    if re.search(r"(?<!\d)>{1,2}(?!&1)", normalized):
        action = preference_for(backend_type, "modify_workspace_files")
        return CommandPolicyResult(action, "modify_workspace_files", "输出重定向会修改工作区文件")

    parts = _parts(normalized)

    # 硬拒绝规则要**再对着剥掉全局选项的每一段**跑一遍。只对整串跑的话，
    # ``git -C <路径> push --force`` 里 ``git`` 与 ``push`` 之间插了东西就匹配不上，
    # 破坏性命令会从 deny 掉到「未识别 → 需要人工确认」。
    for pattern, category, reason in _HARD_DENY:
        if any(
            re.search(pattern, _strip_git_global_options(part), re.IGNORECASE)
            for part in parts
        ):
            return CommandPolicyResult("deny", category, reason)

    decisions: list[CommandPolicyResult] = []
    for part in parts:
        part = _strip_git_global_options(part)
        if _EXTERNAL_WRITE.search(part):
            decisions.append(CommandPolicyResult("approval", "external_write", "向外部服务写入数据始终需要审批"))
            continue
        if _ALWAYS_ALLOW.search(part):
            decisions.append(CommandPolicyResult("allow", "read_only", "只读检查"))
            continue
        matched = False
        for category, pattern in _CATEGORY_PATTERNS:
            if re.search(pattern, part, re.IGNORECASE):
                action = preference_for(backend_type, category)
                decisions.append(CommandPolicyResult(action, category, "根据当前执行环境和审批设置处理"))
                matched = True
                break
        if not matched:
            decisions.append(CommandPolicyResult("approval", "unknown", "未识别的命令需要人工确认"))

    if any(item.action == "deny" for item in decisions):
        return next(item for item in decisions if item.action == "deny")
    if any(item.action == "approval" for item in decisions):
        item = next(item for item in decisions if item.action == "approval")
        return CommandPolicyResult("approval", item.category, item.reason)
    # Compound commands commonly start with ``cd``.  Preserve the meaningful
    # category of the actual operation instead of reporting the navigation step
    # as ``read_only``.
    chosen = next((item for item in reversed(decisions) if item.category != "read_only"), decisions[0])
    return CommandPolicyResult("allow", chosen.category, chosen.reason)


def command_requires_approval(command: str, backend_type: str) -> bool:
    return evaluate_command(command, backend_type).action == "approval"
