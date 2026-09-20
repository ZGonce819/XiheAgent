"""仓库目录映射。

管理 SCM 仓库（Gitee / GitHub / Codeup）URL 到本地工作区目录的映射关系。
Agent 执行任务时需要知道"这个仓库对应本地哪个目录"，本模块负责解析和持久化这个映射。

核心流程：
1. 先查 MySQL 映射表，看是否已经有保存过的目录。
2. 按仓库名检查 `projects/仓库名` 默认目录。
3. 扫描 `projects/*` 下一级 Git 仓库，通过 origin remote 匹配。
4. 都找不到时返回默认 clone 目录，clone 成功后再持久化映射。

和 LX_AICoding 版本的差异：
- 存储后端从 SQLite 换成 MySQL（LocalMySQLStore）。
- 映射表字段简化为 workspace_path，不再有 source/notes/verified。
- 查询方法名从 get_repo_mapping 改为 get_mapping_by_repo。
"""

from __future__ import annotations

import configparser
import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from backends.workspace import Workspace
from store import LocalMySQLStore
from utils.platform_utils import ScmRepo, parse_repo_url


@dataclass(frozen=True)
class RepoMappingResult:
    """仓库和本地目录的解析结果。

    Attributes:
        repo: 解析后的仓库标识对象。
        project_dir: 工作区相对路径。
        local_path: 本地绝对路径。
        source: 匹配来源，如 ``"stored"``、``"default_name"``、``"projects_scan"``、``"default_clone_path"``。
        mapping: MySQL 映射表记录；未找到时为 ``None``。
        remote_matched: 是否通过本地 Git remote 验证。
    """

    repo: ScmRepo
    project_dir: str
    local_path: str
    source: str
    mapping: dict[str, Any] | None = None
    remote_matched: bool = False


def normalize_repo_url(repo_url: str) -> str:
    """把带 token、无 .git、www 域名等形式统一成标准 clone_url。

    仓库映射表使用这个标准地址作为唯一业务键，避免同一个仓库因为不同写法
    生成多条映射。

    Args:
        repo_url: 原始仓库地址。

    Returns:
        标准化的 clone_url。
    """

    return parse_repo_url(repo_url).clone_url


def repo_mapping_id(repo_url: str, project_dir: str) -> str:
    """生成稳定映射 ID，便于同一仓库和目录重复 upsert。

    对标准化后的 repo_url 和 project_dir 拼接计算 SHA1 摘要。

    Args:
        repo_url: 仓库完整地址。
        project_dir: 工作区相对路径。

    Returns:
        40 位十六进制 SHA1 摘要。
    """

    normalized_project_dir = project_dir.replace("\\", "/")
    raw = f"{normalize_repo_url(repo_url)}::{normalized_project_dir}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _candidate_remote_urls(repo_url: str) -> set[str]:
    """生成用于比较 remote origin 的标准化候选 URL。

    根据平台类型生成 HTTPS 和 SSH 两种形态的候选地址，用于与本地
    ``.git/config`` 中的 remote origin 做匹配。

    Args:
        repo_url: 仓库完整地址。

    Returns:
        小写化的候选 URL 集合。
    """

    repo = parse_repo_url(repo_url)
    candidates = {
        repo.clone_url.lower(),
        repo.clone_url.removesuffix(".git").lower(),
    }
    parsed = urlparse(repo.clone_url)
    hostname = (parsed.hostname or "").lower()
    if hostname == "gitee.com":
        candidates.add(f"git@gitee.com:{repo.owner}/{repo.repo}.git".lower())
        candidates.add(f"git@gitee.com:{repo.owner}/{repo.repo}".lower())
    elif hostname == "github.com":
        candidates.add(f"git@github.com:{repo.owner}/{repo.repo}.git".lower())
        candidates.add(f"git@github.com:{repo.owner}/{repo.repo}".lower())
    elif hostname == "codeup.aliyun.com":
        org_id = getattr(repo, "org_id", "")
        if org_id:
            candidates.add(f"git@codeup.aliyun.com:{org_id}/{repo.owner}/{repo.repo}.git".lower())
            candidates.add(f"git@codeup.aliyun.com:{org_id}/{repo.owner}/{repo.repo}".lower())
        else:
            candidates.add(f"git@codeup.aliyun.com:{repo.owner}/{repo.repo}.git".lower())
            candidates.add(f"git@codeup.aliyun.com:{repo.owner}/{repo.repo}".lower())
    return candidates


