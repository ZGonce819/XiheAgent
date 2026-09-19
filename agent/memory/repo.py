"""仓库级长期记忆管理。

每个 SCM 仓库（Gitee / GitHub / Codeup）拥有独立的记忆文件
（``/memories/{owner}/{repo}.md``），通过 LangGraph Store（PostgreSQL）持久化。
本模块提供：

- namespace / store key 生成
- 首次记忆模板构建
- 记忆文件的读取与初始化
"""

from __future__ import annotations

from datetime import UTC, datetime

from deepagents.backends.utils import create_file_data
from langgraph.store.base import BaseStore

from config.constants import INITIAL_REPO_MEMORY_TEMPLATE, REPO_MEMORY_NAMESPACE_PREFIX
from utils.platform_utils import ScmRepo


def repo_memory_virtual_path(owner: str, repo: str) -> str:
    """生成 Agent 可见的记忆文件虚拟路径。

    路径按 owner/repo 命名，多仓库各自独立。Agent 通过此路径读写记忆文件，
    CompositeBackend 会将 ``/memories/`` 前缀路由到 StoreBackend。

    Args:
        owner: 仓库 owner（用户或组织名）。
        repo: 仓库名称。

    Returns:
        虚拟文件路径，格式为 ``/memories/{owner}/{repo}.md``。
    """
    return f"/memories/{owner}/{repo}.md"


def repo_memory_store_key(owner: str, repo: str) -> str:
    """生成 LangGraph Store 中使用的内部 key。

    Agent 访问的是 ``/memories/{owner}/{repo}.md``。CompositeBackend 会把
    ``/memories/`` 路由前缀剥离后再交给 StoreBackend，因此 Store 里
    实际保存的 key 是 ``/{owner}/{repo}.md``。

    Args:
        owner: 仓库 owner（用户或组织名）。
        repo: 仓库名称。

    Returns:
        Store 内部 key，格式为 ``/{owner}/{repo}.md``。
    """
    return f"/{owner}/{repo}.md"


def repo_project_dir(repo: ScmRepo, *, local_folder_mode: bool = False) -> str:
    """根据仓库信息生成本地项目目录。

    只要前端传入仓库地址，后端就可以从 URL 解析出 repo 名称，
    并稳定落到 ``projects/<repo>``。本地目录、命令目录和仓库记忆路径
    都由同一个 owner/repo 规则推导，避免多套映射关系互相覆盖。

    本地文件夹模式下，用户选定的文件夹本身就是项目根（文件夹即项目根），
    仓库目录即当前目录 ``.``，不再有 ``projects/<repo>`` 中间层。

    Args:
        repo: 已解析的仓库信息，至少包含 ``repo`` 属性。
        local_folder_mode: 是否为本地文件夹模式。

    Returns:
        本地项目目录：全局模式为 ``projects/{repo}``，本地文件夹模式为 ``.``。
    """
    if local_folder_mode:
        return "."
    return f"projects/{repo.repo}"


def build_repo_memory_namespace(owner: str, repo: str) -> tuple[str, ...]:
    """生成仓库级长期记忆的 StoreBackend namespace。

    每个仓库使用独立 namespace，不同仓库的记忆文件路径不同、
    namespace 也不同，双重隔离。

    Args:
        owner: 仓库 owner（用户或组织名），会转为小写。
        repo: 仓库名称，会转为小写。

    Returns:
        四元组 namespace：``("xihe-agent", "repo-memory", owner, repo)``。
    """
    return (*REPO_MEMORY_NAMESPACE_PREFIX, owner.lower(), repo.lower())


def _extract_owner_repo_from_namespace(namespace: tuple[str, ...]) -> tuple[str, str] | None:
    """从 namespace 中提取 owner 和 repo。

    namespace 格式为 ``("xihe-agent", "repo-memory", owner, repo)``。
    当前两段必须匹配 :data:`REPO_MEMORY_NAMESPACE_PREFIX`，否则视为非法 namespace。

    Args:
        namespace: LangGraph Store 中的 namespace 元组。

    Returns:
        匹配时返回 ``(owner, repo)``；不匹配时返回 ``None``。
    """
    if len(namespace) >= 4 and namespace[:2] == REPO_MEMORY_NAMESPACE_PREFIX:
        return namespace[2], namespace[3]
    return None


