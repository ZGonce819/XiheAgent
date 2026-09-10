"""GitLab REST API 客户端与仓库 URL 工具函数。

本模块处理底层 GitLab HTTP API 交互：
解析仓库地址、读取访问令牌、创建 Merge Request、发布 MR 评论、处理重复 MR 响应。

上层 LangChain/DeepAgents 工具定义在 `tools/gitlab_tools.py` 中。
这样的分层可以让 API 调用逻辑脱离模型工具协议，便于单元测试、复用和错误处理。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, urlparse

import httpx

from config.env_config import get_env


@dataclass(frozen=True)
class GitLabRepo:
    """标准化后的 GitLab 仓库信息。

    owner 和 repo 用于调用 GitLab API；clone_url 用于 Git clone/push 等本地命令。
    使用 frozen dataclass 可以避免解析后的仓库信息在调用链中被意外修改。
    """

    owner: str
    repo: str
    clone_url: str


def parse_gitlab_repo_url(repo_url: str) -> GitLabRepo:
    """解析 GitLab 仓库 URL。

    Args:
        repo_url: 用户输入或任务配置中的 GitLab 仓库地址，支持带 `.git` 后缀的 HTTPS 地址。

    Returns:
        标准化后的 `GitLabRepo`。

    Raises:
        ValueError: URL 不是 gitlab.com 域名，或路径中无法解析出 owner/repo。
    """

    parsed = urlparse(repo_url.strip())
    hostname = (parsed.hostname or "").lower()
    if hostname not in {"gitlab.com", "www.gitlab.com"}:
        raise ValueError("当前仅支持 gitlab.com 仓库地址")
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"无法解析 GitLab 仓库地址: {repo_url}")
    owner = parts[0]
    repo = re.sub(r"\.git$", "", parts[1])
    return GitLabRepo(owner=owner, repo=repo, clone_url=f"https://gitlab.com/{owner}/{repo}.git")


def normalize_gitlab_repo_url(repo_url: str) -> str:
    """把 GitLab 仓库地址规范化为不含 token 的标准 HTTPS clone URL。"""

    return parse_gitlab_repo_url(repo_url).clone_url


def get_gitlab_token() -> str:
    """读取 GitLab 私人令牌，兼容 SCM_GITLAB_TOKEN。

    优先读取 `GITLAB_TOKEN`，没有时回退到 `SCM_GITLAB_TOKEN`。
    这两个变量只在 API 请求或 Git askpass 环境中使用，不应拼接到日志或命令文本中。
    """

    token = get_env("GITLAB_TOKEN").strip() or get_env("SCM_GITLAB_TOKEN").strip()
    if not token:
        raise RuntimeError("Missing required environment variable: GITLAB_TOKEN or SCM_GITLAB_TOKEN")
    return token


def _gitlab_api_base() -> str:
    """读取 GitLab API 基础地址。"""

    return get_env("GITLAB_API_BASE_URL", "https://gitlab.com/api/v4").rstrip("/")


def _gitlab_headers() -> dict[str, str]:
    """构造 GitLab API 请求头。

    GitLab API v4 使用 PRIVATE-TOKEN 认证头。
    """

    return {
        "PRIVATE-TOKEN": get_gitlab_token(),
        "Content-Type": "application/json",
    }


def _encode_project_id(owner: str, repo: str) -> str:
    """将 owner/repo 编码为 GitLab API 所需的 URL 编码格式。

    GitLab API 使用 URL 编码的 ``namespace/project`` 作为项目标识符。
    """

    return quote(f"{owner}/{repo}", safe="")


def _find_existing_mr(*, owner: str, repo: str, head: str, base: str) -> dict | None:
    """查找相同 source_branch/target_branch 的现有 MR。

    GitLab 在重复 MR 时返回 409，需要主动搜索已有 MR 来实现幂等语义。
    """

    api_base = _gitlab_api_base()
    project_id = _encode_project_id(owner, repo)
    url = f"{api_base}/projects/{project_id}/merge_requests"
    params = {
        "state": "opened",
        "source_branch": head,
        "target_branch": base,
    }
    with httpx.Client(timeout=30) as client:
        response = client.get(url, headers=_gitlab_headers(), params=params)
    if response.status_code >= 400:
        return None
    items = response.json()
    if not isinstance(items, list) or not items:
        return None
    mr = items[0]
    return {
        "web_url": mr.get("web_url", ""),
        "iid": mr.get("iid", 0),
        "reused": True,
        "message": "已复用相同源分支和目标分支的现有 Merge Request",
    }


def create_merge_request(
    *,
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str,
) -> dict:
    """调用 GitLab API 创建 Merge Request。

    Args:
        owner: GitLab 仓库 owner。
        repo: GitLab 仓库名称。
        head: 源分支名称。
        base: 目标分支名称。
        title: MR 标题。
        body: MR 描述。

    Returns:
        GitLab API 返回的 MR JSON；如果检测到重复 MR，则返回带 `reused=True` 的兼容结构。

    Raises:
        RuntimeError: API 返回失败且无法识别为可复用 MR。
    """

    api_base = _gitlab_api_base()
    project_id = _encode_project_id(owner, repo)
    url = f"{api_base}/projects/{project_id}/merge_requests"
    payload = {
        "title": title,
        "source_branch": head,
        "target_branch": base,
        "description": body,
    }
    with httpx.Client(timeout=30) as client:
        response = client.post(url, headers=_gitlab_headers(), json=payload)
    if response.status_code >= 400:
        existing = _find_existing_mr(owner=owner, repo=repo, head=head, base=base)
        if existing is not None:
            return existing
        raise RuntimeError(f"GitLab 创建 MR 失败: {response.status_code} {response.text}")
    return response.json()


def _gitlab_get(path: str, *, params: dict | None = None) -> dict | list:
    """执行 GitLab GET 请求。

    GitLab API v4 使用 PRIVATE-TOKEN 认证头。这里集中处理
    token 注入、base url 和错误抛出，避免多个读取函数重复写 HTTP 细节。
    """

    api_base = _gitlab_api_base()
    url = f"{api_base}{path}"
    with httpx.Client(timeout=30) as client:
        response = client.get(url, headers=_gitlab_headers(), params=params or {})
    if response.status_code >= 400:
        raise RuntimeError(f"GitLab API 读取失败: {response.status_code} {response.text}")
    return response.json()


def get_merge_request(*, owner: str, repo: str, iid: int) -> dict:
    """读取 GitLab Merge Request 详情。"""

    project_id = _encode_project_id(owner, repo)
    data = _gitlab_get(f"/projects/{project_id}/merge_requests/{iid}")
    return data if isinstance(data, dict) else {"items": data}


def list_merge_request_commits(*, owner: str, repo: str, iid: int) -> list:
    """读取 GitLab Merge Request 的提交列表。"""

    project_id = _encode_project_id(owner, repo)
    data = _gitlab_get(f"/projects/{project_id}/merge_requests/{iid}/commits")
    return data if isinstance(data, list) else [data]


def list_merge_request_files(*, owner: str, repo: str, iid: int) -> list:
    """读取 GitLab Merge Request 的文件变更列表。

    GitLab 的 MR 文件变更通过 changes 端点获取。
    """

    project_id = _encode_project_id(owner, repo)
    data = _gitlab_get(f"/projects/{project_id}/merge_requests/{iid}/changes")
    if isinstance(data, dict):
        return data.get("changes", [])
    return [data]


def list_merge_request_comments(*, owner: str, repo: str, iid: int) -> list:
    """读取 GitLab Merge Request 的评论列表。

    GitLab 的 MR 评论通过 notes 端点获取。
    """

    project_id = _encode_project_id(owner, repo)
    data = _gitlab_get(f"/projects/{project_id}/merge_requests/{iid}/notes")
    return data if isinstance(data, list) else [data]


def post_mr_comment(*, owner: str, repo: str, iid: int, body: str) -> dict:
    """调用 GitLab API 向 Merge Request 发布评论。

    Args:
        owner: GitLab 仓库 owner。
        repo: GitLab 仓库名称。
        iid: MR 的项目内部 ID。
        body: 评论正文。

    Returns:
        GitLab API 返回的评论 JSON。

    Raises:
        RuntimeError: API 返回失败状态码。
    """

    api_base = _gitlab_api_base()
    project_id = _encode_project_id(owner, repo)
    url = f"{api_base}/projects/{project_id}/merge_requests/{iid}/notes"
    with httpx.Client(timeout=30) as client:
        response = client.post(url, headers=_gitlab_headers(), json={"body": body})
    if response.status_code >= 400:
        raise RuntimeError(f"GitLab 发布 MR 评论失败: {response.status_code} {response.text}")
    return response.json()
