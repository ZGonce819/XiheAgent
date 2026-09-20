"""记录并恢复 Agent 运行关联的仓库版本。"""

from __future__ import annotations

import logging
import json
import re
import subprocess
from pathlib import Path
from typing import Any

from agent.memory.repo import repo_project_dir
from config.app_config import get_settings
from utils.recycle_bin import move_to_recycle_bin
from utils.platform_utils import parse_repo_url

logger = logging.getLogger(__name__)
_COMMIT_RE = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)


def repository_path(repo_url: str, store: Any | None = None) -> Path:
    """根据仓库 URL 解析本地工作目录路径。

    优先从项目绑定映射表获取；未命中时回退到默认的
    ``projects/<repo>`` 目录。

    Args:
        repo_url: Gitee 仓库完整 URL。
        store: 可选的数据存储实例，用于查询项目绑定。

    Returns:
        仓库对应的本地绝对路径。
    """
    workspace_root = get_settings().workspace_root
    repo = parse_repo_url(repo_url)
    if store is not None:
        try:
            mapping = store.get_mapping_by_repo(repo.clone_url)
        except Exception:
            mapping = None
        if mapping and mapping.get("workspace_path"):
            return workspace_root / str(mapping["workspace_path"])
    return workspace_root / repo_project_dir(repo)