def build_initial_repo_memory(
    *, repo: ScmRepo, project_dir: str, sandbox_mode: bool = False
) -> str:
    """生成首次识别仓库时的记忆文件模板。

    不猜测技术栈和启动命令，只写入已确定的仓库地址、本地目录和安全约定。
    后续由 Agent 在真实分析项目后，通过 ``edit_file`` 更新稳定结论。

    Args:
        repo: 已解析的仓库信息，包含 owner、repo、clone_url。
        project_dir: 本地项目目录，用于推导虚拟路径和命令目录。
        sandbox_mode: 是否使用远程沙盒的真实目录布局。

    Returns:
        Markdown 格式的记忆文件内容。
    """
    now = datetime.now(UTC).isoformat()
    normalized = project_dir.replace("\\", "/").strip()
    if sandbox_mode:
        virtual_project_dir = f"/workspace/projects/{repo.repo}"
        command_repo_dir = virtual_project_dir
        execute_base_dir = "/workspace"
    elif normalized in {".", "", "/"}:
        # 本地文件夹模式：文件夹即项目根，虚拟路径为 /projects，命令目录为当前目录。
        virtual_project_dir = "/projects"
        command_repo_dir = "."
        execute_base_dir = "/projects"
    else:
        virtual_project_dir = "/" + normalized.strip("/")
        command_repo_dir = normalized.removeprefix("projects/").strip("/")
        command_repo_dir = command_repo_dir or repo.repo
        execute_base_dir = "/projects"
    return INITIAL_REPO_MEMORY_TEMPLATE.substitute(
        owner=repo.owner,
        repo=repo.repo,
        clone_url=repo.clone_url,
        virtual_project_dir=virtual_project_dir,
        command_repo_dir=command_repo_dir,
        execute_base_dir=execute_base_dir,
        now=now,
    )


def get_repo_memory_item(store: BaseStore, namespace: tuple[str, ...]):
    """读取指定 namespace 下的仓库记忆条目。

    Agent 可见路径为 ``/memories/{owner}/{repo}.md``，
    数据库内部 key 由 :func:`repo_memory_store_key` 生成。

    Args:
        store: LangGraph Store 实例。
        namespace: 已构建的 namespace 元组，必须包含有效的前缀和 owner/repo。

    Returns:
        Store 中对应的数据条目；namespace 非法或条目不存在时返回 ``None``。
    """
    owner_repo = _extract_owner_repo_from_namespace(namespace)
    if owner_repo is None:
        return None

    owner, repo = owner_repo
    return store.get(namespace, repo_memory_store_key(owner, repo))


def ensure_repo_memory_initialized(
    *,
    store: BaseStore,
    repo: ScmRepo,
    project_dir: str,
    sandbox_mode: bool = False,
) -> bool:
    """确保指定仓库的记忆文件已初始化。

    文件路径为 ``/memories/{owner}/{repo}.md``。只检查当前标准 key，
    不读取或迁移旧版路径。已有文件不会被覆盖。

    Args:
        store: LangGraph Store 实例。
        repo: 已解析的仓库信息。
        project_dir: 本地项目目录，用于生成记忆模板中的路径信息。
        sandbox_mode: 是否使用远程沙盒的真实目录布局。

    Returns:
        本次新建了记忆文件返回 ``True``；文件已存在返回 ``False``。
    """
    namespace = build_repo_memory_namespace(repo.owner, repo.repo)
    new_key = repo_memory_store_key(repo.owner, repo.repo)

    existing = store.get(namespace, new_key)
    if existing is not None:
        return False

    initial_content = build_initial_repo_memory(
        repo=repo,
        project_dir=project_dir,
        sandbox_mode=sandbox_mode,
    )
    store.put(namespace, new_key, create_file_data(initial_content))
    return True
