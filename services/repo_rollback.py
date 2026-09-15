"""Record and restore the repository revision associated with an Agent run."""

from __future__ import annotations

import logging
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from agent.memory.repo import repo_project_dir
from utils.platform_utils import parse_repo_url

logger = logging.getLogger(__name__)
_COMMIT_RE = re.compile(r"^[0-9a-f]{7,64}$", re.IGNORECASE)


def repository_path(repo_url: str, store: Any | None = None) -> Path:
    repo = parse_repo_url(repo_url)
    if store is not None:
        try:
            mapping = store.get_mapping_by_repo(repo.clone_url)
        except Exception:
            mapping = None
        if mapping and mapping.get("workspace_path"):
            return Path(os.environ.get("AI_WORKSPACE_ROOT", r"E:\ai_workspace")) / str(mapping["workspace_path"])
    return Path(os.environ.get("AI_WORKSPACE_ROOT", r"E:\ai_workspace")) / repo_project_dir(repo)


def git_head(repo_url: str, store: Any | None = None) -> str | None:
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
    """Return the first parent of the current commit when one exists."""
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


def git_is_dirty(repo_url: str, store: Any | None = None) -> bool:
    path = repository_path(repo_url, store)
    if not (path / ".git").exists():
        return False
    result = subprocess.run(
        ["git", "-C", str(path), "status", "--porcelain"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    return result.returncode == 0 and bool(result.stdout.strip())


def rollback_run(repo_url: str, run: dict[str, Any], store: Any | None = None) -> bool:
    """Restore tracked files to the revision recorded before one Agent run."""

    base = str(run.get("base_commit") or "")
    head = str(run.get("head_commit") or "")
    if not base or not head or base == head:
        return False
    if not _COMMIT_RE.fullmatch(base) or not _COMMIT_RE.fullmatch(head):
        raise RuntimeError("运行记录中的仓库版本无效，无法回退")

    path = repository_path(repo_url, store)
    current = git_head(repo_url, store)
    if current != head:
        raise RuntimeError("仓库在任务完成后已有新的提交，已停止自动回退以避免覆盖新改动")
    if git_is_dirty(repo_url, store):
        raise RuntimeError("仓库存在未提交的新改动，已停止自动回退以避免覆盖新改动")

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
    logger.info("已回退 Agent 运行产生的代码：base=%s head=%s repo=%s", base, head, path)
    return True
