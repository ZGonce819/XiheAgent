"""SCM 平台检测、仓库 URL 解析与工具路由。

根据仓库 URL 的 hostname 确定性判断所属平台，避免让大模型自行推断。
运行时在上下文注入阶段调用 ``detect_platform``，把平台名写入 SystemMessage，
模型只需按注入的平台名选择对应工具，不需要自己解析 URL。

``parse_repo_url`` 是全局唯一的仓库 URL 解析入口，所有需要把 URL 转换为
结构化仓库信息的调用方都应使用此函数，不再各自维护平台判断逻辑。
"""

from __future__ import annotations

import re
from typing import Protocol
from urllib.parse import urlparse

# hostname → 平台标识。精确匹配优先，覆盖常见域名变体。
_PLATFORM_HOSTS: dict[str, str] = {
    "gitee.com": "gitee",
    "www.gitee.com": "gitee",
    "github.com": "github",
    "www.github.com": "github",
    "codeup.aliyun.com": "codeup",
    "www.codeup.aliyun.com": "codeup",
}

# 平台标识 → 各工具名称。模型按注入的平台名查表选工具。
PLATFORM_TOOL_MAP: dict[str, dict[str, str]] = {
    "gitee": {
        "open_pr": "open_gitee_pull_request",
        "publish_comment": "publish_gitee_pr_comment",
        "get_context": "get_gitee_pull_request_context",
    },
    "github": {
        "open_pr": "open_github_pull_request",
        "publish_comment": "publish_github_pr_comment",
        "get_context": "get_github_pull_request_context",
    },
    "codeup": {
        "open_pr": "open_codeup_merge_request",
        "publish_comment": "publish_codeup_mr_comment",
        "get_context": "get_codeup_merge_request_context",
    },
}

# 平台标识 → 中文显示名（用于注入给模型的消息）
PLATFORM_DISPLAY_NAMES: dict[str, str] = {
    "gitee": "Gitee",
    "github": "GitHub",
    "codeup": "Codeup（阿里云云效）",
}

SUPPORTED_PLATFORMS = frozenset(PLATFORM_TOOL_MAP.keys())


class UnsupportedPlatformError(ValueError):
    """仓库 URL 不属于已知平台时抛出。"""


def detect_platform(repo_url: str) -> str:
    """根据仓库 URL 的 hostname 确定性判断平台。

    优先精确匹配已知域名。

    Args:
        repo_url: 仓库 HTTPS 或 SSH 克隆地址。

    Returns:
        平台标识字符串：``"gitee"``、``"github"`` 或 ``"codeup"``。

    Raises:
        UnsupportedPlatformError: 无法识别平台时抛出。
    """
    parsed = urlparse(repo_url.strip())
    hostname = (parsed.hostname or "").lower()

    if hostname in _PLATFORM_HOSTS:
        return _PLATFORM_HOSTS[hostname]

    raise UnsupportedPlatformError(
        f"无法识别的代码托管平台：{hostname or repo_url!r}。"
        f"当前支持：{', '.join(sorted(SUPPORTED_PLATFORMS))}"
    )


def get_platform_tools(platform: str) -> dict[str, str]:
    """获取指定平台的工具名称映射。

    Args:
        platform: 平台标识，由 ``detect_platform`` 返回。

    Returns:
        包含 ``open_pr``、``publish_comment``、``get_context`` 三个工具名的字典。

    Raises:
        KeyError: 平台标识不在已知列表中。
    """
    return PLATFORM_TOOL_MAP[platform]


def get_platform_display_name(platform: str) -> str:
    """获取平台的中文显示名。

    Args:
        platform: 平台标识，由 ``detect_platform`` 返回。

    Returns:
        平台显示名；未知平台时原样返回 ``platform``。
    """
    return PLATFORM_DISPLAY_NAMES.get(platform, platform)


class ScmRepo(Protocol):
    """任意 SCM 平台的仓库信息协议。

    只要具备 owner、repo 和 clone_url 三个字段即可满足项目内所有
    仓库映射、记忆管理和 API 调用的需要。
    """

    owner: str
    repo: str
    clone_url: str


def parse_repo_url(repo_url: str) -> ScmRepo:
    """根据 URL 域名自动识别 SCM 平台并解析为结构化仓库信息。

    这是全局唯一的仓库 URL 解析入口。所有需要把 URL 转换为
    owner/repo/clone_url 的调用方都应使用此函数。

    Args:
        repo_url: 完整的仓库 HTTPS URL。

    Returns:
        满足 ``ScmRepo`` 协议的仓库信息对象。

    Raises:
        UnsupportedPlatformError: URL 不属于任何已知平台。
        ValueError: URL 路径中无法解析出仓库信息。
    """
    from utils.codeup_utils import parse_codeup_repo_url
    from utils.gitee_utils import parse_gitee_repo_url
    from utils.github_utils import parse_github_repo_url

    platform = detect_platform(repo_url)
    if platform == "codeup":
        return parse_codeup_repo_url(repo_url)
    if platform == "github":
        return parse_github_repo_url(repo_url)
    return parse_gitee_repo_url(repo_url)


def normalize_repo_url(repo: str | None, *, fallback: str | None = None) -> str:
    """把前端传入的仓库字段规范化为标准 clone URL。

    支持 Gitee、GitHub 和 Codeup 三个平台，自动按域名识别。

    支持格式：

    - ``https://gitee.com/owner/repo``
    - ``https://github.com/owner/repo.git``
    - ``https://codeup.aliyun.com/org_id/owner/repo``
    - ``owner/repo``（简写默认 Gitee）

    Args:
        repo: 前端传入的仓库地址，可能为空。
        fallback: 当 ``repo`` 为空时使用的备选地址，通常来自已有 thread 的 repo_url。

    Returns:
        规范化后的完整 HTTPS clone URL。

    Raises:
        ValueError: 仓库地址缺失或格式无法解析。
    """
    text = (repo or fallback or "").strip()
    if not text:
        raise ValueError("未提供仓库地址，请先配置仓库 URL。")

    if text.startswith(("http://", "https://")):
        return parse_repo_url(text).clone_url

    shorthand = text.removesuffix(".git").strip().strip("/")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", shorthand):
        return f"https://gitee.com/{shorthand}.git"

    raise ValueError(
        "仓库地址格式不正确，请输入完整的 Gitee / GitHub / Codeup URL 或 owner/repo（简写默认 Gitee）。"
    )
