"""仓库级长期记忆的结构化更新策略。

任务结束后，从 Agent 最终输出中提取技术栈、测试命令、关键文件等稳定事实，
写回对应的仓库记忆文件。

安全边界：
- 包含 ``.env``、``.secrets``、api_key、私钥等敏感标记的文本不会写入记忆。
- 写入前经过 ``mask_token`` 脱敏处理。
- 最近结论限制条数，避免上下文被历史噪声污染。
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date

from langgraph.store.base import BaseStore

from agent.memory.repo import (
    ScmRepo,
    build_repo_memory_namespace,
    get_repo_memory_item,
    repo_memory_store_key,
)
from config.constants import MAX_FACT_CHARS, MAX_RECENT_ITEMS, SENSITIVE_MARKERS
from utils.gitee_utils import mask_token

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RepoMemoryUpdate:
    """一次仓库记忆更新所需的稳定事实。

    Attributes:
        task_kind: 任务类型，如 ``coding``、``review`` 等。
        final_text: Agent 最终输出文本，提取信息的来源。
        branch_name: 本次任务使用的分支名，可为空。
        pr_url: 本次任务创建的 PR/MR 地址，可为空。
    """
    task_kind: str
    final_text: str
    branch_name: str | None = None
    pr_url: str | None = None


def _contains_sensitive_text(text: str) -> bool:
    """判断文本是否包含不应写入长期记忆的敏感标记。

    Args:
        text: 待检测的文本。

    Returns:
        包含敏感标记时返回 ``True``。
    """
    lowered = text.lower()
    return any(marker in lowered for marker in SENSITIVE_MARKERS)


def _compact_line(text: str, *, limit: int = MAX_FACT_CHARS) -> str:
    """把最终回答压缩成适合写入"最近结论"的单行文本。

    先做 token 脱敏和空白折叠，超出 ``limit`` 时截断并追加 ``...``。

    Args:
        text: Agent 最终输出文本。
        limit: 单行最大字符数，默认取 :data:`MAX_FACT_CHARS`。

    Returns:
        压缩后的单行字符串。
    """
    compacted = " ".join(mask_token(text).split())
    if len(compacted) > limit:
        compacted = compacted[:limit].rstrip() + "..."
    return compacted


def _extract_bullets(text: str, *, keywords: tuple[str, ...], limit: int = 8) -> list[str]:
    """从最终回答中提取包含关键词的列表项。

    只做规则提取，不调用模型。适合抓取 Agent 最终总结里的测试命令、
    关键文件、已完成能力等稳定信息。

    Args:
        text: Agent 最终输出文本。
        keywords: 匹配关键词元组，列表项中包含任意一个即命中。
        limit: 最多提取条数。

    Returns:
        命中的列表项文本列表，已脱敏且去重。
    """
    results: list[str] = []
    for raw_line in text.splitlines():
        if not re.match(r"^\s*(?:[-*]|\d+[.)、])\s+", raw_line):
            continue
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^[-*]\s+", "", line)
        line = re.sub(r"^\d+[.)、]\s*", "", line)
        if line.startswith(("#", "|", "```")):
            continue
        if not line or _contains_sensitive_text(line):
            continue
        lowered = line.lower()
        if any(keyword.lower() in lowered for keyword in keywords):
            clean = mask_token(line)
            if clean not in results:
                results.append(clean)
        if len(results) >= limit:
            break
    return results


def _extract_code_items(text: str, *, suffixes: tuple[str, ...], limit: int = 10) -> list[str]:
    """从 Markdown 反引号内容中提取文件名或命令。

    扫描文本中的反引号包裹内容，筛选出包含指定后缀的条目。

    Args:
        text: Agent 最终输出文本。
        suffixes: 文件后缀元组，如 ``(".py", ".md")``。
        limit: 最多提取条数。

    Returns:
        命中的文件名或命令列表，已脱敏且去重。
    """
    results: list[str] = []
    for item in re.findall(r"`([^`]+)`", text):
        clean = item.strip()
        if not clean or _contains_sensitive_text(clean):
            continue
        if any(clean.endswith(suffix) for suffix in suffixes) or any(suffix in clean for suffix in suffixes):
            if clean not in results:
                results.append(mask_token(clean))
        if len(results) >= limit:
            break
    return results


def _extract_test_commands(text: str, *, limit: int = 5) -> list[str]:
    """提取明确可执行的测试命令，避免把 git log 或说明文字误当命令。

    按三轮策略逐步放宽：先匹配反引号内的 pytest 命令，再匹配行首的
    pytest 命令，最后用正则模糊匹配。

    Args:
        text: Agent 最终输出文本。
        limit: 最多提取条数。

    Returns:
        测试命令列表，已脱敏且去重。
    """
    results: list[str] = []
    for item in re.findall(r"`([^`]+)`", text):
        clean = item.strip()
        lowered = clean.lower()
        if lowered.startswith(("python -m pytest", "pytest")) and not _contains_sensitive_text(clean):
            if clean not in results:
                results.append(mask_token(clean))
        if len(results) >= limit:
            return results

    for raw_line in text.splitlines():
        line = raw_line.strip()
        lowered = line.lower()
        if lowered.startswith(("python -m pytest", "pytest")) and not _contains_sensitive_text(line):
            clean = mask_token(line)
            if clean not in results:
                results.append(clean)
        if len(results) >= limit:
            break
    if len(results) < limit:
        for match in re.findall(
            r"(?:python\s+-m\s+pytest|pytest)(?:\s+[A-Za-z0-9_./\\:-]+)?",
            text,
            flags=re.I,
        ):
            clean = " ".join(match.strip().split())
            if clean and clean not in results and not _contains_sensitive_text(clean):
                results.append(mask_token(clean))
            if len(results) >= limit:
                break
    return results


def _extract_file_names(text: str, *, limit: int = 10) -> list[str]:
    """从普通说明文字中提取常见项目文件名。

    匹配 ``word.py``、``word.md`` 等带常见后缀的标识符。

    Args:
        text: Agent 最终输出文本。
        limit: 最多提取条数。

    Returns:
        文件名列表，已脱敏且去重。
    """
    results: list[str] = []
    for match in re.findall(r"[\w.-]+\.(?:py|md|txt|html|json|toml)", text, flags=re.I):
        clean = match.strip()
        if clean and clean not in results and not _contains_sensitive_text(clean):
            results.append(mask_token(clean))
        if len(results) >= limit:
            break
    return results


def _detect_stack(text: str) -> list[str]:
    """从最终回答中识别常见技术栈关键词。

    通过预定义的关键词表匹配文本中出现的技术栈名称。

    Args:
        text: Agent 最终输出文本。

    Returns:
        识别到的技术栈名称列表，如 ``["FastAPI", "pytest"]``。
    """
    candidates = {
        "FastAPI": ("fastapi",),
        "SQLite": ("sqlite",),
        "pytest": ("pytest",),
        "uvicorn": ("uvicorn",),
        "JWT": ("jwt", "python-jose"),
        "passlib/bcrypt": ("passlib", "bcrypt"),
    }
    lowered = text.lower()
    return [name for name, markers in candidates.items() if any(marker in lowered for marker in markers)]


def _replace_section(memory: str, heading: str, items: list[str]) -> str:
    """用列表项替换 Markdown 二级标题下的内容。

    找到对应标题后、下一个标题前的区域，替换为新列表。若标题不存在，
    则追加到文档末尾。

    Args:
        memory: 当前记忆文件全文。
        heading: 二级标题，如 ``"## 技术栈"``。
        items: 要写入的列表项。

    Returns:
        替换后的记忆全文；``items`` 为空时原样返回。
    """
    if not items:
        return memory
    section = f"{heading}\n" + "\n".join(f"- {item}" for item in items) + "\n\n"
    pattern = re.compile(
        rf"(^## {re.escape(heading.removeprefix('## '))}\n)(.*?)(?=^## |\Z)",
        re.M | re.S,
    )
    if pattern.search(memory):
        return pattern.sub(lambda _: section, memory, count=1)
    return f"{memory.rstrip()}\n\n{section}"


def _append_recent(memory: str, *, task_kind: str, fact: str) -> str:
    """向"最近结论"区域追加一条记录，并限制总条数。

    新条目插入到最前面，旧条目保留但不超过 :data:`MAX_RECENT_ITEMS` 条。
    过长、含代码块或表格的条目会被跳过。

    Args:
        memory: 当前记忆文件全文。
        task_kind: 任务类型，写入条目括号内。
        fact: 要追加的结论文本。

    Returns:
        追加后的记忆全文；``fact`` 为空或含敏感信息时原样返回。
    """
    if not fact or _contains_sensitive_text(fact):
        return memory
    entry = f"- {date.today().isoformat()}（{task_kind}）：{fact}"
    heading = "## 最近结论"
    if entry in memory:
        return memory
    if heading not in memory:
        return f"{memory.rstrip()}\n\n{heading}\n{entry}\n"
    before, after = memory.split(heading, 1)
    lines: list[str] = []
    for line in after.strip().splitlines():
        stripped = line.strip()
        if not stripped or stripped == "- 暂无":
            continue
        if len(stripped) > 520 or "```" in stripped or "| 项目 |" in stripped:
            continue
        lines.append(stripped)
    recent_items = [entry, *lines][:MAX_RECENT_ITEMS]
    return f"{before.rstrip()}\n\n{heading}\n" + "\n".join(recent_items) + "\n"


def _metadata_items(*, branch_name: str | None, pr_url: str | None) -> list[str]:
    """整理分支和 PR 这类线程元数据，生成可写入记忆的描述项。

    Args:
        branch_name: 本次任务使用的分支名。
        pr_url: 本次任务创建的 PR/MR 地址。

    Returns:
        非空元数据描述项列表；均为空时返回空列表。
    """
    items: list[str] = []
    if branch_name:
        items.append(f"最近分支：`{branch_name}`")
    if pr_url:
        items.append(f"最近 Pull Request：{pr_url}")
    return items


def build_updated_repo_memory(memory: str, update: RepoMemoryUpdate) -> str:
    """根据任务最终输出生成更新后的仓库记忆正文。

    从 ``update.final_text`` 中提取技术栈、测试命令、关键文件、已完成能力
    等稳定事实，替换或追加到记忆文件对应章节，最后在"最近结论"中追加摘要。

    Args:
        memory: 当前记忆文件全文。
        update: 本次更新的事实数据。

    Returns:
        更新后的记忆全文；无有效信息时原样返回。
    """
    text = mask_token(update.final_text or "")
    if not text.strip() or _contains_sensitive_text(text):
        return memory

    stack = _detect_stack(text)
    test_commands = _extract_test_commands(text, limit=5)
    key_files = _extract_code_items(
        text,
        suffixes=(".py", ".md", ".txt", ".html", ".json", ".toml"),
        limit=10,
    )
    for file_name in _extract_file_names(text, limit=10):
        if file_name not in key_files:
            key_files.append(file_name)
        if len(key_files) >= 10:
            break
    completed_features = _extract_bullets(
        text,
        keywords=("接口", "新增", "完成", "实现", "测试通过", "passed"),
        limit=8,
    )
    completed_features = [
        item
        for item in completed_features
        if not any(skip in item for skip in ("分支", "Pull Request", " PR", "Fast-forward", "merge"))
    ]

    updated = memory
    if stack:
        updated = _replace_section(updated, "## 技术栈", stack)
    if test_commands:
        updated = _replace_section(updated, "## 测试命令", test_commands)
    if key_files:
        updated = _replace_section(updated, "## 关键文件", key_files)
    metadata = _metadata_items(branch_name=update.branch_name, pr_url=update.pr_url)
    if metadata:
        updated = _replace_section(updated, "## 分支与 PR", metadata)
    if completed_features:
        updated = _replace_section(updated, "## 已完成能力", completed_features)

    return _append_recent(updated, task_kind=update.task_kind, fact=_compact_line(text))


def update_repo_memory_from_text(
    *,
    store: BaseStore,
    repo: ScmRepo,
    update: RepoMemoryUpdate,
) -> bool:
    """把任务最终输出写回仓库级长期记忆。

    读取当前记忆文件，调用 :func:`build_updated_repo_memory` 生成更新内容，
    仅在内容发生变化时写回 Store。

    Args:
        store: LangGraph Store 实例。
        repo: 已解析的仓库信息。
        update: 本次更新的事实数据。

    Returns:
        记忆被修改并写回返回 ``True``；无变化或记忆不存在返回 ``False``。
    """
    namespace = build_repo_memory_namespace(repo.owner, repo.repo)
    item = get_repo_memory_item(store, namespace)
    if item is None:
        logger.info("仓库记忆不存在，跳过结构化更新：repo=%s/%s", repo.owner, repo.repo)
        return False

    current = str(item.value.get("content") or "")
    updated = build_updated_repo_memory(current, update)
    if updated == current:
        logger.info(
            "仓库记忆无新增稳定结论：repo=%s/%s task_kind=%s",
            repo.owner,
            repo.repo,
            update.task_kind,
        )
        return False

    value = dict(item.value)
    value["content"] = updated
    store.put(namespace, repo_memory_store_key(repo.owner, repo.repo), value)
    logger.info(
        "仓库记忆已结构化更新：repo=%s/%s task_kind=%s",
        repo.owner,
        repo.repo,
        update.task_kind,
    )
    return True
