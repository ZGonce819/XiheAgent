"""Skills 安装服务模块。

提供默认 Skills 的自动安装功能，在应用启动时将项目自带的 skills
拷贝到用户工作区目录。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from config.app_config import get_settings
from config.constants import PROJECT_ROOT


def ensure_default_skills() -> list[str]:
    """确保工作区有默认的 skills。

    检查项目自带的默认 skills 目录（``PROJECT_ROOT/agent/skills``），
    将所有不存在的 skills 拷贝到工作区的 skills 目录
    （``workspace_root/skills``）。

    Returns:
        已安装的 skill 名称列表（本次新拷贝的）。

    Note:
        - 只拷贝工作区中不存在的 skills，避免覆盖用户修改。
        - 如果项目没有默认 skills 源目录，返回空列表。
        - 自动创建目标目录（如果不存在）。
    """
    settings = get_settings()
    skills_dir = settings.skills_dir

    # 项目自带的默认 skills 目录
    default_skills_source = PROJECT_ROOT / "agent" / "skills"

    if not default_skills_source.exists():
        return []  # 没有默认 skills 源，跳过

    # 创建工作区 skills 目录
    skills_dir.mkdir(parents=True, exist_ok=True)

    installed_skills: list[str] = []

    # 拷贝所有默认 skills
    for skill_folder in sorted(default_skills_source.iterdir()):
        if not skill_folder.is_dir():
            continue

        dest = skills_dir / skill_folder.name

        # 只拷贝不存在的，避免覆盖用户修改
        if not dest.exists():
            shutil.copytree(skill_folder, dest)
            installed_skills.append(skill_folder.name)

    return installed_skills


def get_default_skills() -> list[tuple[str, bytes]]:
    """获取默认 skills 内容列表（供沙盒上传使用）。

    从工作区 skills 目录读取所有已安装的 skills，返回可用于上传的文件列表。

    Returns:
        ``(沙盒内目标路径, 字节内容)`` 元组列表。每个 skill 的 SKILL.md
        会被放置在 ``/workspace/skills/<skill_name>/SKILL.md``。

    Note:
        - 只读取工作区中已存在的 skills（由 ensure_default_skills 安装）。
        - 如果工作区没有 skills，返回空列表。
        - 自动跳过不包含 SKILL.md 的目录。
    """
    settings = get_settings()
    skills_dir = settings.skills_dir

    if not skills_dir.exists():
        return []

    upload_files: list[tuple[str, bytes]] = []

    for skill_folder in sorted(skills_dir.iterdir()):
        if not skill_folder.is_dir():
            continue

        skill_md = skill_folder / "SKILL.md"
        if not skill_md.exists():
            continue

        # 读取 SKILL.md 内容
        content = skill_md.read_text(encoding="utf-8")
        target_path = f"/workspace/skills/{skill_folder.name}/SKILL.md"
        upload_files.append((target_path, content.encode("utf-8")))

    return upload_files
