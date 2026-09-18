"""自动化任务的验证门。

循环跑完一轮之后，要有人独立检查结果，不能让写代码的那个 agent 自己批改作业。
本模块就是那道检查，做三件事：

1. **先把每条命令过一遍审批策略**（:func:`backends.command_policy.evaluate_command`）。
   循环是无人在旁边看着跑的，所以：

   - 策略判 ``deny`` 的命令不执行；
   - 策略判 ``approval`` 的命令**也不执行** —— 无人值守时没有人能点「同意」，
     静默跑掉就等于绕过审批。

   两种都记为 ``skipped`` 并把原因写清楚，让看状态的人知道这一项没被真正检查。

2. **逐条执行并记录**，输出只保留**末尾**一段：报错、堆栈、失败断言都在最后，
   保留开头会把最有用的部分丢掉。

3. **给出一个机器可判定的结论**。没有配置任何检查项时结论是**不通过**，不是通过 ——
   「没检查」和「检查过了」是两件事。

Note:
    本模块不碰后端抽象：执行器由调用方以 ``runner`` 传入，将来应传一个走沙箱
    backend 的执行器。不传时退回本机 ``subprocess``，只适合本地开发与测试。

Note:
    检查命令来自配置，属于**不可信输入**。审批策略是第一道闸，:data:`MAX_OUTPUT_CHARS`
    截断是第二道 —— 防止一条命令把巨量输出灌进下一轮的上下文。
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from backends.command_policy import evaluate_command

CheckStatus = Literal["passed", "failed", "skipped", "error"]

Runner = Callable[[str, int], tuple[int, str]]
"""执行一条命令，返回 ``(退出码, 输出)``；无法执行时应抛异常。"""

DEFAULT_TIMEOUT_SECONDS = 300
"""单条检查的默认超时，避免一条卡住的命令把整个循环拖死。"""

MAX_OUTPUT_CHARS = 1200
"""单条检查保留的输出长度（取末尾），防止巨量输出灌进下一轮上下文。"""

MAX_CHECKS_KEPT = 20
"""写进循环状态时最多保留多少条检查结果。"""


@dataclass(frozen=True)
class Check:
    """一条检查项。"""

    command: str
    description: str = ""
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS


@dataclass(frozen=True)
class CheckOutcome:
    """一条检查项的执行结果。"""

    command: str
    description: str
    status: CheckStatus
    exit_code: int | None = None
    output: str = ""
    note: str = ""

    @property
    def ok(self) -> bool:
        """这一条是否确认通过。

        Returns:
            只有真正跑过且退出码为 0 才是 ``True``；跳过和出错都算未确认。
        """
        return self.status == "passed"


@dataclass(frozen=True)
class VerificationResult:
    """一次验证的整体结论。"""

    outcomes: tuple[CheckOutcome, ...]

    @property
    def passed(self) -> bool:
        """整体是否通过。

        没有检查项、或有任何一项没确认通过，都返回 ``False``。

        Returns:
            全部检查项都确认通过时返回 ``True``。
        """
        return bool(self.outcomes) and all(item.ok for item in self.outcomes)

    @property
    def summary(self) -> str:
        """一句话结论，用于写进循环状态。

        Returns:
            中文短句，例如 ``2 项检查全部通过`` 或 ``1 项检查未通过（共 3 项）``。
        """
        total = len(self.outcomes)
        if not total:
            return "没有配置检查项，无法确认结果"
        if self.passed:
            return f"{total} 项检查全部通过"
        bad = sum(1 for item in self.outcomes if not item.ok)
        return f"{bad} 项检查未通过（共 {total} 项）"

    def feedback(self) -> str:
        """拼出塞回下一轮的反馈文本。

        只陈述事实：哪一项没过、退出码多少、输出末尾是什么。**不给修复建议** ——
        该由下一轮的模型根据这些证据自己判断。

        Returns:
            多行纯文本。
        """
        lines = [f"验证未通过：{self.summary}"]
        for item in self.outcomes:
            if item.ok:
                continue
            label = item.description or item.command
            if item.status == "failed":
                lines.append(f"- 未通过：{label}（{item.command}，退出码 {item.exit_code}）")
                if item.output:
                    lines.append("  输出末尾：")
                    lines += [f"  {line}" for line in item.output.splitlines()]
            elif item.status == "skipped":
                lines.append(f"- 未执行：{label}（{item.command}）—— {item.note}")
            else:
                lines.append(f"- 执行出错：{label}（{item.command}）—— {item.note}")
        return "\n".join(lines)

    def as_state_dict(self) -> dict[str, Any]:
        """转成可以写进循环状态的字典。

        只带执行结论，不带输出正文 —— 状态文件要长期留着，不能把每次的
        测试输出都塞进去。

        Returns:
            含 ``passed`` / ``summary`` / ``checks`` 的字典。
        """
        return {
            "passed": self.passed,
            "summary": self.summary,
            "checks": [
                {
                    "command": item.command,
                    "description": item.description,
                    "status": item.status,
                    "exitCode": item.exit_code,
                }
                for item in self.outcomes[:MAX_CHECKS_KEPT]
            ],
        }


def _tail(text: Any, limit: int) -> str:
    """保留文本末尾的一段。

    Args:
        text: 原始文本，``None`` 视为空串。
        limit: 保留的最大字符数。

    Returns:
        末尾 ``limit`` 个字符；超长时在前面加一行省略提示。
    """
    content = str(text or "")
    if len(content) <= limit:
        return content
    return "...（前面还有 " + str(len(content) - limit) + " 个字符已省略）\n" + content[-limit:]


def parse_checks(raw: Any) -> tuple[Check, ...]:
    """把配置里的检查项解析成 :class:`Check`。

    条目可以是一句话（只有命令），也可以是带 ``description`` / ``timeoutSeconds``
    的字典。

    Args:
        raw: 配置里的检查项列表；``None`` 视为空。

    Returns:
        解析后的检查项元组。

    Raises:
        ValueError: 条目不是字符串或字典、命令为空、超时不是正整数。
            **宁可报错也不静默丢条目** —— 少一条检查会让「通过」变得不可信。
    """
    if raw is None:
        return ()
    if isinstance(raw, (str, dict)):
        raw = [raw]
    if not isinstance(raw, (list, tuple)):
        raise ValueError("检查项必须是一个列表")

    checks: list[Check] = []
    for index, item in enumerate(raw, start=1):
        if isinstance(item, str):
            command, description, timeout = item.strip(), "", DEFAULT_TIMEOUT_SECONDS
        elif isinstance(item, dict):
            command = str(item.get("command") or "").strip()
            description = str(item.get("description") or "").strip()
            raw_timeout = item.get("timeoutSeconds", DEFAULT_TIMEOUT_SECONDS)
            try:
                timeout = int(raw_timeout)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"第 {index} 项检查的超时时间必须是整数") from exc
        else:
            raise ValueError(f"第 {index} 项检查必须是字符串或字典")

        if not command:
            raise ValueError(f"第 {index} 项检查的命令不能为空")
        if timeout <= 0:
            raise ValueError(f"第 {index} 项检查的超时时间必须大于 0")
        checks.append(Check(command=command, description=description, timeout_seconds=timeout))
    return tuple(checks)


def _make_local_runner(workspace: Path | str | None) -> Runner:
    """构造一个在本机执行命令的执行器。

    Args:
        workspace: 命令的工作目录；``None`` 表示沿用当前目录。

    Returns:
        符合 :data:`Runner` 协议的可调用对象。
    """

    def run(command: str, timeout_seconds: int) -> tuple[int, str]:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=str(workspace) if workspace else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
        return completed.returncode, (completed.stdout or "") + (completed.stderr or "")

    return run


def unverified(reason: str, checks: Sequence[Check]) -> VerificationResult:
    """把一批检查项整体记成「没执行」。

    用于「说不清该在哪跑」这类情况：与其退回进程当前目录跑出一份与目标仓库无关的
    结论，不如明确记成没验证。结论仍然是不通过 —— 没确认过就不能算通过。

    Args:
        reason: 给人看的原因，会写进每一条的备注与反馈。
        checks: 原本要执行的检查项。

    Returns:
        全部记为 ``skipped`` 的结论。
    """
    return VerificationResult(
        outcomes=tuple(
            CheckOutcome(
                command=item.command,
                description=item.description,
                status="skipped",
                note=reason,
            )
            for item in checks
        )
    )


def run_checks(
    checks: Sequence[Check],
    *,
    backend_type: str = "local",
    workspace: Path | str | None = None,
    runner: Runner | None = None,
) -> VerificationResult:
    """逐条执行检查并给出结论。

    所有检查都会跑完再汇总，不在第一条失败时就中断 —— 一次拿全反馈，下一轮
    才能一次改到位。

    Args:
        checks: 检查项，通常来自 :func:`parse_checks`。
        backend_type: 执行环境，``"local"`` 或 ``"sandbox"``，用于审批策略判断。
        workspace: 命令的工作目录。
        runner: 执行器；不传时用本机 ``subprocess``。

    Returns:
        整体结论。
    """
    execute = runner or _make_local_runner(workspace)
    outcomes: list[CheckOutcome] = []

    for check in checks:
        policy = evaluate_command(check.command, backend_type)
        if policy.action == "deny":
            outcomes.append(
                CheckOutcome(
                    command=check.command,
                    description=check.description,
                    status="skipped",
                    note=f"审批策略禁止执行：{policy.reason}",
                )
            )
            continue
        if policy.action == "approval":
            outcomes.append(
                CheckOutcome(
                    command=check.command,
                    description=check.description,
                    status="skipped",
                    note=f"该命令需要人工审批，无人值守时不执行：{policy.reason}",
                )
            )
            continue

        try:
            exit_code, output = execute(check.command, check.timeout_seconds)
        except subprocess.TimeoutExpired:
            outcomes.append(
                CheckOutcome(
                    command=check.command,
                    description=check.description,
                    status="error",
                    note=f"超过 {check.timeout_seconds} 秒仍未结束",
                )
            )
            continue
        except Exception as exc:
            # 检查项来自配置，属于不可信输入；任何异常都不该把整个循环带崩。
            outcomes.append(
                CheckOutcome(
                    command=check.command,
                    description=check.description,
                    status="error",
                    note=f"检查没能执行：{type(exc).__name__}: {exc}",
                )
            )
            continue

        outcomes.append(
            CheckOutcome(
                command=check.command,
                description=check.description,
                status="passed" if exit_code == 0 else "failed",
                exit_code=exit_code,
                output=_tail(output, MAX_OUTPUT_CHARS),
            )
        )

    return VerificationResult(tuple(outcomes))
