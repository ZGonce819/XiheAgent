"""仓库 URL 规范化服务。

把前端传入的仓库字段统一整理成后端运行时可解析的 Gitee URL。

前端为了方便展示和输入，通常使用 ``owner/repo`` 这种简写；但运行时、
仓库映射、仓库记忆和 Gitee API 封装都统一依赖完整 URL。这里在 Dashboard
API 边界做一次规范化，避免把 ``msb-goldbin/efg-test`` 直接传给
``parse_gitee_repo_url`` 后触发 500。
"""

from __future__ import annotations

import re

from utils.gitee_utils import parse_gitee_repo_url

DEFAULT_REPO_URL = "https://gitee.com/msb-goldbin/ai_coding.git"


def normalize_repo_url(repo: str | None, *, fallback: str | None = None) -> str:
    """把前端传入的仓库字段规范化为标准 Gitee clone URL。

    支持格式：

    - ``https://gitee.com/owner/repo``
    - ``https://gitee.com/owner/repo.git``
    - ``owner/repo``

    Args:
        repo: 前端传入的仓库地址，可能为空。
        fallback: 当 ``repo`` 为空时使用的备选地址，通常来自已有 thread 的 repo_url。

    Returns:
        规范化后的完整 Gitee HTTPS clone URL。

    Raises:
        ValueError: 仓库地址格式无法解析。
    """

    text = (repo or fallback or DEFAULT_REPO_URL).strip()
    if not text:
        return DEFAULT_REPO_URL

    if text.startswith(("http://", "https://")):
        return parse_gitee_repo_url(text).clone_url

    shorthand = text.removesuffix(".git").strip().strip("/")
    if re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", shorthand):
        return f"https://gitee.com/{shorthand}.git"

    raise ValueError(
        "Gitee 仓库地址格式不正确，请输入完整 Gitee URL 或 owner/repo。"
    )
