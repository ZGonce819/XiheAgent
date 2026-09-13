"""Codeup（阿里云云效）API 访问封装。

本模块只负责与 Codeup HTTP API 和仓库 URL 解析相关的低层能力：
解析仓库地址、读取访问令牌、创建 Merge Request、发布 MR 评论、处理重复 MR 响应。

上层 LangChain/DeepAgents 工具定义在 `tools/codeup_tools.py` 中。
这样的分层可以让 API 调用逻辑脱离模型工具协议，便于单元测试、复用和错误处理。

Codeup API 要点：
- 认证使用 `x-yunxiao-token` 请求头（Personal Access Token）。
- 仓库标识使用 `repositoryId`（数值 ID 或 URL 编码的完整路径）。
- MR 使用 `localId` 标识，等同于 Gitee/GitHub 的 PR number。
- 创建 MR 需要 `sourceProjectId` / `targetProjectId`（数值仓库 ID）。
- 评论需要 `patchset_biz_id`（版本 ID），发评论前需先读取版本列表。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, urlparse

import httpx

from config.env_config import get_env


@dataclass(frozen=True)
class CodeupRepo:
    """标准化后的 Codeup 仓库信息。

    org_id 是云效组织 ID；owner/repo 用于路径展示；
    clone_url 用于 Git clone/push 等本地命令。
    使用 frozen dataclass 可以避免解析后的仓库信息在调用链中被意外修改。
    """

    org_id: str
    owner: str
    repo: str
    clone_url: str


def parse_codeup_repo_url(repo_url: str) -> CodeupRepo:
    """解析 Codeup 仓库 URL。

    Args:
        repo_url: Codeup 仓库地址，格式为 `https://codeup.aliyun.com/{org_id}/{owner}/{repo}`，
            支持带 `.git` 后缀。

    Returns:
        标准化后的 `CodeupRepo`。

    Raises:
        ValueError: URL 不是 codeup.aliyun.com 域名，或路径中无法解析出 org_id/owner/repo。
    """

    parsed = urlparse(repo_url.strip())
    hostname = (parsed.hostname or "").lower()
    if hostname not in {"codeup.aliyun.com", "www.codeup.aliyun.com"}:
        raise ValueError("当前仅支持 codeup.aliyun.com 仓库地址")
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) < 3:
        raise ValueError(f"无法解析 Codeup 仓库地址: {repo_url}")
    org_id = parts[0]
    owner = parts[1]
    repo = re.sub(r"\.git$", "", parts[2])
    return CodeupRepo(
        org_id=org_id,
        owner=owner,
        repo=repo,
        clone_url=f"https://codeup.aliyun.com/{org_id}/{owner}/{repo}.git",
    )


def normalize_codeup_repo_url(repo_url: str) -> str:
    """把 Codeup 仓库地址规范化为不含 token 的标准 HTTPS clone URL。

    Args:
        repo_url: 用户输入的 Codeup 仓库地址。

    Returns:
        标准 HTTPS clone URL 字符串。
    """

    return parse_codeup_repo_url(repo_url).clone_url


def get_codeup_token() -> str:
    """读取 Codeup 个人访问令牌，兼容 SCM_CODEUP_TOKEN。

    优先读取 `CODEUP_TOKEN`，没有时回退到 `SCM_CODEUP_TOKEN`。
    这两个变量只在 API 请求或 Git askpass 环境中使用，不应拼接到日志或命令文本中。

    Returns:
        Codeup 访问令牌字符串。

    Raises:
        RuntimeError: 未配置任何 Codeup 令牌环境变量。
    """

    token = get_env("CODEUP_TOKEN").strip() or get_env("SCM_CODEUP_TOKEN").strip()
    if not token:
        raise RuntimeError("Missing required environment variable: CODEUP_TOKEN or SCM_CODEUP_TOKEN")
    return token


def mask_codeup_token(text: str) -> str:
    """对文本中的 Codeup 令牌做脱敏。

    API 错误、Git 输出和异常信息可能包含访问令牌。
    所有写日志或返回给模型的外部错误文本都应经过该函数处理。

    Args:
        text: 待脱敏的原始文本。

    Returns:
        脱敏后的文本。
    """

    masked = text
    for token_name in ("CODEUP_TOKEN", "SCM_CODEUP_TOKEN"):
        token = get_env(token_name).strip()
        if token:
            masked = masked.replace(token, "***")
    return masked


def _codeup_api_base() -> str:
    """读取 Codeup API 基础地址。

    中心版使用 `CODEUP_API_DOMAIN`（默认 `https://devops.aliyuncs.com`）。

    Returns:
        包含组织 ID 的 API 基础 URL 字符串。

    Raises:
        RuntimeError: 未配置 CODEUP_ORG_ID 环境变量。
    """

    domain = get_env("CODEUP_API_DOMAIN", "https://devops.aliyuncs.com").rstrip("/")
    org_id = get_env("CODEUP_ORG_ID").strip()
    if not org_id:
        raise RuntimeError("Missing required environment variable: CODEUP_ORG_ID")
    return f"{domain}/oapi/v1/codeup/organizations/{org_id}"


def _codeup_headers() -> dict[str, str]:
    """构造 Codeup API 请求头。

    Codeup OAPI 使用 `x-yunxiao-token` 请求头传递个人访问令牌。

    Returns:
        包含认证和 Content-Type 的请求头字典。
    """

    return {
        "x-yunxiao-token": get_codeup_token(),
        "Content-Type": "application/json",
    }


def _resolve_repo_id(*, owner: str, repo: str) -> str:
    """把 owner/repo 转换为 Codeup API 可识别的 repositoryId。

    Codeup API 接受数值 ID 或 URL 编码的完整路径。
    这里使用 URL 编码路径（`owner%2Frepo`），避免额外 API 调用来获取数值 ID。

    Args:
        owner: 仓库 owner（命名空间）。
        repo: 仓库名称。

    Returns:
        URL 编码后的 repositoryId 字符串。
    """

    return quote(f"{owner}/{repo}", safe="")


def _resolve_project_id(*, owner: str, repo: str) -> int:
    """通过 Codeup API 查询仓库的数值 ID，供创建 MR 时使用。

    Codeup 创建 MR 需要 `sourceProjectId` / `targetProjectId`（数值类型），
    不能直接使用 URL 编码路径。这里通过 GetRepository API 解析。

    Args:
        owner: 仓库 owner。
        repo: 仓库名称。

    Returns:
        仓库数值 ID。

    Raises:
        RuntimeError: API 返回失败或响应中缺少数值 ID。
    """

    api_base = _codeup_api_base()
    repo_path = quote(f"{owner}/{repo}", safe="")
    url = f"{api_base}/repositories/{repo_path}"
    with httpx.Client(timeout=30) as client:
        response = client.get(url, headers=_codeup_headers())
    if response.status_code >= 400:
        raise RuntimeError(f"Codeup 查询仓库信息失败: {response.status_code} {response.text}")
    data = response.json()
    project_id = data.get("id") or data.get("projectId")
    if project_id is None:
        raise RuntimeError(f"Codeup 仓库响应中未找到数值 ID: {data}")
    return int(project_id)


def _codeup_get(path: str, *, params: dict | None = None) -> dict | list:
    """执行 Codeup GET 请求。

    Codeup OAPI 使用 `x-yunxiao-token` 认证头。这里集中处理
    token 注入、base url 和错误抛出，避免多个读取函数重复写 HTTP 细节。

    Args:
        path: API 路径。
        params: 可选的查询参数字典。

    Returns:
        API 返回的 JSON 字典或列表。

    Raises:
        RuntimeError: API 返回 400 及以上状态码。
    """

    api_base = _codeup_api_base()
    url = f"{api_base}{path}"
    with httpx.Client(timeout=30) as client:
        response = client.get(url, headers=_codeup_headers(), params=params or {})
    if response.status_code >= 400:
        raise RuntimeError(f"Codeup API 读取失败: {response.status_code} {response.text}")
    return response.json()


def _codeup_post(path: str, *, json_payload: dict | None = None) -> dict | list:
    """执行 Codeup POST 请求。
    
    Args:
        path: API 路径。
        json_payload: 可选的 JSON 请求体。
    
    Returns:
        API 返回的 JSON 字典或列表。
    
    Raises:
        RuntimeError: API 返回 400 及以上状态码。
    """

    api_base = _codeup_api_base()
    url = f"{api_base}{path}"
    with httpx.Client(timeout=30) as client:
        response = client.post(url, headers=_codeup_headers(), json=json_payload or {})
    if response.status_code >= 400:
        raise RuntimeError(f"Codeup API 请求失败: {response.status_code} {response.text}")
    return response.json()


def _check_duplicate_mr(*, owner: str, repo: str, source_branch: str, target_branch: str) -> dict | None:
    """查询相同源分支和目标分支的现有 MR，实现幂等语义。

    Codeup 在重复 MR 时可能返回 400 错误，提前查询可以避免误判为失败。

    Args:
        owner: 仓库 owner。
        repo: 仓库名称。
        source_branch: 源分支名称。
        target_branch: 目标分支名称。

    Returns:
        包含已有 MR 信息的字典，未找到时返回 None。
    """

    api_base = _codeup_api_base()
    repo_id = _resolve_repo_id(owner=owner, repo=repo)
    url = f"{api_base}/repositories/{repo_id}/changeRequests"
    params = {
        "sourceBranch": source_branch,
        "targetBranch": target_branch,
        "state": "opened",
    }
    try:
        with httpx.Client(timeout=30) as client:
            response = client.get(url, headers=_codeup_headers(), params=params)
        if response.status_code >= 400:
            return None
        data = response.json()
        items = data if isinstance(data, list) else data.get("items", data.get("data", []))
        if not items:
            return None
        mr = items[0]
        mr_url = mr.get("webUrl") or mr.get("detailUrl") or ""
        return {
            "html_url": mr_url,
            "webUrl": mr_url,
            "detailUrl": mr_url,
            "localId": mr.get("localId"),
            "reused": True,
            "message": "已复用相同源分支和目标分支的现有 Merge Request",
        }
    except Exception:
        return None


def create_merge_request(
    *,
    owner: str,
    repo: str,
    source_branch: str,
    target_branch: str,
    title: str,
    description: str,
) -> dict:
    """调用 Codeup API 创建 Merge Request。

    Args:
        owner: Codeup 仓库 owner（命名空间）。
        repo: Codeup 仓库名称。
        source_branch: 源分支名称。
        target_branch: 目标分支名称。
        title: MR 标题。
        description: MR 描述。

    Returns:
        Codeup API 返回的 MR JSON；如果检测到重复 MR，则返回带 `reused=True` 的兼容结构。

    Raises:
        RuntimeError: API 返回失败且无法识别为可复用 MR。
    """

    api_base = _codeup_api_base()
    repo_id = _resolve_repo_id(owner=owner, repo=repo)
    project_id = _resolve_project_id(owner=owner, repo=repo)
    url = f"{api_base}/repositories/{repo_id}/changeRequests"
    payload = {
        "title": title,
        "sourceBranch": source_branch,
        "sourceProjectId": project_id,
        "targetBranch": target_branch,
        "targetProjectId": project_id,
        "description": description,
    }
    with httpx.Client(timeout=30) as client:
        response = client.post(url, headers=_codeup_headers(), json=payload)
    if response.status_code >= 400:
        existing = _check_duplicate_mr(
            owner=owner,
            repo=repo,
            source_branch=source_branch,
            target_branch=target_branch,
        )
        if existing is not None:
            return existing
        raise RuntimeError(f"Codeup 创建 MR 失败: {response.status_code} {response.text}")
    return response.json()


def get_merge_request(*, owner: str, repo: str, local_id: int) -> dict:
    """读取 Codeup Merge Request 详情。
    
    Args:
        owner: Codeup 仓库 owner。
        repo: Codeup 仓库名称。
        local_id: MR 局部编号。
    
    Returns:
        MR 详情字典。
    """

    repo_id = _resolve_repo_id(owner=owner, repo=repo)
    data = _codeup_get(f"/repositories/{repo_id}/changeRequests/{local_id}")
    return data if isinstance(data, dict) else {"items": data}


def list_merge_request_commits(*, owner: str, repo: str, local_id: int) -> list:
    """读取 Codeup Merge Request 的提交列表。

    Codeup 没有独立的 MR commits 列表端点，通过 patchsets 获取版本中的提交信息。

    Args:
        owner: Codeup 仓库 owner。
        repo: Codeup 仓库名称。
        local_id: MR 局部编号。

    Returns:
        版本提交信息列表。
    """

    repo_id = _resolve_repo_id(owner=owner, repo=repo)
    data = _codeup_get(f"/repositories/{repo_id}/changeRequests/{local_id}/patchsets")
    items = data if isinstance(data, list) else data.get("items", data.get("data", [])) if isinstance(data, dict) else [data]
    return items


def list_merge_request_files(*, owner: str, repo: str, local_id: int) -> list:
    """读取 Codeup Merge Request 的文件变更列表。

    Codeup 的 changeTree 端点需要 `fromPatchSetId` 和 `toPatchSetId`，
    先读取 patchsets 获取最新版本 ID，再查询变更文件树。

    Args:
        owner: Codeup 仓库 owner。
        repo: Codeup 仓库名称。
        local_id: MR 局部编号。

    Returns:
        文件变更记录列表。
    """

    repo_id = _resolve_repo_id(owner=owner, repo=repo)
    patchsets = _codeup_get(f"/repositories/{repo_id}/changeRequests/{local_id}/patchsets")
    items = patchsets if isinstance(patchsets, list) else patchsets.get("items", patchsets.get("data", [])) if isinstance(patchsets, dict) else []
    if not items:
        return []

    source_patchsets = [p for p in items if p.get("relatedMergeItemType") == "MERGE_SOURCE"]
    target_patchsets = [p for p in items if p.get("relatedMergeItemType") == "MERGE_TARGET"]
    if not source_patchsets or not target_patchsets:
        source_patchsets = items[:1]
        target_patchsets = items[-1:]

    from_id = target_patchsets[0].get("patchSetBizId") or target_patchsets[0].get("commitId", "")
    to_id = source_patchsets[0].get("patchSetBizId") or source_patchsets[0].get("commitId", "")

    data = _codeup_get(
        f"/repositories/{repo_id}/changeRequests/{local_id}/diffs/changeTree",
        params={"fromPatchSetId": from_id, "toPatchSetId": to_id},
    )
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        return data.get("files", data.get("items", data.get("data", [])))
    return [data]


def list_merge_request_comments(*, owner: str, repo: str, local_id: int) -> list:
    """读取 Codeup Merge Request 的评论列表。

    Codeup 的评论列表使用 POST 端点（支持过滤条件），
    不传过滤条件时返回全部评论。

    Args:
        owner: Codeup 仓库 owner。
        repo: Codeup 仓库名称。
        local_id: MR 局部编号。

    Returns:
        评论列表。
    """

    repo_id = _resolve_repo_id(owner=owner, repo=repo)
    data = _codeup_post(f"/repositories/{repo_id}/changeRequests/{local_id}/comments/list")
    return data if isinstance(data, list) else [data]


def _get_latest_patchset_biz_id(*, owner: str, repo: str, local_id: int) -> str:
    """获取 MR 最新版本的 patchset_biz_id。

    Codeup 评论必须关联一个 `patchset_biz_id`，
    这里取最新的 MERGE_SOURCE 版本 ID 作为全局评论的版本关联。

    Args:
        owner: Codeup 仓库 owner。
        repo: Codeup 仓库名称。
        local_id: MR 局部编号。

    Returns:
        最新版本的 patchset_biz_id 字符串。

    Raises:
        RuntimeError: MR 无可用版本或缺少 patchSetBizId。
    """

    repo_id = _resolve_repo_id(owner=owner, repo=repo)
    data = _codeup_get(f"/repositories/{repo_id}/changeRequests/{local_id}/patchsets")
    items = data if isinstance(data, list) else data.get("items", data.get("data", [])) if isinstance(data, dict) else []
    if not items:
        raise RuntimeError(f"Codeup MR 无可用版本: {owner}/{repo}#{local_id}")
    source_patchsets = [p for p in items if p.get("relatedMergeItemType") == "MERGE_SOURCE"]
    target = source_patchsets[0] if source_patchsets else items[0]
    biz_id = target.get("patchSetBizId") or target.get("commitId")
    if not biz_id:
        raise RuntimeError(f"Codeup 版本数据中未找到 patchSetBizId: {target}")
    return str(biz_id)


def post_mr_comment(*, owner: str, repo: str, local_id: int, body: str) -> dict:
    """调用 Codeup API 向 Merge Request 发布全局评论。

    自动获取最新 patchset_biz_id 并构造 GLOBAL_COMMENT。

    Args:
        owner: Codeup 仓库 owner。
        repo: Codeup 仓库名称。
        local_id: MR 局部编号。
        body: 评论正文。

    Returns:
        Codeup API 返回的评论 JSON。

    Raises:
        RuntimeError: API 返回失败状态码。
    """

    repo_id = _resolve_repo_id(owner=owner, repo=repo)
    patchset_biz_id = _get_latest_patchset_biz_id(owner=owner, repo=repo, local_id=local_id)
    url = f"/repositories/{repo_id}/changeRequests/{local_id}/comments"
    payload = {
        "comment_type": "GLOBAL_COMMENT",
        "content": body,
        "draft": False,
        "patchset_biz_id": patchset_biz_id,
        "resolved": False,
    }
    return _codeup_post(url, json_payload=payload)
