"""工作目录与项目管理接口。"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from agent.graph.graph import get_store
from config.app_config import get_settings
from schemas.request_schema import (
    DashboardProjectBindFolderRequest,
    DashboardProjectRenameRequest,
)

router = APIRouter(prefix="/workspace")
logger = logging.getLogger(__name__)


class _WorkspaceUpdateRequest(BaseModel):
    workspace_root: str | None = None
    projects_dir: str | None = None
    skills_dir: str | None = None


def _ensure_writable(path: Path, label: str) -> None:
    """确保目录存在且可写，不存在时自动创建。"""
    if not path.exists():
        try:
            path.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            raise HTTPException(status_code=400, detail=f"无法创建{label}：{e}")
    try:
        test = path / ".xihe_write_test"
        test.touch()
        test.unlink()
    except OSError:
        raise HTTPException(status_code=400, detail=f"{label}不可写：{path}")


def _migrate_workspace(
    old_workspace: Path,
    new_workspace: Path,
    progress_callback: callable | None = None,
) -> dict[str, Any]:
    """迁移工作区项目目录到新位置。"""
    import shutil

    old_projects = old_workspace / "projects"
    new_projects = new_workspace / "projects"

    result = {"migrated": 0, "errors": []}

    if not old_projects.exists():
        return result

    if not new_projects.exists():
        try:
            new_projects.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            result["errors"].append(f"无法创建新项目目录：{e}")
            return result

    projects = [p for p in old_projects.iterdir() if p.is_dir()]
    total = len(projects)

    if total == 0:
        return result

    for index, project in enumerate(projects, start=1):
        try:
            target = new_projects / project.name
            if target.exists():
                result["errors"].append(f"项目已存在：{project.name}")
                continue

            if progress_callback:
                progress_callback(index, total, f"迁移项目：{project.name}")

            shutil.move(str(project), str(target))
            result["migrated"] += 1
        except Exception as e:
            result["errors"].append(f"迁移失败 {project.name}：{e}")

    return result


@router.get("")
def dashboard_get_workspace() -> dict[str, str]:
    """返回当前工作目录配置。"""
    settings = get_settings()
    return {
        "workspace_root": str(settings.workspace_root),
        "projects_dir": str(settings.projects_dir),
        "skills_dir": str(settings.skills_dir),
    }


@router.put("")
def dashboard_update_workspace(body: _WorkspaceUpdateRequest) -> dict[str, Any]:
    """更新工作目录配置。"""
    from config.workspace_config import save_workspace_config

    settings = get_settings()
    migration_result = None

    if body.workspace_root is not None:
        new_path = Path(body.workspace_root).resolve()
        _ensure_writable(new_path, "工作目录")

        old_workspace = settings.workspace_root
        if old_workspace != new_path:
            migration_result = _migrate_workspace(old_workspace, new_path)

        settings.workspace_root = new_path
        save_workspace_config(workspace_root=str(new_path))

    if body.projects_dir is not None:
        new_projects = Path(body.projects_dir).resolve()
        _ensure_writable(new_projects, "项目目录")
        settings.projects_dir = new_projects
        save_workspace_config(projects_dir=str(new_projects))

    if body.skills_dir is not None:
        new_skills = Path(body.skills_dir).resolve()
        _ensure_writable(new_skills, "技能目录")
        settings.skills_dir = new_skills
        save_workspace_config(skills_dir=str(new_skills))

    response = {
        "workspace_root": str(settings.workspace_root),
        "projects_dir": str(settings.projects_dir),
        "skills_dir": str(settings.skills_dir),
    }

    if migration_result is not None:
        response["migration"] = migration_result

    return response


@router.get("/pick-folder")
def dashboard_pick_folder() -> dict[str, Any]:
    """打开系统原生文件夹选择对话框。"""
    result: dict[str, str | None] = {"path": None}
    error: Exception | None = None
    done = threading.Event()

    def _pick() -> None:
        nonlocal error
        try:
            import tkinter as tk
            from tkinter import filedialog

            root = tk.Tk()
            root.withdraw()
            root.attributes("-topmost", True)
            path = filedialog.askdirectory(title="选择工作目录", parent=root)
            result["path"] = path if path else None
            root.destroy()
        except Exception as e:
            error = e
        finally:
            done.set()

    thread = threading.Thread(target=_pick, daemon=True)
    thread.start()
    done.wait(timeout=120)

    if error is not None:
        raise HTTPException(status_code=500, detail=f"文件夹对话框失败：{error}")
    if not done.is_set():
        raise HTTPException(status_code=504, detail="文件夹选择超时")

    return result


@router.get("/projects")
def dashboard_list_projects() -> dict[str, Any]:
    """分别列出用户创建的项目与工作目录中发现的文件夹。"""
    settings = get_settings()
    projects_dir = settings.projects_dir

    bindings: dict[str, str | None] = {}
    try:
        for record in get_store().list_project_folders():
            name = record.get("project_name")
            folder = record.get("local_folder")
            if isinstance(name, str):
                folder_str = folder if isinstance(folder, str) else ""
                bindings[name] = folder_str if folder_str.strip() else None
    except Exception:
        logger.warning("读取项目本地文件夹绑定失败，返回未合并的目录列表", exc_info=True)

    created_projects = [
        {
            "name": name,
            "path": folder or "",
            "local_folder": folder,
            "source": "created",
        }
        for name, folder in bindings.items()
    ]

    workspace_projects: list[dict[str, Any]] = []
    if projects_dir.exists():
        for child in sorted(projects_dir.iterdir(), key=lambda p: p.name.lower()):
            if child.is_dir() and not child.name.startswith("."):
                workspace_projects.append({
                    "name": child.name,
                    "path": str(child),
                    "local_folder": str(child),
                    "source": "workspace",
                })

    return {
        "projects": created_projects + workspace_projects,
        "created_projects": created_projects,
        "workspace_projects": workspace_projects,
        "projects_dir": str(projects_dir),
    }


@router.patch("/projects/rename")
def dashboard_rename_project(body: DashboardProjectRenameRequest) -> dict[str, Any]:
    """重命名项目：把该项目下所有会话的 project_name 更新为新名称。"""
    old_name = body.old_name.strip()
    new_name = body.new_name.strip()
    if not old_name or not new_name:
        raise HTTPException(status_code=400, detail="项目名称不能为空")
    if old_name == new_name:
        return {"renamed": 0, "name": new_name}
    affected = get_store().rename_project(old_name, new_name)
    return {"renamed": affected, "name": new_name}


@router.post("/projects/bind-folder")
def dashboard_bind_project_folder(body: DashboardProjectBindFolderRequest) -> dict[str, Any]:
    """创建一个只有名称的项目。"""
    project_name = body.project_name.strip()
    if not project_name:
        raise HTTPException(status_code=400, detail="项目名称不能为空")
    raw_folder = (body.local_folder or "").strip()
    folder = Path(raw_folder).resolve() if raw_folder else None
    if folder is not None and (not folder.exists() or not folder.is_dir()):
        raise HTTPException(status_code=400, detail=f"本地文件夹不存在或不是目录：{folder}")
    get_store().upsert_project_folder(project_name, str(folder) if folder else "")
    return {"project_name": project_name}
