"""GitHub API 访问封装。

本模块只负责与 GitHub HTTP API 和仓库 URL 解析相关的低层能力：
解析仓库地址、读取访问令牌、创建 Pull Request、发布 PR 评论、处理重复 PR 响应。

上层 LangChain/DeepAgents 工具定义在 `tools/github_tools.py` 中。
这样的分层可以让 API 调用逻辑脱离模型工具协议，便于单元测试、复用和错误处理。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from config.env_config import get_env


@dataclass(frozen=True)
class GitHubRepo:
    """标准化后的 GitHub 仓库信息。

    owner 和 repo 用于调用 GitHub API；clone_url 用于 Git clone/push 等本地命令。
    使用 frozen dataclass 可以避免解析后的仓库信息在调用链中被意外修改。
    """

    owner: str
    repo: str
    clone_url: str


def parse_github_repo_url(repo_url: str) -> GitHubRepo:
    """解析 GitHub 仓库 URL。

    Args:
        repo_url: 用户输入或任务配置中的 GitHub 仓库地址，支持带 `.git` 后缀的 HTTPS 地址。

    Returns:
        标准化后的 `GitHubRepo`。

    Raises:
        ValueError: URL 不是 github.com 域名，或路径中无法解析出 owner/repo。
    """

    parsed = urlparse(repo_url.strip())
    hostname = (parsed.hostname or "").lower()
    if hostname not in {"github.com", "www.github.com"}:
        raise ValueError("当前仅支持 github.com 仓库地址")
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 2:
        raise ValueError(f"无法解析 GitHub 仓库地址: {repo_url}")
    owner = parts[0]
    repo = re.sub(r"\.git$", "", parts[1])
    return GitHubRepo(owner=owner, repo=repo, clone_url=f"https://github.com/{owner}/{repo}.git")


def normalize_github_repo_url(repo_url: str) -> str:
    """把 GitHub 仓库地址规范化为不含 token 的标准 HTTPS clone URL。"""

    return parse_github_repo_url(repo_url).clone_url


def get_github_token() -> str:
    """读取 GitHub 私人令牌，兼容 SCM_GITHUB_TOKEN。

    优先读取 `GITHUB_TOKEN`，没有时回退到 `SCM_GITHUB_TOKEN`。
    这两个变量只在 API 请求或 Git askpass 环境中使用，不应拼接到日志或命令文本中。
    """

    token = get_env("GITHUB_TOKEN").strip() or get_env("SCM_GITHUB_TOKEN").strip()
    if not token:
        raise RuntimeError("Missing required environment variable: GITHUB_TOKEN or SCM_GITHUB_TOKEN")
    return token


def _github_api_base() -> str:
    """读取 GitHub API 基础地址。"""

    return get_env("GITHUB_API_BASE_URL", "https://api.github.com").rstrip("/")


def _github_headers() -> dict[str, str]:
    """构造 GitHub API 请求头。

    GitHub REST API v3 使用 Bearer token 认证。
    """

    return {
        "Authorization": f"Bearer {get_github_token()}",
        "Accept": "application/vnd.github+json",
    }


def _find_existing_pr(*, owner: str, repo: str, head: str, base: str) -> dict | None:
    """通过 GitHub Search API 查找相同 head/base 的现有 PR。

    GitHub 在重复 PR 时返回 422 但不像 Gitee 那样在错误文本中包含 PR URL，
    因此需要主动搜索已有 PR 来实现幂等语义。
    """

    api_base = _github_api_base()
    query = f"repo:{owner}/{repo} is:pr is:open head:{head} base:{base}"
    url = f"{api_base}/search/issues"
    with httpx.Client(timeout=30) as client:
        response = client.get(url, headers=_github_headers(), params={"q": query})
    if response.status_code >= 400:
        return None
    data = response.json()
    items = data.get("items", [])
    if not items:
        return None
    pr = items[0]
    return {
        "html_url": pr["html_url"],
        "url": pr["url"],
        "number": pr["number"],
        "reused": True,
        "message": "已复用相同源分支和目标分支的现有 Pull Request",
    }


def create_pull_request(
    *,
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str,
) -> dict:
    """调用 GitHub API 创建 Pull Request。

    Args:
        owner: GitHub 仓库 owner。
        repo: GitHub 仓库名称。
        head: 源分支名称。
        base: 目标分支名称。
        title: PR 标题。
        body: PR 描述。

    Returns:
        GitHub API 返回的 PR JSON；如果检测到重复 PR，则返回带 `reused=True` 的兼容结构。

    Raises:
        RuntimeError: API 返回失败且无法识别为可复用 PR。
    """

    api_base = _github_api_base()
    url = f"{api_base}/repos/{owner}/{repo}/pulls"
    payload = {
        "title": title,
        "head": head,
        "base": base,
        "body": body,
    }
    with httpx.Client(timeout=30) as client:
        response = client.post(url, headers=_github_headers(), json=payload)
    if response.status_code >= 400:
        existing = _find_existing_pr(owner=owner, repo=repo, head=head, base=base)
        if existing is not None:
            return existing
        raise RuntimeError(f"GitHub 创建 PR 失败: {response.status_code} {response.text}")
    return response.json()


def _github_get(path: str, *, params: dict | None = None) -> dict | list:
    """执行 GitHub GET 请求。

    GitHub REST API v3 使用 Bearer token 认证头。这里集中处理
    token 注入、base url 和错误抛出，避免多个读取函数重复写 HTTP 细节。
    """

    api_base = _github_api_base()
    url = f"{api_base}{path}"
    with httpx.Client(timeout=30) as client:
        response = client.get(url, headers=_github_headers(), params=params or {})
    if response.status_code >= 400:
        raise RuntimeError(f"GitHub API 读取失败: {response.status_code} {response.text}")
    return response.json()


def get_pull_request(*, owner: str, repo: str, number: int) -> dict:
    """读取 GitHub Pull Request 详情。"""

    data = _github_get(f"/repos/{owner}/{repo}/pulls/{number}")
    return data if isinstance(data, dict) else {"items": data}


def list_pull_request_commits(*, owner: str, repo: str, number: int) -> list:
    """读取 GitHub Pull Request 的提交列表。"""

    data = _github_get(f"/repos/{owner}/{repo}/pulls/{number}/commits")
    return data if isinstance(data, list) else [data]


def list_pull_request_files(*, owner: str, repo: str, number: int) -> list:
    """读取 GitHub Pull Request 的文件变更列表。"""

    data = _github_get(f"/repos/{owner}/{repo}/pulls/{number}/files")
    return data if isinstance(data, list) else [data]


def list_pull_request_comments(*, owner: str, repo: str, number: int) -> list:
    """读取 GitHub Pull Request 的普通评论列表。

    GitHub 的 PR 评论通过 issues 端点获取（/issues/{number}/comments）。
    """

    data = _github_get(f"/repos/{owner}/{repo}/issues/{number}/comments")
    return data if isinstance(data, list) else [data]


def post_pr_comment(*, owner: str, repo: str, number: int, body: str) -> dict:
    """调用 GitHub API 向 Pull Request 发布评论。

    Args:
        owner: GitHub 仓库 owner。
        repo: GitHub 仓库名称。
        number: PR 编号。
        body: 评论正文。

    Returns:
        GitHub API 返回的评论 JSON。

    Raises:
        RuntimeError: API 返回失败状态码。
    """

    api_base = _github_api_base()
    url = f"{api_base}/repos/{owner}/{repo}/issues/{number}/comments"
    with httpx.Client(timeout=30) as client:
        response = client.post(url, headers=_github_headers(), json={"body": body})
    if response.status_code >= 400:
        raise RuntimeError(f"GitHub 发布 PR 评论失败: {response.status_code} {response.text}")
    return response.json()
