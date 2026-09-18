"""技能管理工具。

给智能体提供一条**受控的**技能创建通道。之所以要做成工具而不是直接放开
`/skills` 目录的写权限，原因有两点：

1. ``backends/local_shell.py`` 把 ``skills`` 目录硬编码为只读资产，防止模型
   通过 shell 篡改自己的行为准则。这个守卫不能撤，否则等于给了模型自修改
   权限。
2. 技能内容有严格规范（frontmatter 必填 ``name`` 与 ``description``，名称格式
   受限），直接写文件很容易产出加载器静默丢弃的幽灵技能。

因此这里的做法是：工具通过 :mod:`services.skill_registry` 在进程内校验并落盘，
绕过虚拟路径层的写守卫，但**必须**经过校验与来源标记，并把创建结果写回事件流，
保证可追溯。
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool

from utils.context_utils import get_runtime_thread_id


def _resolve_scope(scope: str, thread_id: str | None) -> tuple[str, str | None]:
    """把工具入参中的作用域解析为 registry 可用的形式。

    - ``global``：写入全局技能目录，跨项目共享。
    - ``project_set``：写入全局目录下的项目集合，随项目走但不需要项目绑定本地
      文件夹。
    - ``local``：写入当前会话所属项目文件夹内的 ``.xihe-agent/skills``，要求
      项目已绑定本地文件夹；未绑定时自动降级为 ``project_set``，再不行降为
      ``global``，避免工具因环境缺失而直接失败。

    Args:
        scope: 模型传入的作用域，``global`` / ``project_set`` / ``local``。
        thread_id: 当前会话 ID，用于反查项目。

    Returns:
        ``(scope, project_name)`` 二元组。
    """
    from services.skill_registry import (
        GLOBAL_SCOPE,
        LOCAL_SCOPE,
        PROJECT_SET_SCOPE,
        resolve_project_folder,
    )

    normalized = (scope or GLOBAL_SCOPE).strip().lower()
    if normalized not in {PROJECT_SET_SCOPE, LOCAL_SCOPE} or not thread_id:
        return GLOBAL_SCOPE, None

    project_name: str | None = None
    try:
        from agent.graph.graph import get_store

        thread = get_store().get_thread(thread_id)
        raw = thread.get("project_name") if thread else None
        if isinstance(raw, str) and raw.strip():
            project_name = raw.strip()
    except Exception:
        project_name = None

    if not project_name:
        return GLOBAL_SCOPE, None
    if normalized == LOCAL_SCOPE:
        if resolve_project_folder(project_name) is None:
            # 本地层要求真实磁盘目录，缺失时退到项目集合，仍保留项目归属。
            return PROJECT_SET_SCOPE, project_name
        return LOCAL_SCOPE, project_name
    return PROJECT_SET_SCOPE, project_name


@tool
def create_skill(
    name: str,
    description: str,
    instructions: str,
    scope: str = "global",
) -> dict[str, Any]:
    """创建或更新一个可复用的技能。

    适用场景：完成一次任务后，发现其中包含一套值得沉淀的固定流程、检查清单或
    领域知识，希望以后能自动复用。技能会被写入技能目录，下次构建智能体时自动
    注册，本会话内不会立即生效。

    Args:
        name: 技能名，只能包含小写字母、数字和连字符（如 ``db-migration-check``）。
            中文名会被自动转换为编号标识，建议直接用英文短横线命名。
        description: 技能说明，写清"什么时候该用这个技能"。这段文字是模型判断
            是否加载技能的唯一依据，最多 1024 字符，务必具体、包含触发条件。
        instructions: 技能正文，Markdown 格式。写清具体步骤、检查项、命令和
            注意事项，而不是笼统的建议。
        scope: ``global`` 跨项目共享（默认）；``project_set`` 仅当前项目可见，
            存在全局目录下的项目集合里；``local`` 仅当前项目可见，存在项目
            文件夹内，要求项目已绑定本地文件夹。越靠后的层同名时覆盖前面的层。

    Returns:
        包含 ``created``、``skill_id``、``scope``、``virtual_path`` 与 ``message``
        的字典。失败时返回带 ``error`` 的字典，不会抛出异常。
    """
    from services.skill_registry import (
        SkillValidationError,
        create_skill as registry_create_skill,
        scope_dir,
    )

    thread_id = get_runtime_thread_id()

    if not (name or "").strip():
        return {"error": "技能名不能为空"}
    if not (description or "").strip():
        return {"error": "技能说明不能为空，它是模型判断是否加载该技能的依据"}
    if not (instructions or "").strip():
        return {"error": "技能正文不能为空"}

    resolved_scope, project_name = _resolve_scope(scope, thread_id)
    requested = (scope or "").strip().lower()
    downgraded = requested in {"project_set", "local", "project"} and resolved_scope != requested

    try:
        record = registry_create_skill(
            name=name.strip(),
            description=description.strip(),
            body=instructions,
            scope=resolved_scope,
            project_name=project_name,
            origin="agent",
            overwrite=True,
        )
    except SkillValidationError as e:
        return {"error": str(e)}
    except OSError as e:
        return {"error": f"技能写入失败：{e}"}

    directory = scope_dir(resolved_scope, project_name) / record.id

    message = f"已创建技能「{record.name}」（{record.scope} 作用域），将在下一次构建智能体时自动加载。"
    if downgraded:
        message += (
            "当前项目未绑定本地文件夹或未指定项目，已自动保存为"
            f"{record.scope}技能。"
        )

    return {
        "created": True,
        "skill_id": record.id,
        "name": record.name,
        "scope": record.scope,
        "virtual_path": record.virtual_path,
        "path": str(directory),
        "message": message,
    }


@tool
def list_skills(scope: str = "all") -> dict[str, Any]:
    """列出当前可用的技能。

    在决定是否新建技能之前先调用本工具，可以避免重复创建功能重叠的技能。

    Args:
        scope: ``global`` 只看全局技能，``project_set`` 只看当前项目集合，
            ``local`` 只看当前项目文件夹内的技能，``all`` 返回全部（默认）。

    Returns:
        包含 ``skills`` 列表与 ``count`` 的字典。每项含 ``id``、``name``、
        ``description``、``scope`` 与 ``origin``。
    """
    from services.skill_registry import (
        GLOBAL_SCOPE,
        LOCAL_SCOPE,
        PROJECT_SET_SCOPE,
        list_skills as registry_list,
    )

    thread_id = get_runtime_thread_id()
    normalized = (scope or "all").strip().lower()
    if normalized == "project":
        normalized = LOCAL_SCOPE

    records = []
    if normalized in {"all", GLOBAL_SCOPE}:
        records.extend(registry_list(GLOBAL_SCOPE))
    if normalized in {"all", PROJECT_SET_SCOPE}:
        resolved, project_name = _resolve_scope(PROJECT_SET_SCOPE, thread_id)
        if resolved in {PROJECT_SET_SCOPE, LOCAL_SCOPE} and project_name:
            records.extend(registry_list(PROJECT_SET_SCOPE, project_name))
    if normalized in {"all", LOCAL_SCOPE}:
        resolved, project_name = _resolve_scope(LOCAL_SCOPE, thread_id)
        if resolved == LOCAL_SCOPE and project_name:
            records.extend(registry_list(LOCAL_SCOPE, project_name))

    return {
        "count": len(records),
        "skills": [
            {
                "id": record.id,
                "name": record.name,
                "description": record.description,
                "scope": record.scope,
                "origin": record.origin,
            }
            for record in records
        ],
    }
