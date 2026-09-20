"""技能注册服务模块。

把技能的「解析 / 校验 / 落盘 / 列举 / 删除」收敛到一处，作为技能链路的
唯一事实来源。历史上这些逻辑散落在 ``services/skill_installer.py``（拷贝）
与 dashboard 的技能接口（解析）里，两处容易分叉，导致出现
「接口认为有效、但 SkillsMiddleware 加载不到」的幽灵技能。

## 作用域模型

技能按作用域分成四层，加载时后写的源覆盖先写的源：

================  ====================================================  ==================
作用域            物理落点                                               虚拟路径
================  ====================================================  ==================
``builtin``       ``PROJECT_ROOT/agent/skills``                          不挂载，仅模板源
``global``        ``Settings.skills_dir``                                ``/skills-global/``
``project_set``   ``<全局技能目录>/.project-set/<集合名>``               ``/skills-set/``
``local``         ``<项目本地文件夹>/.xihe-agent/skills``                ``/skills/``
================  ====================================================  ==================

``project_set`` 是**用户在全局目录下创建的技能集合**，与业务项目一一对应
（集合名即项目名）。它比全局层更贴近具体工作，又不像 ``local`` 那样要求
项目绑定本地文件夹，因此适合「跨机器同步的项目级技能」。

``local`` 作用域只在项目绑定了本地文件夹（``project_local_folders`` 表）
时才存在，指向磁盘上该项目文件夹内的 ``.xihe-agent/skills``。

沙箱会话（``backend_type == "sandbox"``）可用层数少一层：容器里没有项目本地
文件夹，``local`` 不可用；但全局层与项目集合层都会同步进容器，落点由
:data:`SANDBOX_SKILL_ROOT` 决定，见 :func:`sandbox_skill_sources`。

历史兼容：``project`` 是 ``local`` 的旧称，:func:`normalize_scope` 会把它们
统一折叠到 ``local``，避免旧数据与旧调用方失效。

## 校验规则

校验口径必须与 ``deepagents.middleware.skills`` 完全一致，否则会静默丢技能：

- ``SKILL.md`` 必须以 ``---`` 开头的 YAML frontmatter 起始；
- frontmatter 必须是映射，且 ``name`` 与 ``description`` 均非空；
- ``name`` 需符合 Agent Skills 规范：小写字母 / 数字 / 连字符，≤64 字符；
- ``description`` 规范上限 1024 字符（超出会被中间件截断）；
- 单文件大小不超过 :data:`MAX_SKILL_FILE_SIZE`。

Example:
    >>> from services.skill_registry import list_skills
    >>> [item["id"] for item in list_skills("global")]
    ['code-review']
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from config.app_config import get_settings
from config.constants import PROJECT_ROOT

logger = logging.getLogger(__name__)

Scope = Literal["builtin", "global", "project_set", "local"]

BUILTIN_SCOPE: Scope = "builtin"
GLOBAL_SCOPE: Scope = "global"
PROJECT_SET_SCOPE: Scope = "project_set"
LOCAL_SCOPE: Scope = "local"

# 旧称兼容：project 曾同时表示「项目级技能」，现拆成 project_set 与 local。
LEGACY_PROJECT_SCOPE = "project"

SCOPE_LABELS: dict[str, str] = {
    BUILTIN_SCOPE: "内置",
    GLOBAL_SCOPE: "全局",
    PROJECT_SET_SCOPE: "项目",
    LOCAL_SCOPE: "本地",
}

# 项目集合在全局技能目录下的容器目录名。以点开头，因此不会被
# :func:`list_skills` 当作普通技能目录枚举。
PROJECT_SET_DIR_NAME = ".project-set"

SKILL_FILE_NAME = "SKILL.md"
ORIGIN_MARKER_NAME = ".origin"

# 与 backends/local_shell.py 的虚拟路径常量保持一致。
VIRTUAL_LOCAL_SKILLS = "/skills/"
VIRTUAL_GLOBAL_SKILLS = "/skills-global/"
VIRTUAL_PROJECT_SET_SKILLS = "/skills-set/"

# 沙箱容器内的技能根目录。
#
# 沙箱 backend 不做虚拟路径映射（``OpenSandboxBackend`` 直接把路径当容器内真实
# 路径使用），因此沙箱会话的技能源必须写成容器内真实路径。``/workspace`` 是
# OpenSandbox 的默认工作目录，容器内布局刻意与主机保持一致：全局层直接放在
# ``/workspace/skills``，项目集合层放在 ``/workspace/.project-set/<集合名>``，
# 这样两层的覆盖关系与目录语义和本地环境完全相同。
#
# 注意：项目集合层刻意放在 ``/workspace/.project-set/`` 而非
# ``/workspace/skills/.project-set/``。后者位于全局技能根目录内部，会被
# ``SkillsMiddleware`` 的 ``ls()`` 扫描到并当作候选技能目录，触发对
# ``.project-set/SKILL.md`` 的 404 读取。第三方框架不过滤以点开头的目录，
# 因此只能从布局上避免——把项目集合层移出全局技能根目录即可。
SANDBOX_SKILL_ROOT = "/workspace/skills"

# 与 deepagents.middleware.skills 对齐的硬约束。
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
MAX_SKILL_NAME_LENGTH = 64
MAX_SKILL_DESCRIPTION_LENGTH = 1024
MAX_SKILL_FILE_SIZE = 512_000
MAX_SKILL_TOTAL_SIZE = 8_000_000
MAX_SKILL_TOTAL_FILES = 200


class SkillValidationError(ValueError):
    """技能内容不符合规范时抛出。

    调用方（HTTP 接口或 Agent 工具）应把该异常转换为 400 级错误，
    而不是静默忽略，避免产生加载不到的幽灵技能。
    """


@dataclass
class SkillRecord:
    """一个已落盘技能的元数据。

    Attributes:
        id: 技能目录名，也是同作用域内的唯一标识。
        name: frontmatter 中的 ``name``。
        description: frontmatter 中的 ``description``，或首个一级标题。
        scope: 技能所属作用域，``builtin`` / ``global`` / ``project_set`` / ``local``。
        project_name: ``project_set`` 的集合名，或 ``local`` 的项目名；``global`` 为 ``None``。
        directory: 技能目录的真实绝对路径。
        virtual_path: 智能体视角下的虚拟路径。
        origin: 来源标记，``builtin`` / ``user`` / ``agent``。
        updated_at: ``SKILL.md`` 的修改时间（UTC ISO 8601）。
        extra_files: 除 ``SKILL.md`` 外的附属文件相对路径列表。
        source_path: 该技能实际生效的文件路径，便于前端展示与排错。
    """

    id: str
    name: str
    description: str
    scope: Scope
    project_name: str | None
    directory: Path
    virtual_path: str
    origin: str = "user"
    updated_at: str = ""
    extra_files: list[str] = field(default_factory=list)
    source_path: str = ""

    def to_payload(self) -> dict[str, Any]:
        """转换为前端 / 接口使用的字典。

        Returns:
            可直接 JSON 序列化的技能描述字典，字段名保持与既有接口兼容
            （``virtualPath``、``updatedAt`` 为驼峰）。
        """
        result: dict[str, Any] = {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "scope": self.scope,
            "scopeLabel": SCOPE_LABELS.get(self.scope, self.scope),
            "projectName": self.project_name,
            "virtualPath": self.virtual_path,
            "origin": self.origin,
            "updatedAt": self.updated_at,
            "extraFiles": list(self.extra_files),
            "sourcePath": self.source_path,
        }
        if self.scope == "local":
            try:
                result["localFolderName"] = self.directory.parent.parent.parent.name
            except (AttributeError, IndexError):
                logger.debug("无法解析本地技能文件夹名：path=%s", self.directory)
        return result


# ── 内容解析与校验 ──────────────────────────────────────────────────────────


def parse_frontmatter(text: str) -> dict[str, Any]:
    """解析 ``SKILL.md`` 开头的 YAML frontmatter。

    只做轻量解析，不引入 PyYAML 依赖：技能 frontmatter 实际只用到
    ``name``、``description``、``license``、``compatibility`` 这类扁平标量，
    以及元数据映射。

    Args:
        text: ``SKILL.md`` 的完整文本。

    Returns:
        解析出的键值对；没有合法 frontmatter 时返回空字典。

    Note:
        解析失败一律返回空字典而不抛异常，由 :func:`validate_skill_content`
        统一给出可读的错误信息。
    """
    if not text.startswith("---"):
        return {}
    match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.DOTALL)
    if not match:
        return {}
    block = match.group(1)

    data: dict[str, Any] = {}
    for raw_line in block.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        # 去掉成对的引号
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        data[key] = value
    return data


def split_frontmatter(text: str) -> tuple[str, str]:
    """把 ``SKILL.md`` 拆成 frontmatter 块与正文。

    Args:
        text: 完整文本。

    Returns:
        ``(frontmatter 内文本, 正文)``；没有 frontmatter 时前者为空串。
    """
    if not text.startswith("---"):
        return "", text
    match = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n|$)", text, re.DOTALL)
    if not match:
        return "", text
    return match.group(1), text[match.end() :]


def split_skill_name(raw_name: str) -> list[str]:
    """按 Agent Skills 规范拆解技能名。

    Args:
        raw_name: 用户输入的技能名或描述。

    Returns:
        规范化的片段列表；无可用片段时返回空列表。
    """
    lowered = raw_name.strip().lower()
    parts = re.split(r"[^a-z0-9]+", lowered)
    return [part for part in parts if part]


def slugify_skill_name(raw_name: str) -> str:
    """把任意文本规范化为合法的技能标识。

    中文字符会被丢弃，因此纯中文输入会得到空串，调用方需要提供回退名。

    Args:
        raw_name: 用户输入的技能名。

    Returns:
        规范化后的技能标识；无法规范化时返回空串。
    """
    return "-".join(split_skill_name(raw_name))


def coerce_ascii_name(raw_name: str) -> str:
    """把可能含中文的技能名转换为合法标识。

    Agent 自动创建技能时常用中文名，直接丢弃非法字符会得到空串，因此退化为
    按字符码求和生成的编号形式，保证结果始终是合法且可用的标识。

    Args:
        raw_name: 原始技能名。

    Returns:
        合法的技能标识，保证非空且以字母开头。
    """
    slug = slugify_skill_name(raw_name)
    if slug:
        return slug
    digest = sum(ord(char) for char in raw_name.strip()) or 0
    return f"skill-{digest:x}" if digest else "skill-untitled"


def sanitize_dir_name(raw_name: str) -> str:
    """把用户输入的目录名清洗为可安全落盘的目录名，保留原始大小写。

    与 :func:`coerce_ascii_name` 的区别是**不做大小写折叠**：项目集合是用户
    自己命名的东西，``Xihe`` 就应该落成 ``Xihe`` 而不是 ``xihe``。这里的职责
    只是去掉路径分隔符、``..`` 等会造成目录逃逸的字符，以及文件系统不接受的
    字符，然后把空白折叠成连字符。

    Args:
        raw_name: 用户输入的目录名。

    Returns:
        清洗后的目录名；无任何可用字符时返回空串。
    """
    # 先把路径分隔符与保留字符替换掉，防止逃逸出容器目录。
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', "-", raw_name.strip())
    # 折叠空白为连字符，保持目录名可直接阅读且不含空格。
    cleaned = re.sub(r"\s+", "-", cleaned)
    # 去掉开头结尾的连字符与点（``.`` / ``..`` 会指向目录本身或父目录）。
    cleaned = cleaned.strip("-. ")
    return cleaned


def build_skill_document(name: str, description: str, body: str) -> str:
    """组装一份规范的 ``SKILL.md`` 文本。

    Args:
        name: 技能标识。
        description: 技能说明。
        body: 正文（不含 frontmatter）。

    Returns:
        以 ``---`` 起始、包含 frontmatter 的完整 Markdown 文本。
    """
    safe_description = description.strip().replace("\n", " ")
    text = f"---\nname: {name}\ndescription: {safe_description}\n---\n\n{body.strip()}\n"
    return text


def validate_skill_content(text: str, directory_name: str | None = None) -> dict[str, str]:
    """校验收到的 ``SKILL.md`` 是否会被 SkillsMiddleware 接受。

    Args:
        text: ``SKILL.md`` 的完整文本。
        directory_name: 技能目录名，用于检查名称一致性。

    Returns:
        ``{"name": ..., "description": ...}``，其中 ``name`` 已规范化。

    Raises:
        SkillValidationError: 缺失 frontmatter / 必填字段，或字段超出规范上限。
    """
    if not text.strip():
        raise SkillValidationError("技能内容不能为空")
    if len(text.encode("utf-8")) > MAX_SKILL_FILE_SIZE:
        raise SkillValidationError(
            f"SKILL.md 过大（上限 {MAX_SKILL_FILE_SIZE // 1000} KB）"
        )

    frontmatter = parse_frontmatter(text)
    if not frontmatter:
        raise SkillValidationError(
            "SKILL.md 必须以 YAML frontmatter 开头，格式为 --- 包围的 name 与 description"
        )

    name = str(frontmatter.get("name") or "").strip()
    description = str(frontmatter.get("description") or "").strip()
    if not name:
        raise SkillValidationError("frontmatter 缺少必填字段 name")
    if not description:
        raise SkillValidationError("frontmatter 缺少必填字段 description")
    if len(name) > MAX_SKILL_NAME_LENGTH:
        raise SkillValidationError(f"技能名过长（上限 {MAX_SKILL_NAME_LENGTH} 字符）")
    if not NAME_PATTERN.match(name):
        raise SkillValidationError(
            "技能名只能包含小写字母、数字和连字符，且不能以连字符开头或结尾"
        )
    if len(description) > MAX_SKILL_DESCRIPTION_LENGTH:
        raise SkillValidationError(
            f"技能说明过长（上限 {MAX_SKILL_DESCRIPTION_LENGTH} 字符），"
            "超出部分会被加载器丢弃"
        )
    if directory_name and directory_name != name:
        raise SkillValidationError(
            f"技能名与目录名不一致：frontmatter 为 {name}，目录为 {directory_name}"
        )
    return {"name": name, "description": description}


# ── 目录定位 ────────────────────────────────────────────────────────────────


def builtin_skills_dir() -> Path:
    """返回随产品发布的内置技能目录。

    Returns:
        ``PROJECT_ROOT/agent/skills``。
    """
    return PROJECT_ROOT / "agent" / "skills"


def global_skills_dir() -> Path:
    """返回全局作用域的技能目录。

    Returns:
        :attr:`config.app_config.Settings.skills_dir`。
    """
    return get_settings().skills_dir


def project_set_root() -> Path:
    """返回项目集合的容器目录。

    Returns:
        全局技能目录下的 ``.project-set``。
    """
    return global_skills_dir() / PROJECT_SET_DIR_NAME


def project_set_dir(project_set_name: str) -> Path:
    """返回某个项目集合的技能目录。

    集合名是用户自己起的，因此**保留原始大小写**（``Xihe`` 落成 ``Xihe``）；
    只清洗掉路径分隔符、``..`` 等会逃逸出容器目录的字符。

    Args:
        project_set_name: 项目集合名，通常是业务项目名。

    Returns:
        ``<全局技能目录>/.project-set/<集合名>``。

    Raises:
        SkillValidationError: 集合名为空时。
    """
    slug = sanitize_dir_name(project_set_name)
    if not slug:
        raise SkillValidationError("项目集合名不能为空")
    return project_set_root() / slug


def local_skills_dir(project_name: str) -> Path:
    """返回项目本地作用域的技能目录。

    Args:
        project_name: 项目名。

    Returns:
        该项目绑定文件夹下的 ``.xihe-agent/skills``。

    Raises:
        SkillValidationError: 项目未绑定本地文件夹时。
    """
    folder = resolve_project_folder(project_name)
    if folder is None:
        raise SkillValidationError(
            f"项目「{project_name}」尚未绑定本地文件夹，无法使用本地技能"
        )
    return folder / ".xihe-agent" / "skills"


def resolve_project_folder(project_name: str) -> Path | None:
    """按项目名反查绑定的本地文件夹。

    Args:
        project_name: 项目名。

    Returns:
        已解析的绝对路径；未绑定或不可用时返回 ``None``。
    """
    if not project_name or not project_name.strip():
        return None
    try:
        from agent.graph.graph import get_store

        record = get_store().get_project_folder(project_name.strip())
    except Exception:
        return None
    folder = record.get("local_folder") if record else None
    if not isinstance(folder, str) or not folder.strip():
        return None
    path = Path(folder).expanduser()
    return path.resolve() if path.exists() else None


def normalize_scope(scope: str | None) -> Scope:
    """把任意作用域写法折叠到规范标识。

    主要用途是兼容旧调用方使用的 ``project``（历史名称），它现在等价于
    ``local``。

    Args:
        scope: 原始作用域字符串；为空时视为 ``global``。

    Returns:
        规范化的作用域标识。

    Raises:
        SkillValidationError: 无法识别的作用域。
    """
    normalized = (scope or GLOBAL_SCOPE).strip().lower()
    if normalized == LEGACY_PROJECT_SCOPE:
        return LOCAL_SCOPE
    if normalized in {BUILTIN_SCOPE, GLOBAL_SCOPE, PROJECT_SET_SCOPE, LOCAL_SCOPE}:
        return normalized  # type: ignore[return-value]
    raise SkillValidationError(
        f"未知的技能作用域：{scope}（可选 global / project_set / local）"
    )


def scope_dir(scope: str, project_name: str | None = None) -> Path:
    """按作用域解析技能目录。

    Args:
        scope: ``builtin`` / ``global`` / ``project_set`` / ``local``。
        project_name: 项目集合名（``project_set``）或项目名（``local``）。

    Returns:
        技能目录的绝对路径（可能尚不存在）。

    Raises:
        SkillValidationError: 作用域非法，或所需的项目/集合名缺失。
    """
    normalized = normalize_scope(scope)
    if normalized == BUILTIN_SCOPE:
        return builtin_skills_dir()
    if normalized == GLOBAL_SCOPE:
        return global_skills_dir()
    if normalized == PROJECT_SET_SCOPE:
        if not project_name or not project_name.strip():
            raise SkillValidationError("项目级技能必须指定项目名")
        return project_set_dir(project_name)
    if not project_name or not project_name.strip():
        raise SkillValidationError("本地技能必须指定项目名")
    return local_skills_dir(project_name)


def scope_virtual_path(scope: str) -> str:
    """返回作用域对应的智能体虚拟路径。

    ``global`` 在无项目绑定时就是 ``/skills/``；一旦会话绑定了本地文件夹，
    ``/skills`` 会被 backend 让给项目本地技能，全局技能改由
    ``/skills-global/`` 暴露，因此挂载路径需由 :func:`agent_skill_sources`
    按模式计算。

    Args:
        scope: 作用域标识。

    Returns:
        虚拟路径；``builtin`` 不挂载，返回空串。
    """
    normalized = normalize_scope(scope)
    if normalized == GLOBAL_SCOPE:
        return VIRTUAL_GLOBAL_SKILLS
    if normalized == PROJECT_SET_SCOPE:
        return VIRTUAL_PROJECT_SET_SKILLS
    if normalized == LOCAL_SCOPE:
        return VIRTUAL_LOCAL_SKILLS
    return ""


def writable_scopes(
    project_name: str | None = None, *, has_local_folder: bool | None = None
) -> list[str]:
    """返回当前可写的技能作用域列表。

    ``global`` 永远可写；``project_set`` 只要有项目名即可（它落在全局目录下，
    不要求绑定本地文件夹）；``local`` 仅在项目已绑定本地文件夹时出现。

    Args:
        project_name: 可选的当前项目名。
        has_local_folder: 当前会话是否有可用本地目录。省略时兼容独立技能页，
            仍按项目绑定查询。

    Returns:
        作用域标识列表。
    """
    scopes: list[str] = [GLOBAL_SCOPE]
    if project_name and project_name.strip():
        scopes.append(PROJECT_SET_SCOPE)
        has_local = (
            resolve_project_folder(project_name) is not None
            if has_local_folder is None
            else has_local_folder
        )
        if has_local:
            scopes.append(LOCAL_SCOPE)
    return scopes


def agent_skill_sources(
    project_name: str | None = None, *, has_local_folder: bool | None = None
) -> list[str]:
    """返回传给 ``SkillsMiddleware`` 的技能源列表。

    ``SkillsMiddleware`` 按传入顺序加载，**后写的源覆盖先写的同名技能**，
    因此越贴近具体工作的层排在越后面：全局 → 项目集合 → 本地，让仓库专属
    约定优先于个人全局偏好。

    路径取决于 backend 模式，这是本设计唯一的耦合点：

    * 未绑定本地文件夹时，``/skills`` 会被 backend 让给项目本地技能，
      因此这里只挂全局与项目集合两个源；
    * 已绑定时，三层同时挂载。

    内置层不参与挂载，只在启动期作为模板拷贝源。

    Args:
        project_name: 当前项目名；为空时退化为只挂全局源。
        has_local_folder: 当前会话是否有独立本地目录。省略时兼容旧调用方，
            仍按项目绑定查询。

    Returns:
        虚拟路径列表，按加载顺序排列。
    """
    if not project_name or not project_name.strip():
        # 拿不到项目名时不确定 backend 是否处于本地文件夹模式，此时
        # ``/skills`` 的指向是不确定的，挂全局源才是安全的。
        return [VIRTUAL_GLOBAL_SKILLS]
    project_slug = sanitize_dir_name(project_name)
    if not project_slug:
        return [VIRTUAL_GLOBAL_SKILLS]
    project_source = f"{VIRTUAL_PROJECT_SET_SKILLS}{project_slug}/"
    has_local = (
        resolve_project_folder(project_name) is not None
        if has_local_folder is None
        else has_local_folder
    )
    if has_local:
        return [VIRTUAL_GLOBAL_SKILLS, project_source, VIRTUAL_LOCAL_SKILLS]
    return [VIRTUAL_GLOBAL_SKILLS, project_source]


def sandbox_skill_sources(project_name: str | None = None) -> list[str]:
    """返回沙箱会话传给 ``SkillsMiddleware`` 的技能源列表。

    沙箱 backend 不做虚拟路径映射，路径即容器内真实路径，因此这里返回的是
    :data:`SANDBOX_SKILL_ROOT` 下的绝对路径。

    可用层级比本地环境少一层：沙箱容器里没有项目本地文件夹，``local`` 层不
    存在。但只要会话属于某个项目集合，就与全局层一起挂载项目集合层——项目
    专属技能不再因为「跑在沙箱里」而失效。

    Args:
        project_name: 当前会话所属项目集合名；为空时只挂全局层。

    Returns:
        容器内技能源路径列表，按「全局 → 项目集合」的覆盖顺序排列。
    """
    sources = [f"{SANDBOX_SKILL_ROOT}/"]
    if project_name and project_name.strip():
        sources.append(f"{sandbox_project_set_root(project_name)}/")
    return sources


def sandbox_project_set_root(project_name: str) -> str:
    """返回沙箱内某个项目集合的技能根目录。

    Args:
        project_name: 项目集合名。

    Returns:
        形如 ``/workspace/.project-set/Xihe`` 的容器内路径。

    Raises:
        SkillValidationError: 集合名为空时。
    """
    slug = sanitize_dir_name(project_name)
    if not slug:
        raise SkillValidationError("项目集合名不能为空")
    # 项目集合层放在 /workspace/.project-set/ 而非 /workspace/skills/.project-set/，
    # 避免被 SkillsMiddleware 的 ls() 扫描到（它不过滤以点开头的目录）。
    return f"/workspace/{PROJECT_SET_DIR_NAME}/{slug}"


# ── 列举 ────────────────────────────────────────────────────────────────────


def _skill_updated_at(skill_file: Path) -> str:
    """读取 ``SKILL.md`` 的修改时间。

    Args:
        skill_file: 技能文件路径。

    Returns:
        UTC ISO 8601 时间字符串；读取失败时返回空串。
    """
    try:
        stamp = datetime.fromtimestamp(skill_file.stat().st_mtime, tz=timezone.utc)
    except OSError:
        return ""
    return stamp.isoformat()


def _list_extra_files(skill_dir: Path) -> list[str]:
    """列出技能目录下除 ``SKILL.md`` 与内部标记以外的附属文件。

    来源标记 ``.origin`` 属于本模块的内部簿记，不应出现在前端展示中。

    Args:
        skill_dir: 技能目录。

    Returns:
        相对路径列表，最多 50 项；符号链接与越界路径会被跳过。
    """
    files: list[str] = []
    try:
        for child in sorted(skill_dir.rglob("*")):
            if len(files) >= 50:
                break
            if not child.is_file() or child.is_symlink():
                continue
            relative = child.relative_to(skill_dir).as_posix()
            if relative in {SKILL_FILE_NAME, ORIGIN_MARKER_NAME}:
                continue
            files.append(relative)
    except OSError:
        return files
    return files


def read_skill(directory: Path, *, scope: str, project_name: str | None = None) -> SkillRecord | None:
    """读取单个技能目录的元数据。

    Args:
        directory: 技能目录。
        scope: 所属作用域。
        project_name: ``scope`` 为 ``project`` 时的项目名。

    Returns:
        :class:`SkillRecord`；目录无效或缺少 ``SKILL.md`` 时返回 ``None``。
    """
    skill_file = directory / SKILL_FILE_NAME
    if not skill_file.is_file():
        return None
    try:
        text = skill_file.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    frontmatter = parse_frontmatter(text)
    name = str(frontmatter.get("name") or "").strip() or directory.name
    description = str(frontmatter.get("description") or "").strip()
    if not description:
        heading = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
        description = heading.group(1).strip() if heading else "可由智能体按需加载的本地技能。"

    marker = read_origin_marker(directory)
    origin = marker or ("builtin" if scope == BUILTIN_SCOPE else "user")

    virtual_root = scope_virtual_path(scope)
    if scope == PROJECT_SET_SCOPE and project_name:
        project_slug = sanitize_dir_name(project_name)
        if project_slug:
            virtual_root = f"{VIRTUAL_PROJECT_SET_SKILLS}{project_slug}/"

    return SkillRecord(
        id=directory.name,
        name=name,
        description=description,
        scope=scope,  # type: ignore[arg-type]
        project_name=project_name,
        directory=directory,
        virtual_path=f"{virtual_root}{directory.name}/SKILL.md",
        origin=origin,
        updated_at=_skill_updated_at(skill_file),
        extra_files=_list_extra_files(directory),
        source_path=str(skill_file),
    )


def list_skills(
    scope: str = GLOBAL_SCOPE,
    project_name: str | None = None,
    *,
    base_dir: Path | None = None,
) -> list[SkillRecord]:
    """列举某个作用域下的全部技能。

    Args:
        scope: 作用域标识，``project`` 会被折叠为 ``local``。
        project_name: ``project_set`` 的集合名或 ``local`` 的项目名。
        base_dir: 显式技能根目录，用于按当前会话目录读取本地技能。

    Returns:
        按目录名排序的技能记录列表；目录不存在时返回空列表。
    """
    try:
        normalized = normalize_scope(scope)
        base = base_dir if base_dir is not None else scope_dir(normalized, project_name)
    except SkillValidationError:
        return []
    if not base.is_dir():
        return []

    records: list[SkillRecord] = []
    for child in sorted(base.iterdir(), key=lambda item: item.name.lower()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        record = read_skill(child, scope=normalized, project_name=project_name)
        if record is not None:
            records.append(record)
    return records


def list_project_sets() -> list[str]:
    """列出已存在的项目集合名。

    Returns:
        集合目录名列表（已排序）；容器目录不存在时返回空列表。
    """
    root = project_set_root()
    if not root.is_dir():
        return []
    try:
        return sorted(
            (
                child.name
                for child in root.iterdir()
                if child.is_dir() and not child.name.startswith(".")
            ),
            key=str.lower,
        )
    except OSError:
        return []


# ── 来源标记 ────────────────────────────────────────────────────────────────


def read_origin_marker(skill_dir: Path) -> str | None:
    """读取技能目录的来源标记。

    标记文件 ``.origin`` 内容为 ``user`` 或 ``agent``，用于区分人类创建与
    智能体自动创建的技能。

    Args:
        skill_dir: 技能目录。

    Returns:
        标记值；文件不存在或内容非法时返回 ``None``。
    """
    marker = skill_dir / ORIGIN_MARKER_NAME
    if not marker.is_file():
        return None
    try:
        value = marker.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return value if value in {"user", "agent", "builtin"} else None


def write_origin_marker(skill_dir: Path, origin: str) -> None:
    """写入技能目录的来源标记。

    Args:
        skill_dir: 技能目录。
        origin: ``user`` 或 ``agent``。
    """
    try:
        (skill_dir / ORIGIN_MARKER_NAME).write_text(origin, encoding="utf-8")
    except OSError:
        return


# ── 落盘 ────────────────────────────────────────────────────────────────────


def ensure_unique_skill_name(base_dir: Path, desired: str) -> str:
    """在目标目录下生成不冲突的技能标识。

    Args:
        base_dir: 技能根目录。
        desired: 期望的技能标识。

    Returns:
        可用的技能标识，冲突时追加 ``-2``、``-3`` 等后缀。
    """
    if not (base_dir / desired).exists():
        return desired
    index = 2
    while (base_dir / f"{desired}-{index}").exists():
        index += 1
    return f"{desired}-{index}"


def create_skill(
    *,
    name: str,
    description: str,
    body: str,
    scope: str = GLOBAL_SCOPE,
    project_name: str | None = None,
    origin: str = "user",
    extra_files: dict[str, bytes] | None = None,
    overwrite: bool = False,
    directory_name: str | None = None,
    base_dir: Path | None = None,
) -> SkillRecord:
    """创建（或覆盖）一个技能。

    Args:
        name: 技能标识，中文等非法字符会被规范化。
        description: 技能说明。
        body: 技能正文（不含 frontmatter）。
        scope: 目标作用域，不允许为 ``builtin``。
        project_name: ``project_set`` 的集合名，或 ``local`` 的项目名。
        origin: 来源标记，``user`` 或 ``agent``。
        extra_files: 随技能一并写入的附属文件，键为相对路径。
        overwrite: 同名技能已存在时是否覆盖。
        directory_name: 指定目录名，用于导入既有技能时保持目录与名称一致。
        base_dir: 直接指定技能目录路径，绕过 scope_dir 解析。

    Returns:
        写入后的技能记录。

    Raises:
        SkillValidationError: 作用域非法、名为空、内容不合规或已存在且不覆盖。
    """
    normalized = normalize_scope(scope)
    if normalized == BUILTIN_SCOPE:
        raise SkillValidationError("内置技能目录不可写入")

    resolved_dir = base_dir if base_dir is not None else scope_dir(normalized, project_name)
    slug = (directory_name or coerce_ascii_name(name)).strip()
    if not slug:
        raise SkillValidationError("技能名不能为空")

    safe_body = body.strip() or f"# {name}\n\n{description.strip()}\n"
    document = build_skill_document(slug, description, safe_body)
    validate_skill_content(document, directory_name=slug)

    target = resolved_dir / slug
    if target.exists():
        if not overwrite:
            raise SkillValidationError(f"技能「{slug}」已存在，请改用其他名称")
        _clear_skill_dir(target, resolved_dir)

    resolved_dir.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    (target / SKILL_FILE_NAME).write_text(document, encoding="utf-8")
    write_origin_marker(target, origin)

    for relative, content in (extra_files or {}).items():
        destination = _safe_join(target, relative)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)

    record = read_skill(
        target, scope=normalized, project_name=_record_project_name(normalized, project_name)
    )
    if record is None:
        raise SkillValidationError("技能写入失败，请检查目录权限")
    return record


def _record_project_name(scope: str, project_name: str | None) -> str | None:
    """决定记录里要保留的项目名。

    ``global`` 作用域不携带项目归属，其余作用域保留原值。

    Args:
        scope: 已规范化的作用域。
        project_name: 调用方传入的项目名。

    Returns:
        该作用域下应记录的项目名。
    """
    return None if scope == GLOBAL_SCOPE else project_name


def update_skill(
    skill_id: str,
    *,
    scope: str = GLOBAL_SCOPE,
    project_name: str | None = None,
    name: str | None = None,
    description: str | None = None,
    body: str | None = None,
) -> SkillRecord:
    """更新一个已存在技能的内容。

    未提供的字段沿用原值；正文会与新的 frontmatter 重新组装，
    因此调用方不需要自己拼 ``---`` 块。

    Args:
        skill_id: 技能目录名。
        scope: 目标作用域。
        project_name: 项目集合名或项目名。
        name: 新的技能标识（会同时重命名目录）。
        description: 新的技能说明。
        body: 新的正文。

    Returns:
        更新后的技能记录。

    Raises:
        SkillValidationError: 目标作用域不可写、技能不存在或内容不合规。
    """
    normalized = normalize_scope(scope)
    if normalized == BUILTIN_SCOPE:
        raise SkillValidationError("内置技能目录不可写入")

    base_dir = scope_dir(normalized, project_name)
    target = base_dir / skill_id
    current = read_skill(
        target, scope=normalized, project_name=_record_project_name(normalized, project_name)
    )
    if current is None:
        raise SkillValidationError(f"技能「{skill_id}」不存在")

    original = (target / SKILL_FILE_NAME).read_text(encoding="utf-8", errors="replace")
    _, original_body = split_frontmatter(original)
    next_name = (name or current.id).strip()
    next_description = (description or current.description).strip()
    next_body = body if body is not None else original_body.strip()

    slug = coerce_ascii_name(next_name)
    document = build_skill_document(slug, next_description, next_body)
    validate_skill_content(document)

    final_dir = target
    if slug != skill_id:
        renamed = base_dir / slug
        if renamed.exists():
            raise SkillValidationError(f"技能「{slug}」已存在，无法重命名")
        target.rename(renamed)
        final_dir = renamed

    (final_dir / SKILL_FILE_NAME).write_text(document, encoding="utf-8")

    record = read_skill(
        final_dir,
        scope=normalized,
        project_name=_record_project_name(normalized, project_name),
    )
    if record is None:
        raise SkillValidationError("技能更新失败，请检查目录权限")
    return record


def delete_skill(
    skill_id: str, *, scope: str = GLOBAL_SCOPE, project_name: str | None = None
) -> bool:
    """删除一个技能目录。

    Args:
        skill_id: 技能目录名。
        scope: 目标作用域。
        project_name: 项目集合名或项目名。

    Returns:
        确实删除了目录返回 ``True``，技能不存在返回 ``False``。

    Raises:
        SkillValidationError: 作用域不可写或路径越界。
    """
    normalized = normalize_scope(scope)
    if normalized == BUILTIN_SCOPE:
        raise SkillValidationError("内置技能目录不可删除")

    base_dir = scope_dir(normalized, project_name)
    target = base_dir / skill_id
    if not target.is_dir():
        return False
    if target.parent.resolve() != base_dir.resolve():
        raise SkillValidationError("技能路径越界，删除已拒绝")
    shutil.rmtree(target)
    return True


def _clear_skill_dir(target: Path, base_dir: Path) -> None:
    """清空一个已存在的技能目录内容。

    Args:
        target: 技能目录。
        base_dir: 技能根目录，用于越界校验。
    """
    if target.parent.resolve() != base_dir.resolve():
        raise SkillValidationError("技能路径越界，覆盖已拒绝")
    shutil.rmtree(target)


def _safe_join(base: Path, relative: str) -> Path:
    """把相对路径安全地拼到基准目录下，阻止路径穿越。

    Args:
        base: 基准目录。
        relative: 待拼接的相对路径。

    Returns:
        拼接后的绝对路径。

    Raises:
        SkillValidationError: 相对路径为绝对路径或逃逸出基准目录时。
    """
    candidate = Path(relative.replace("\\", "/"))
    if candidate.is_absolute() or candidate.drive:
        raise SkillValidationError(f"附件路径非法：{relative}")
    combined = (base / candidate).resolve()
    if combined != base.resolve() and base.resolve() not in combined.parents:
        raise SkillValidationError(f"附件路径越界：{relative}")
    return combined


# ── 安装默认技能 ────────────────────────────────────────────────────────────


def install_builtin_skills(origin: str = "builtin") -> list[str]:
    """把内置技能拷贝到全局作用域，跳过已存在的同名技能。

    与旧实现保持一致：只补缺，不覆盖用户改动，因此升级产品不会冲掉
    用户对内置技能的本地修改。

    Args:
        origin: 写入内置技能的来源标记。

    Returns:
        本次新拷贝的技能目录名列表。
    """
    source = builtin_skills_dir()
    if not source.is_dir():
        return []

    target_root = global_skills_dir()
    target_root.mkdir(parents=True, exist_ok=True)

    installed: list[str] = []
    for skill_folder in sorted(source.iterdir()):
        if not skill_folder.is_dir() or skill_folder.name.startswith("."):
            continue
        destination = target_root / skill_folder.name
        if destination.exists():
            continue
        shutil.copytree(skill_folder, destination)
        write_origin_marker(destination, origin)
        installed.append(skill_folder.name)
    return installed


def load_skill_documents(
    scope: str = GLOBAL_SCOPE,
    project_name: str | None = None,
    *,
    root: str = SANDBOX_SKILL_ROOT,
) -> list[tuple[str, bytes]]:
    """读取某个作用域的技能文件内容，供沙盒上传使用。

    Args:
        scope: 目标作用域。
        project_name: 项目集合名或项目名。
        root: 该作用域在沙箱内的技能根目录，默认全局层根目录
            :data:`SANDBOX_SKILL_ROOT`。

    Returns:
        ``(沙盒内目标路径, 字节内容)`` 列表，路径形如
        ``<root>/<skill>/SKILL.md``，并包含附属文件。
    """
    prefix = root.rstrip("/")
    uploads: list[tuple[str, bytes]] = []
    for record in list_skills(scope, project_name):
        try:
            content = (record.directory / SKILL_FILE_NAME).read_bytes()
        except OSError:
            continue
        uploads.append((f"{prefix}/{record.id}/{SKILL_FILE_NAME}", content))
        for relative in record.extra_files:
            try:
                payload = (record.directory / relative).read_bytes()
            except OSError:
                continue
            uploads.append((f"{prefix}/{record.id}/{relative}", payload))
    return uploads


def load_sandbox_skill_documents(project_name: str | None = None) -> list[tuple[str, bytes]]:
    """组装要同步进沙箱的技能文件清单。

    顺序为「全局层 → 项目集合层」，与 :func:`sandbox_skill_sources` 的挂载顺序
    一致。``SkillsMiddleware`` 以「后写的源覆盖先写的源」处理同名技能，因此这里
    让项目集合层排在后面，同名时覆盖全局层。

    会话不属于任何项目集合时只返回全局层，与 :func:`sandbox_skill_sources` 的
    口径完全对齐——上传什么就挂载什么，不会出现「传了没挂」或「挂了没传」的错位。

    Args:
        project_name: 当前会话所属项目集合名；为空时只同步全局层。

    Returns:
        ``(沙盒内目标路径, 字节内容)`` 列表。
    """
    uploads = load_skill_documents(GLOBAL_SCOPE)
    if project_name and project_name.strip():
        uploads += load_skill_documents(
            PROJECT_SET_SCOPE,
            project_name,
            root=sandbox_project_set_root(project_name),
        )
    return uploads


def sandbox_skill_roots(project_name: str | None = None) -> list[str]:
    """返回沙箱内需要维护的技能根目录列表。

    同步时先清空这些目录再写入，保证主机上删掉的技能不会残留在沙箱里。即使
    当前一个技能都没有，也要把它们建出来，否则 ``SkillsMiddleware`` 会对着
    不存在的目录刷 ``path_not_found`` 警告。

    Args:
        project_name: 当前会话所属项目集合名；为空时只返回全局层根目录。

    Returns:
        沙箱内目录路径列表。
    """
    roots = [SANDBOX_SKILL_ROOT]
    if project_name and project_name.strip():
        roots.append(sandbox_project_set_root(project_name))
    return roots
