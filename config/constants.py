"""项目级共享常量。

集中管理散落在各模块中的魔法字符串、默认值和配置阈值，
避免同一常量在多个文件中重复定义导致不一致。

常量分类:
    - 项目根目录与工作区路径
    - 任务类型
    - 安全与脱敏标记
    - 模型配置与 Agent 运行限制
    - 仓库记忆模板与容量阈值
    - HTTP / SSE / Redis Stream 协议常量
    - 消息清洗与工具入参过滤集合
    - 事件流消费与 Backend 缓存阈值

所有常量使用 ``Final`` 标注，确保类型检查器捕获意外赋值。
"""

from __future__ import annotations

from pathlib import Path
from string import Template
from typing import Final, Literal

from config.env_config import get_env

# ── 项目根目录 ──────────────────────────────────────────────────────────────

PROJECT_ROOT: Final[Path] = Path(__file__).resolve().parents[1]

# ── 工作区目录 ──────────────────────────────────────────────────────────────

WORKSPACE_ROOT: Final[Path] = Path(get_env("AI_WORKSPACE_ROOT", r"E:\ai_workspace")).resolve()

PROJECTS_DIR: Final[Path] = WORKSPACE_ROOT / "projects"

# ── 任务类型 ────────────────────────────────────────────────────────────────

TaskKind = Literal["coding", "analysis", "planning", "qa", "sync", "inspect", "review"]

VALID_TASK_KINDS: Final[frozenset[str]] = frozenset(
    {"coding", "analysis", "planning", "qa", "sync", "inspect", "review"}
)

READ_ONLY_TASK_KINDS: Final[frozenset[str]] = frozenset(
    {"analysis", "planning", "qa", "inspect", "review"}
)

# ── 安全与脱敏 ──────────────────────────────────────────────────────────────

SENSITIVE_MARKERS: Final[tuple[str, ...]] = (
    ".env",
    ".secrets",
    "api_key",
    "apikey",
    "private key",
    "私钥",
)

# ── 模型配置 ────────────────────────────────────────────────────────────────

DEEPSEEK_V4_MAX_TOKENS: Final[int] = 16_000
INTENT_MODEL_MAX_TOKENS: Final[int] = 2_000

# ── Agent 运行限制 ──────────────────────────────────────────────────────────

DEFAULT_RECURSION_LIMIT: Final[int] = 9999
MODEL_CALL_RECURSION_LIMIT: Final[int] = 5000

DEFAULT_MAX_TOOL_CALLS: Final[int] = 240
DEFAULT_MAX_SECONDS: Final[int] = 1800

TASK_KIND_DEFAULT_LIMITS: Final[dict[str, tuple[int, int]]] = {
    "qa": (60, 600),
    "inspect": (30, 300),
    "sync": (40, 300),
    "analysis": (120, 900),
    "planning": (120, 900),
    "review": (180, 1200),
    "coding": (300, 1800),
}

# ── 仓库记忆 ────────────────────────────────────────────────────────────────

REPO_MEMORY_NAMESPACE_PREFIX: Final[tuple[str, str]] = ("xihe-agent", "repo-memory")

INITIAL_REPO_MEMORY_TEMPLATE: Final[Template] = Template("""\
# 仓库记忆：$owner/$repo

## 基本信息
- 仓库地址：$clone_url
- 本地目录：$virtual_project_dir
- 命令目录：$command_repo_dir
- 初始化时间：$now

## 技术栈
- 待分析

## 启动命令
- 待分析

## 测试命令
- 待分析

## 关键文件
- 待分析

## 已知约定
- 文件工具使用 `$virtual_project_dir/...` 这样的虚拟路径。
- `execute` 默认在 `/projects` 对应的本地目录下执行，命令里直接使用 `$command_repo_dir`，不要写 `projects/$command_repo_dir`。
- 不得把 token、私钥、`.env`、`.secrets` 或本机敏感路径写入记忆。
- 如果本记忆与真实仓库文件冲突，以真实文件和实际命令输出为准。

## 最近结论
- 暂无
""")

MAX_REPO_MEMORY_CHARS: Final[int] = 6000
MAX_RECENT_ITEMS: Final[int] = 20
MAX_FACT_CHARS: Final[int] = 350

WORKSPACE_MEMORY_PATH: Final[Path] = PROJECT_ROOT / "agent" / "memory" / "workspace.md"

# ── HTTP 安全请求 ───────────────────────────────────────────────────────────

MAX_REDIRECTS: Final[int] = 5

# ── SSE 流式响应 ────────────────────────────────────────────────────────────

SSE_HEADERS: Final[dict[str, str]] = {
    "Cache-Control": "no-cache, no-transform",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",
}

# ── Redis Stream ────────────────────────────────────────────────────────────

STREAM_KEY_PREFIX: Final[str] = "xihe-agent:sse:events:"
CONSUMER_GROUP: Final[str] = "xihe-agent-dashboard"

# ── Checkpoint 消息过滤 ──────────────────────────────────────────────────────

MIN_MESSAGE_LENGTH: Final[int] = 4

# ── 消息清洗 ────────────────────────────────────────────────────────────────

TEXT_BLOCK_TYPES: Final[frozenset[str]] = frozenset({"text", "input_text", "output_text", "plain_text"})

NON_TEXT_TOOL_BLOCK_TYPES: Final[frozenset[str]] = frozenset({
    "invalid_tool_call",
    "tool_call",
    "tool_call_chunk",
    "server_tool_call",
    "server_tool_call_chunk",
    "server_tool_result",
})

# ── 工具入参清洗 ──────────────────────────────────────────────────────────────

PATH_ARGUMENTS: Final[frozenset[str]] = frozenset({"path", "cwd", "repo_dir", "project_dir", "file_path", "old_path", "new_path"})

GITEE_URL_ARGUMENTS: Final[frozenset[str]] = frozenset({"repo_url"})

GITHUB_URL_ARGUMENTS: Final[frozenset[str]] = frozenset({"repo_url"})

CODEUP_URL_ARGUMENTS: Final[frozenset[str]] = frozenset({"repo_url"})

READ_FILE_INT_ARGUMENTS: Final[frozenset[str]] = frozenset({"offset", "limit"})

# ── 事件流消费 ──────────────────────────────────────────────────────────────

MAX_STREAM_TEXT_LENGTH: Final[int] = 1200

STREAM_FLUSH_THRESHOLD: Final[int] = 24

# ── Backend 缓存 ────────────────────────────────────────────────────────────

MAX_CACHED_BACKENDS: Final[int] = 128