def _clean_remote_url(remote_url: str) -> str:
    """清理 remote URL 中的 token，保留 owner/repo 语义用于比较。

    对 HTTP(S) 地址去除 token 查询参数并统一为小写；其他形态只去除 ``.git`` 后缀。

    Args:
        remote_url: 原始 remote URL。

    Returns:
        清理后的小写化 URL。
    """

    text = remote_url.strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"} and parsed.hostname:
        path = parsed.path.strip("/")
        return f"https://{parsed.hostname.lower()}/{path}".removesuffix(".git").lower()
    return text.removesuffix(".git").lower()


def _read_origin_hidden(repo_path: Path) -> str | None:
    """读取本地 Git 仓库的 origin remote 地址。

    从 ``.git/config`` 文件中解析 ``[remote "origin"]`` 段的 ``url`` 字段。

    Args:
        repo_path: 本地仓库目录路径。

    Returns:
        origin remote URL；不存在或无 Git 仓库时返回 ``None``。
    """

    config_path = repo_path / ".git" / "config"
    if not config_path.exists():
        return None
    parser = configparser.ConfigParser()
    parser.read(config_path, encoding="utf-8")
    section = 'remote "origin"'
    if not parser.has_section(section):
        return None
    return parser.get(section, "url", fallback=None)


def _is_nested_projects_dir(project_dir: str) -> bool:
    """判断仓库目录是否落入错误的 `projects/projects` 嵌套目录。

    正常业务仓库只应该位于 `projects/<repo>` 或 `projects/<custom-name>`。
    `projects/projects/<repo>` 通常来自命令工作目录和相对路径重复拼接。

    Args:
        project_dir: 工作区相对路径。

    Returns:
        ``True`` 表示是嵌套的错误目录。
    """

    normalized = project_dir.replace("\\", "/").strip("/")
    return normalized == "projects/projects" or normalized.startswith("projects/projects/")


def remote_matches_repo(remote_url: str | None, repo_url: str) -> bool:
    """判断本地 remote origin 是否对应目标仓库。

    把双方 URL 都标准化后比较，支持 HTTPS 和 SSH 混合比较。

    Args:
        remote_url: 本地 Git 仓库的 origin remote URL。
        repo_url: 目标仓库地址。

    Returns:
        ``True`` 表示匹配；``False`` 表示不匹配或 remote_url 为空。
    """

    if not remote_url:
        return False
    cleaned = _clean_remote_url(remote_url)
    candidates = {_clean_remote_url(item) for item in _candidate_remote_urls(repo_url)}
    return cleaned in candidates


def _save_mapping(
    store: LocalMySQLStore,
    *,
    repo: ScmRepo,
    workspace_path: str,
) -> None:
    """统一写入仓库目录映射。

    MySQL 映射表只保存 mapping_id、repo_url、workspace_path、repo_owner、repo_name，
    不再有 source/notes/verified 等辅助字段。

    Args:
        store: MySQL Store 实例。
        repo: 仓库标识对象。
        workspace_path: 工作区相对路径。
    """

    project_dir = workspace_path.replace("\\", "/")
    store.upsert_mapping(
        mapping_id=repo_mapping_id(repo.clone_url, project_dir),
        repo_url=repo.clone_url,
        workspace_path=project_dir,
        repo_owner=repo.owner,
        repo_name=repo.repo,
    )


