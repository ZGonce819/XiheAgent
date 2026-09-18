"""Skills 安装服务模块。

早期版本在这里实现了「启动时把项目自带 skills 拷贝到工作区」以及
「读取技能内容供沙盒上传」两项能力。迁移到 :mod:`services.skill_registry`
之后，本模块只保留下兼容用的薄封装，真正的解析 / 校验 / 落盘 / 列举逻辑
统一收敛在 registry 中，避免两处实现分叉。

Example:
    >>> from services.skill_installer import ensure_default_skills
    >>> ensure_default_skills()
    ['code-review']
"""

from __future__ import annotations

from services.skill_registry import (
    GLOBAL_SCOPE,
    install_builtin_skills,
    load_skill_documents,
)


def ensure_default_skills() -> list[str]:
    """确保工作区有默认的 skills。

    检查项目自带的默认 skills 目录（``PROJECT_ROOT/agent/skills``），
    将所有不存在的 skills 拷贝到全局技能目录
    （``Settings.skills_dir``）。

    Returns:
        已安装的 skill 名称列表（本次新拷贝的）。

    Note:
        - 只拷贝工作区中不存在的 skills，避免覆盖用户修改。
        - 如果项目没有默认 skills 源目录，返回空列表。
        - 自动创建目标目录（如果不存在）。
    """
    return install_builtin_skills()


def get_default_skills() -> list[tuple[str, bytes]]:
    """获取全局作用域 skills 内容列表（供沙盒上传使用）。

    从全局技能目录读取所有已安装的 skills，返回可用于上传的文件列表。

    Returns:
        ``(沙盒内目标路径, 字节内容)`` 元组列表。每个 skill 的 SKILL.md
        会被放置在 ``/workspace/skills/<skill_name>/SKILL.md``，附属文件
        一并上传。

    Note:
        - 只读取全局作用域中已存在且带 SKILL.md 的 skills。
        - 如果工作区没有 skills，返回空列表。
    """
    return load_skill_documents(GLOBAL_SCOPE)
