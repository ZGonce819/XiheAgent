"""SCM 平台检测与工具路由。

根据仓库 URL 的 hostname 确定性判断所属平台，避免让大模型自行推断。
运行时在上下文注入阶段调用 ``detect_platform``，把平台名写入 SystemMessage，
模型只需按注入的平台名选择对应工具，不需要自己解析 URL。
"""

from __future__ import annotations

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
    """获取平台的中文显示名。"""
    return PLATFORM_DISPLAY_NAMES.get(platform, platform)