def discover_repo_mapping(
    *,
    repo_url: str,
    workspace: Workspace,
    store: LocalMySQLStore,
    local_folder_mode: bool = False,
) -> RepoMappingResult:
    """解析仓库对应的本地 projects 目录。

    顺序：
    1. 读取 MySQL 中已保存的映射。
    2. 按仓库名检查 `projects/仓库名`。
    3. 只扫描 `projects/*` 下一级 Git 仓库，通过 origin remote 匹配。
    4. 都找不到时返回默认 clone 目录 `projects/仓库名`。

    本地文件夹模式下（``local_folder_mode=True``），用户选定的文件夹本身就是
    项目根，仓库目录恒为当前目录 ``.``，不再走 ``projects/<repo>`` 推导和扫描。
    此时只判断文件夹根是否已经是对应仓库的 Git 目录，用以决定 source 和
    remote_matched，clone 目标同样是 ``.``。

    Args:
        repo_url: 仓库完整地址。
        workspace: 当前工作区实例。
        store: MySQL Store 实例。
        local_folder_mode: 是否为本地文件夹模式。

    Returns:
        仓库映射解析结果，包含本地路径、匹配来源和验证状态。
    """

    repo = parse_repo_url(repo_url)

    if local_folder_mode:
        # 文件夹即项目根：workspace.root 就是用户选定文件夹，仓库目录为 "."。
        root_path = workspace.resolve(".")
        remote = _read_origin_hidden(root_path)
        already_repo = (
            root_path.exists()
            and (root_path / ".git").exists()
            and remote_matches_repo(remote, repo.clone_url)
        )
        return RepoMappingResult(
            repo=repo,
            project_dir=".",
            local_path=str(root_path),
            source="local_folder" if already_repo else "local_folder_clone",
            mapping=None,
            remote_matched=already_repo,
        )

    existing = store.get_mapping_by_repo(repo.clone_url)
    if existing:
        # 第一优先级：相信已经保存过的映射，但必须重新验证。
        # 目录可能被用户删除、移动，或者 remote origin 被手动改到另一个仓库。
        # 所以这里不仅看 MySQL，还要读本地 .git/config 做二次确认。
        project_dir = str(existing.get("workspace_path", "")).replace("\\", "/")
        if _is_nested_projects_dir(project_dir):
            project_dir = ""
        if not project_dir:
            existing = None
            local_path = None
            remote = None
        else:
            local_path = workspace.resolve(project_dir)
            remote = _read_origin_hidden(local_path)
        if (
            local_path
            and local_path.exists()
            and (local_path / ".git").exists()
            and remote_matches_repo(remote, repo.clone_url)
        ):
            return RepoMappingResult(
                repo=repo,
                project_dir=project_dir,
                local_path=str(local_path),
                source="stored",
                mapping=existing,
                remote_matched=True,
            )

    default_project_dir = (Path("projects") / repo.repo).as_posix()
    default_path = workspace.resolve(default_project_dir)
    default_remote = _read_origin_hidden(default_path)
    if (
        default_path.exists()
        and (default_path / ".git").exists()
        and remote_matches_repo(default_remote, repo.clone_url)
    ):
        # 第二优先级：按仓库名匹配默认目录。
        # 这是课堂演示最常见的情况：第一次 clone 后通常就是 projects/<repo>。
        # 命中后立即写回映射表，后续任务就可以走 stored 分支。
        _save_mapping(store, repo=repo, workspace_path=default_project_dir)
        return RepoMappingResult(
            repo=repo,
            project_dir=default_project_dir,
            local_path=str(default_path),
            source="default_name",
            mapping=existing,
            remote_matched=True,
        )

    projects_path = workspace.resolve("projects")
    if projects_path.exists():
        # 第三优先级：扫描 projects 下一级目录。
        # 这个分支用于处理用户手动改过目录名的情况，例如 projects/ai_coding_demo。
        # 为了控制成本，只扫描下一层 Git 仓库，不做全盘递归搜索。
        for child in sorted(projects_path.iterdir(), key=lambda item: item.name.lower()):
            if child.name.lower() == "projects":
                continue
            if not child.is_dir() or not (child / ".git").exists():
                continue
            remote = _read_origin_hidden(child)
            if remote_matches_repo(remote, repo.clone_url):
                project_dir = f"projects/{child.name}"
                _save_mapping(store, repo=repo, workspace_path=project_dir)
                return RepoMappingResult(
                    repo=repo,
                    project_dir=project_dir,
                    local_path=str(child),
                    source="projects_scan",
                    mapping=existing,
                    remote_matched=True,
                )

    # 最后仍未找到时，不立即写库，因为此时目录可能还不存在。
    # runtime 会使用这个 default_clone_path 去执行 git clone；clone 成功后再调用
    # save_clone_mapping 持久化，避免把未验证目录提前写成映射。
    return RepoMappingResult(
        repo=repo,
        project_dir=default_project_dir,
        local_path=str(default_path),
        source="default_clone_path",
        mapping=existing,
        remote_matched=False,
    )


def save_clone_mapping(
    *,
    repo_url: str,
    project_dir: str,
    local_path: str,
    store: LocalMySQLStore,
) -> None:
    """clone 或用户手动指定目录成功后保存映射。

    Args:
        repo_url: 仓库完整地址。
        project_dir: 工作区相对路径。
        local_path: 本地绝对路径。
        store: MySQL Store 实例。
    """

    repo = parse_repo_url(repo_url)
    _save_mapping(store, repo=repo, workspace_path=project_dir)
