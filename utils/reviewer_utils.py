"""PR Diff 解析与 finding 位置验证工具。

提供 unified diff 的结构化解析能力，提取变更文件和新增/修改行号，
供代码审查工具校验 finding 是否落在本次 diff 的真实变更范围内。

确定性 Python 解析比让模型从 diff 文本中猜行号更稳定，避免 position drift。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # 仅作类型注解使用（本文件已启用 from __future__ import annotations，注解为字符串惰性求值）。
    # 放在 TYPE_CHECKING 分支可避免 utils 基础层在运行时反向依赖 backends 上层包。
    from backends.local_shell import LocalShellBackend

_SAFE_GIT_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/\-]{0,180}$")


@dataclass(frozen=True)
class DiffFile:
    """单个变更文件的 diff 摘要。

    ``changed_lines`` 只记录新文件中的新增或修改行号，用于校验 review finding
    是否真的落在本次 diff 上。删除行没有新文件行号，不作为行级 finding 目标。
    """

    path: str
    changed_lines: tuple[int, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class DiffSummary:
    """本次 review diff 的结构化摘要。"""

    base: str
    head: str | None
    files: tuple[DiffFile, ...]
    raw_diff: str

    @property
    def changed_file_paths(self) -> tuple[str, ...]:
        """返回所有变更文件的路径元组。

        Returns:
            按排序顺序排列的变更文件路径元组。
        """

        return tuple(file.path for file in self.files)

    def changed_lines_for(self, file_path: str) -> set[int]:
        """返回指定文件在 diff 中的变更行号集合。

        Args:
            file_path: 待查询的文件路径。

        Returns:
            新增或修改行号集合；文件不在 diff 中时返回空集合。
        """

        normalized = _normalize_diff_path(file_path)
        for file in self.files:
            if file.path == normalized:
                return set(file.changed_lines)
        return set()


def _validate_git_ref(value: str, *, name: str) -> str:
    """校验 git ref，避免模型把 shell 片段塞进 diff 命令。

    Args:
        value: 待校验的 git ref 字符串。
        name: 参数名称，用于错误提示。

    Returns:
        去除空白后的合法 git ref。

    Raises:
        ValueError: ref 为空或包含不安全字符时抛出。
    """

    ref = value.strip()
    if not ref or not _SAFE_GIT_REF_RE.fullmatch(ref):
        raise ValueError(f"{name} 不是安全的 git ref: {value!r}")
    return ref


def _normalize_diff_path(path: str) -> str:
    """统一 diff 文件路径，去掉 ``a/``、``b/`` 前缀并转换为正斜杠。

    Args:
        path: 原始 diff 文件路径。

    Returns:
        规范化后的正斜杠路径。
    """

    normalized = path.strip().replace("\\", "/")
    if normalized.startswith("a/") or normalized.startswith("b/"):
        normalized = normalized[2:]
    return normalized


def parse_unified_diff(diff_text: str, *, base: str = "HEAD", head: str | None = None) -> DiffSummary:
    """解析 unified diff，提取变更文件和新文件中的变更行号。

    Args:
        diff_text: unified diff 格式文本。
        base: 对比基准分支或 commit。
        head: 可选目标分支。

    Returns:
        结构化的 ``DiffSummary`` 对象。
    """

    files: dict[str, set[int]] = {}
    current_file: str | None = None
    new_line: int | None = None

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            current_file = None
            new_line = None
            parts = raw_line.split()
            if len(parts) >= 4:
                current_file = _normalize_diff_path(parts[3])
                files.setdefault(current_file, set())
            continue

        if raw_line.startswith("+++ "):
            path = raw_line[4:].strip()
            if path != "/dev/null":
                current_file = _normalize_diff_path(path)
                files.setdefault(current_file, set())
            continue

        if raw_line.startswith("@@"):
            match = re.search(r"\+(\d+)(?:,(\d+))?", raw_line)
            new_line = int(match.group(1)) if match else None
            continue

        if current_file is None or new_line is None:
            continue

        if raw_line.startswith("+") and not raw_line.startswith("+++"):
            files.setdefault(current_file, set()).add(new_line)
            new_line += 1
        elif raw_line.startswith("-") and not raw_line.startswith("---"):
            continue
        else:
            new_line += 1

    parsed_files = tuple(
        DiffFile(path=path, changed_lines=tuple(sorted(lines)))
        for path, lines in sorted(files.items())
    )
    return DiffSummary(base=base, head=head, files=parsed_files, raw_diff=diff_text)


def get_local_diff(backend: LocalShellBackend, repo_dir: str, base: str = "HEAD") -> str:
    """返回 reviewer 要审查的本地 git diff 文本。

    新 reviewer 工具优先调用 ``get_local_diff_summary``，因为后者会同时返回
    变更文件和变更行号。

    Args:
        backend: 当前工作区的 LocalShellBackend。
        repo_dir: 仓库虚拟路径，例如 ``projects/ai_coding``。
        base: 对比基准分支、commit 或 ``HEAD``。

    Returns:
        unified diff 格式的原始文本。

    Raises:
        RuntimeError: git diff 命令执行失败时抛出。
    """

    base = _validate_git_ref(base, name="base")
    result = backend.run(f"git diff --unified=80 {base}", cwd=repo_dir)
    if result.exit_code != 0:
        raise RuntimeError(result.stderr or result.stdout or "git diff failed")
    return result.stdout


def get_local_diff_summary(
    backend: LocalShellBackend,
    repo_dir: str,
    *,
    base: str = "HEAD",
    head: str | None = None,
) -> DiffSummary:
    """生成本地分支或工作区 diff 的结构化摘要。

    Args:
        backend: 当前工作区的 LocalShellBackend。
        repo_dir: 仓库虚拟路径，例如 ``projects/ai_coding``。
        base: 对比基准分支、commit 或 ``HEAD``。
        head: 可选目标分支。传入时使用 ``base...head``，不传时审查工作区相对 base 的 diff。

    Returns:
        结构化的 ``DiffSummary`` 对象。

    Raises:
        ValueError: git ref 不合法时抛出。
        RuntimeError: git diff 命令执行失败时抛出。
    """

    base_ref = _validate_git_ref(base, name="base")
    head_ref = _validate_git_ref(head, name="head") if head else None
    range_expr = f"{base_ref}...{head_ref}" if head_ref else base_ref
    result = backend.run(f"git diff --unified=80 {range_expr}", cwd=repo_dir)
    if result.exit_code != 0:
        raise RuntimeError(result.stderr or result.stdout or "git diff failed")
    return parse_unified_diff(result.stdout, base=base_ref, head=head_ref)


def validate_finding_location(
    summary: DiffSummary,
    *,
    file: str,
    line: int | None,
) -> tuple[bool, str]:
    """校验 finding 是否指向本次 diff 中真实存在的文件和变更行。

    Args:
        summary: 已解析的 diff 摘要。
        file: finding 指向的文件路径。
        line: finding 指向的行号，可以为 None。

    Returns:
        ``(is_valid, message)`` 元组。
    """

    normalized = _normalize_diff_path(file)
    changed_files = set(summary.changed_file_paths)
    if normalized not in changed_files:
        return False, f"文件不在本次 diff 中: {file}"
    if line is None:
        return True, "文件属于本次 diff，未指定行号。"
    changed_lines = summary.changed_lines_for(normalized)
    if line not in changed_lines:
        return False, f"行号 {line} 不是文件 {normalized} 的新增或修改行。"
    return True, "finding 位置有效。"