def _git_untracked_files(path: Path) -> set[str]:
    """获取仓库中未跟踪的文件路径集合，不修改任何文件。

    Args:
        path: Git 仓库本地路径。

    Returns:
        仓库相对路径的集合；命令失败时返回空集合。
    """

    result = subprocess.run(
        ["git", "-C", str(path), "ls-files", "--others", "--exclude-standard", "-z"],
        capture_output=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        return set()
    return {
        item.decode("utf-8", errors="replace").replace("\\", "/")
        for item in result.stdout.split(b"\0")
        if item
    }


def _git_added_files(path: Path, base: str, head: str) -> set[str]:
    """获取两个提交之间新增的文件路径集合。

    Args:
        path: Git 仓库本地路径。
        base: 基准提交哈希。
        head: 目标提交哈希。

    Returns:
        新增文件的仓库相对路径集合；命令失败时返回空集合。
    """

    result = subprocess.run(
        [
            "git",
            "-C",
            str(path),
            "diff",
            "--diff-filter=A",
            "--name-only",
            "-z",
            base,
            head,
        ],
        capture_output=True,
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        return set()
    return {
        item.decode("utf-8", errors="replace").replace("\\", "/")
        for item in result.stdout.split(b"\0")
        if item
    }


def _event_generated_paths(run: dict[str, Any], store: Any | None) -> set[str]:
    """从运行事件中提取本次 Agent 写入的文件路径集合。

    Args:
        run: 运行记录字典。
        store: 数据存储实例。

    Returns:
        本次运行通过 ``write_file`` 工具写入的文件路径集合。
    """

    thread_id = str(run.get("thread_id") or "")
    if not thread_id or store is None:
        return set()
    try:
        events = store.list_run_events(thread_id)
    except Exception:
        logger.warning("读取运行事件失败，无法清理本次运行生成的文件", exc_info=True)
        return set()

    paths: set[str] = set()
    for event in events:
        if event.get("status") != "completed":
            continue
        detail = event.get("detail")
        if not detail:
            continue
        try:
            payload = json.loads(detail) if isinstance(detail, str) else detail
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(payload, dict) or payload.get("tool") != "write_file":
            continue
        args = payload.get("args")
        if not isinstance(args, dict):
            continue
        value = args.get("file_path") or args.get("path")
        if isinstance(value, str) and value.strip():
            paths.add(value.strip())
    return paths


def _repo_relative_event_path(raw_path: str, repo_path: Path) -> str | None:
    """将记录的写入路径规范化为仓库相对的 POSIX 路径。

    支持绝对路径和 ``projects/<repo>/...`` 前缀两种格式。

    Args:
        raw_path: 原始记录的文件路径。
        repo_path: 仓库本地根目录。

    Returns:
        仓库相对 POSIX 路径；无法解析时返回 ``None``。
    """

    raw = raw_path.replace("\\", "/").strip()
    if re.match(r"^[A-Za-z]:/", raw):
        try:
            return Path(raw).resolve().relative_to(repo_path.resolve()).as_posix()
        except ValueError:
            return None

    normalized = raw.lstrip("/")
    project_prefix = f"projects/{repo_path.name}/"
    if normalized.startswith(project_prefix):
        normalized = normalized[len(project_prefix):]
    elif normalized == f"projects/{repo_path.name}":
        return None
    elif normalized.startswith("projects/"):
        normalized = normalized[len("projects/"):]
    if not normalized:
        return None
    candidate = (repo_path / normalized).resolve()
    try:
        relative = candidate.relative_to(repo_path.resolve())
    except ValueError:
        return None
    return relative.as_posix() if relative.parts else None


def _recycle_generated_files(
    repo_path: Path,
    run: dict[str, Any],
    store: Any | None,
    *,
    added_files: set[str] | None = None,
) -> list[Path]:
    """回收本次运行新增的已提交文件和未跟踪文件。

    Args:
        repo_path: Git 仓库本地路径。
        run: 运行记录字典。
        store: 数据存储实例。
        added_files: 可选的已提交新增文件集合。

    Returns:
        已移入回收站的路径列表。
    """

    recorded = {
        relative
        for raw in _event_generated_paths(run, store)
        if (relative := _repo_relative_event_path(raw, repo_path))
    }
    # 普通文件夹没有 Git 索引，仍应回收成功 write_file 创建的文件。
    # Git 项目继续使用未跟踪列表，避免把已有受版本控制的文件移走。
    untracked = _git_untracked_files(repo_path) if (repo_path / ".git").exists() else recorded
    targets = sorted((added_files or set()).union(untracked.intersection(recorded)))
    moved: list[Path] = []
    workspace_root = get_settings().workspace_root
    for relative in targets:
        source = repo_path / Path(relative)
        if source.exists() or source.is_symlink():
            moved.append(
                move_to_recycle_bin(
                    source,
                    project_root=repo_path,
                    workspace_root=workspace_root,
                )
            )
    return moved


def git_head(repo_url: str, store: Any | None = None) -> str | None:
    """获取仓库当前 HEAD 提交的哈希值。

    Args:
        repo_url: Gitee 仓库完整 URL。
        store: 可选的数据存储实例。

    Returns:
        HEAD 提交哈希字符串；仓库不存在或命令失败时返回 ``None``。
    """
    path = repository_path(repo_url, store)
    if not (path / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value if _COMMIT_RE.fullmatch(value) else None


def git_parent(repo_url: str, store: Any | None = None) -> str | None:
    """获取当前 HEAD 提交的第一个父提交哈希。

    Args:
        repo_url: Gitee 仓库完整 URL。
        store: 可选的数据存储实例。

    Returns:
        父提交哈希字符串；无父提交或命令失败时返回 ``None``。
    """
    path = repository_path(repo_url, store)
    if not (path / ".git").exists():
        return None
    result = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD^"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and _COMMIT_RE.fullmatch(value) else None


def git_changed_files(repo_url: str, base: str, head: str, store: Any | None = None) -> list[str]:
    """获取两个提交之间变更的文件路径列表。

    Args:
        repo_url: Gitee 仓库完整 URL。
        base: 基准提交哈希。
        head: 目标提交哈希。
        store: 可选的数据存储实例。

    Returns:
        变更文件的仓库相对路径列表；命令失败时返回空列表。
    """
    path = repository_path(repo_url, store)
    if not (path / ".git").exists():
        return []
    result = subprocess.run(
        ["git", "-C", str(path), "diff", "--name-only", base, head],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def git_files_dirty(repo_url: str, files: list[str], store: Any | None = None) -> bool:
    """检查给定文件列表中是否存在未提交的改动。

    Args:
        repo_url: Gitee 仓库完整 URL。
        files: 待检查的文件路径列表。
        store: 可选的数据存储实例。

    Returns:
        存在未提交改动时返回 ``True``，否则返回 ``False``。
    """
    path = repository_path(repo_url, store)
    if not (path / ".git").exists():
        return False
    if not files:
        return False
    result = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain", "--", *files],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def rollback_run(repo_url: str, run: dict[str, Any], store: Any | None = None) -> bool:
    """回退一次 Agent 运行：恢复跟踪文件并回收未跟踪的新增文件。

    如果运行包含提交变更，则执行 ``git reset --hard`` 回退到基准提交；
    否则仅回收本次运行通过 ``write_file`` 创建的文件。

    Args:
        repo_url: Gitee 仓库完整 URL。
        run: 运行记录字典，需包含 ``base_commit`` 和 ``head_commit``。
        store: 可选的数据存储实例。

    Returns:
        成功回退时返回 ``True``。

    Raises:
        RuntimeError: 提交无效、仓库已有新提交或文件存在未提交改动时抛出。
    """

    base = str(run.get("base_commit") or "")
    head = str(run.get("head_commit") or "")
    path = repository_path(repo_url, store)
    has_commit_rollback = bool(base and head and base != head)
    if has_commit_rollback:
        if not _COMMIT_RE.fullmatch(base) or not _COMMIT_RE.fullmatch(head):
            raise RuntimeError("运行记录中的仓库版本无效，无法回退")
        current = git_head(repo_url, store)
        if current != head:
            raise RuntimeError("仓库在任务完成后已有新的提交，已停止自动回退以避免覆盖新改动")
        changed = git_changed_files(repo_url, base, head, store)
        if git_files_dirty(repo_url, changed, store):
            raise RuntimeError("Agent 修改过的文件存在未提交的新改动，已停止自动回退以避免覆盖新改动")

    added_files = _git_added_files(path, base, head) if has_commit_rollback else set()
    recycled = _recycle_generated_files(path, run, store, added_files=added_files)
    if not has_commit_rollback:
        if recycled:
            logger.info("已将 Agent 运行生成的文件移入回收站：repo=%s count=%d", path, len(recycled))
        return bool(recycled)

    result = subprocess.run(
        ["git", "-C", str(path), "reset", "--hard", base],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"仓库代码回退失败：{detail or 'git reset 失败'}")
    logger.info(
        "已回退 Agent 运行产生的代码：base=%s head=%s repo=%s recycled=%d",
        base,
        head,
        path,
        len(recycled),
    )
    return True
