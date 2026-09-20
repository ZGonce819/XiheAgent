"""技能管理相关接口。"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from config.app_config import get_settings
from schemas.request_schema import (
    DashboardSkillCreateRequest,
    DashboardSkillUpdateRequest,
)

router = APIRouter(prefix="/skills")
logger = logging.getLogger(__name__)

SCOPE_LABELS_CN = {
    "global": "全局",
    "project_set": "项目",
    "local": "本地",
}


def _skill_scope_context(
    scope: str, project_name: str | None, local_folder: str | None = None
) -> tuple[str, str | None]:
    """规范化技能作用域参数并做可用性校验。"""
    from services.skill_registry import (
        GLOBAL_SCOPE,
        LOCAL_SCOPE,
        SkillValidationError,
        normalize_scope,
        resolve_project_folder,
    )

    try:
        normalized = normalize_scope(scope)
    except SkillValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if normalized == GLOBAL_SCOPE:
        return normalized, None

    name = (project_name or "").strip()
    if normalized == LOCAL_SCOPE:
        direct_folder = (local_folder or "").strip()
        if direct_folder:
            return normalized, name or None
        if name:
            if resolve_project_folder(name) is None:
                raise HTTPException(
                    status_code=400,
                    detail=f"项目「{name}」尚未绑定本地文件夹，无法使用本地技能",
                )
            return normalized, name
        raise HTTPException(
            status_code=400,
            detail="本地技能需要指定项目名或直接提供文件夹路径",
        )

    if not name:
        raise HTTPException(
            status_code=400,
            detail=f"{SCOPE_LABELS_CN.get(normalized, normalized)}技能必须指定项目名",
        )
    return normalized, name


def _refresh_sandbox_skills(scope: str, project_name: str | None) -> None:
    """技能落盘后把最新技能集同步进活跃沙箱。"""
    try:
        from services.sandbox_skill_sync import refresh_sandbox_skills

        refresh_sandbox_skills(scope=scope, project_name=project_name)
    except Exception:
        logger.warning(
            "技能已保存，但同步到沙箱失败：scope=%s project=%s",
            scope,
            project_name,
            exc_info=True,
        )


def _slug_or_fallback(raw_name: str) -> str:
    """把技能名规范化为合法的目录名，失败时给出编号回退。"""
    from services.skill_registry import coerce_ascii_name

    return coerce_ascii_name(raw_name)


@router.get("")
def dashboard_skills(
    project_name: str | None = None,
    backend_type: str | None = None,
    local_folder: str | None = None,
    session_scoped: bool = False,
) -> dict[str, Any]:
    """列出可见的 DeepAgents 技能，并标注作用域与来源。"""
    from services.skill_registry import (
        GLOBAL_SCOPE,
        LOCAL_SCOPE,
        PROJECT_SET_SCOPE,
        list_skills,
        project_set_dir,
        resolve_project_folder,
        writable_scopes,
    )

    sandbox_session = (backend_type or "").strip().lower() == "sandbox"

    skill_items: list[dict[str, Any]] = []
    for record in list_skills(GLOBAL_SCOPE):
        skill_items.append(record.to_payload())

    has_project = bool(project_name and project_name.strip())
    direct_local_folder = (local_folder or "").strip()
    project_dir = (
        Path(direct_local_folder).expanduser().resolve()
        if direct_local_folder
        else (
            None
            if session_scoped
            else (resolve_project_folder(project_name) if has_project else None)
        )
    )
    if project_dir is not None and not project_dir.is_dir():
        project_dir = None
    local_available = project_dir is not None and not sandbox_session
    if has_project:
        for record in list_skills(PROJECT_SET_SCOPE, project_name):
            skill_items.append(record.to_payload())
        if local_available:
            for record in list_skills(
                LOCAL_SCOPE,
                project_name,
                base_dir=project_dir / ".xihe-agent" / "skills",
            ):
                skill_items.append(record.to_payload())
    else:
        from services.skill_registry import list_project_sets

        for set_name in list_project_sets():
            for record in list_skills(PROJECT_SET_SCOPE, set_name):
                skill_items.append(record.to_payload())
        try:
            from agent.graph.graph import get_store

            for binding in get_store().list_project_folders():
                bound_project = binding.get("project_name")
                if bound_project:
                    for record in list_skills(LOCAL_SCOPE, bound_project):
                        skill_items.append(record.to_payload())
        except Exception:
            logger.debug("列出本地作用域技能失败", exc_info=True)

    scopes = [
        {
            "scope": GLOBAL_SCOPE,
            "label": "全局",
            "description": "跨项目共享，所有会话都可加载。",
            "projectName": None,
            "directory": str(get_settings().skills_dir),
            "available": True,
        }
    ]
    try:
        set_directory = str(project_set_dir(project_name)) if has_project else None
    except Exception:
        set_directory = None
    any_local_bound = False
    if not has_project:
        try:
            from agent.graph.graph import get_store

            any_local_bound = len(get_store().list_project_folders()) > 0
        except Exception:
            logger.debug("查询项目绑定列表失败", exc_info=True)
    scopes.append(
        {
            "scope": PROJECT_SET_SCOPE,
            "label": "项目",
            "description": (
                "仅该项目可见，同名时覆盖全局技能。"
                if has_project
                else "汇总所有项目集合的技能，选择项目可聚焦查看。"
            ),
            "projectName": project_name if has_project else None,
            "directory": set_directory,
            "available": True,
        }
    )
    scopes.append(
        {
            "scope": LOCAL_SCOPE,
            "label": "本地",
            "description": (
                "沙箱会话没有项目本地文件夹，该层不参与加载。"
                if sandbox_session
                else (
                    "仅该项目可见，存放在项目文件夹内，同名时覆盖项目技能。"
                    if has_project
                    else "汇总所有已绑定文件夹项目的本地技能。"
                )
            ),
            "projectName": project_name if has_project else None,
            "directory": (
                str(project_dir / ".xihe-agent" / "skills") if project_dir else None
            ),
            "available": (local_available or any_local_bound) and not sandbox_session,
        }
    )

    available_scopes = [
        scope
        for scope in writable_scopes(
            project_name,
            has_local_folder=(
                local_available if direct_local_folder or session_scoped else None
            ),
        )
        if not (sandbox_session and scope == LOCAL_SCOPE)
    ]

    return {
        "items": skill_items,
        "scopes": scopes,
        "writableScopes": available_scopes,
        "virtualRoot": "/skills",
        "available": any(item["available"] for item in scopes),
    }


@router.get("/{skill_id}")
def dashboard_skill_detail(
    skill_id: str, scope: str = "global", project_name: str | None = None
) -> dict[str, Any]:
    """读取单个技能的完整内容。"""
    from services.skill_registry import SKILL_FILE_NAME, scope_dir, read_skill

    normalized, name = _skill_scope_context(scope, project_name)
    directory = scope_dir(normalized, name) / skill_id
    record = read_skill(directory, scope=normalized, project_name=name)
    if record is None:
        raise HTTPException(status_code=404, detail=f"技能「{skill_id}」不存在")
    try:
        content = (directory / SKILL_FILE_NAME).read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"读取技能失败：{e}")
    payload = record.to_payload()
    payload["content"] = content
    return payload


@router.post("", status_code=201)
def dashboard_create_skill(body: DashboardSkillCreateRequest) -> dict[str, Any]:
    """创建技能。"""
    from services.skill_registry import (
        SkillValidationError,
        create_skill,
        ensure_unique_skill_name,
        parse_frontmatter,
        scope_dir,
    )

    scope, project_name = _skill_scope_context(
        body.scope, body.project_name, body.local_folder
    )

    content = (body.content or "").strip()
    if content:
        frontmatter = parse_frontmatter(content)
        raw_name = str(frontmatter.get("name") or body.name or "").strip()
        raw_description = str(frontmatter.get("description") or body.description or "").strip()
        markup = content
    else:
        raw_name = (body.name or "").strip()
        raw_description = (body.description or "").strip()
        markup = None

    if not markup and not raw_name:
        raise HTTPException(status_code=400, detail="技能名不能为空")
    if not markup and not raw_description:
        raise HTTPException(status_code=400, detail="技能说明不能为空")

    try:
        direct_base: Path | None = None
        if scope == "local" and (body.local_folder or "").strip():
            direct_base = Path(body.local_folder.strip()) / ".xihe-agent" / "skills"
            base_dir = direct_base
        else:
            base_dir = scope_dir(scope, project_name)
        if markup:
            from services.skill_registry import split_frontmatter

            _, markup_body = split_frontmatter(markup)
            document_name = _slug_or_fallback(raw_name)
            document_name = ensure_unique_skill_name(base_dir, document_name)
            record = create_skill(
                name=raw_name or document_name,
                description=raw_description,
                body=markup_body,
                scope=scope,
                project_name=project_name,
                origin="user",
                overwrite=False,
                directory_name=document_name,
                base_dir=direct_base,
            )
        else:
            record = create_skill(
                name=raw_name,
                description=raw_description,
                body=body.body or "",
                scope=scope,
                project_name=project_name,
                origin="user",
                overwrite=False,
                base_dir=direct_base,
            )
    except SkillValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"技能写入失败：{e}")

    logger.info(
        "已创建技能：scope=%s project=%s id=%s", scope, project_name, record.id
    )
    _refresh_sandbox_skills(scope, project_name)
    return record.to_payload()


@router.put("/{skill_id}")
def dashboard_update_skill(
    skill_id: str, body: DashboardSkillUpdateRequest
) -> dict[str, Any]:
    """更新技能内容。"""
    from services.skill_registry import SkillValidationError, split_frontmatter, update_skill

    scope, project_name = _skill_scope_context(body.scope, body.project_name)

    name = body.name
    description = body.description
    markup_body = body.body

    content = (body.content or "").strip()
    if content:
        from services.skill_registry import parse_frontmatter

        frontmatter = parse_frontmatter(content)
        _, markup_body = split_frontmatter(content)
        name = str(frontmatter.get("name") or name or "").strip() or name
        description = str(frontmatter.get("description") or description or "").strip() or description

    try:
        record = update_skill(
            skill_id,
            scope=scope,
            project_name=project_name,
            name=name,
            description=description,
            body=markup_body,
        )
    except SkillValidationError as e:
        message = str(e)
        if "不存在" in message:
            raise HTTPException(status_code=404, detail=message)
        raise HTTPException(status_code=400, detail=message)
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"技能更新失败：{e}")

    logger.info("已更新技能：scope=%s id=%s", scope, record.id)
    _refresh_sandbox_skills(scope, project_name)
    return record.to_payload()


@router.delete("/{skill_id}", status_code=204)
def dashboard_delete_skill(
    skill_id: str, scope: str = "global", project_name: str | None = None
) -> None:
    """删除技能。"""
    from services.skill_registry import SkillValidationError, delete_skill

    normalized, name = _skill_scope_context(scope, project_name)
    try:
        removed = delete_skill(skill_id, scope=normalized, project_name=name)
    except SkillValidationError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"技能删除失败：{e}")
    if not removed:
        raise HTTPException(status_code=404, detail=f"技能「{skill_id}」不存在")
    _refresh_sandbox_skills(normalized, name)


@router.post("/upload", status_code=201)
def dashboard_upload_skill(
    file: UploadFile = File(..., description="技能 ZIP 包或单个 SKILL.md"),
    scope: str = Form("global", description="作用域：global / project_set / local"),
    project_name: str | None = Form(None, description="项目集合名或项目名"),
    name: str | None = Form(None, description="技能名，仅在单文件上传且缺少 frontmatter 时使用"),
    overwrite: bool = Form(False, description="同名技能已存在时是否覆盖"),
    local_folder: str | None = Form(None, description="本地作用域的直接文件夹路径（无需项目绑定）"),
) -> dict[str, Any]:
    """通过上传 ZIP 包或单个 SKILL.md 创建技能。"""
    import io
    import zipfile

    from services.skill_registry import (
        MAX_SKILL_TOTAL_FILES,
        MAX_SKILL_TOTAL_SIZE,
        SKILL_FILE_NAME,
        SkillValidationError,
        build_skill_document,
        create_skill,
        ensure_unique_skill_name,
        parse_frontmatter,
        scope_dir,
        slugify_skill_name,
        split_frontmatter,
        validate_skill_content,
    )

    normalized, project = _skill_scope_context(scope, project_name, local_folder)
    direct_base: Path | None = None
    if normalized == "local" and (local_folder or "").strip():
        direct_base = Path(local_folder.strip()) / ".xihe-agent" / "skills"
        base_dir = direct_base
    else:
        base_dir = scope_dir(normalized, project)
    filename = (file.filename or "").strip().lower()

    raw = file.file.read(MAX_SKILL_TOTAL_SIZE + 1)
    if len(raw) > MAX_SKILL_TOTAL_SIZE:
        raise HTTPException(
            status_code=400,
            detail=f"上传内容过大（上限 {MAX_SKILL_TOTAL_SIZE // 1000} KB）",
        )
    if not raw:
        raise HTTPException(status_code=400, detail="上传文件为空")

    created: list[dict[str, Any]] = []
    failed: list[dict[str, str]] = []

    def _store(
        skill_name: str,
        directory_name: str,
        document: str,
        attachments: dict[str, bytes] | None = None,
    ) -> None:
        """校验并落盘一个技能。"""
        validate_skill_content(document)
        _, markup_body = split_frontmatter(document)
        final_name = directory_name
        if not overwrite:
            final_name = ensure_unique_skill_name(base_dir, directory_name)
        record = create_skill(
            name=skill_name,
            description=str(parse_frontmatter(document).get("description") or ""),
            body=markup_body,
            scope=normalized,
            project_name=project,
            origin="user",
            extra_files=attachments or {},
            overwrite=overwrite,
            directory_name=final_name,
            base_dir=direct_base,
        )
        created.append(record.to_payload())

    if filename.endswith(".md") or filename.endswith(".markdown"):
        document = raw.decode("utf-8", errors="replace")
        if not document.startswith("---"):
            fallback = (name or "").strip() or Path(filename).stem
            slug = slugify_skill_name(fallback)
            if not slug:
                raise HTTPException(
                    status_code=400,
                    detail="SKILL.md 缺少 frontmatter，且未能从文件名推导出合法技能名",
                )
            heading = re.search(r"^#\s+(.+)$", document, re.MULTILINE)
            description = heading.group(1).strip() if heading else f"由 {Path(filename).stem} 导入的技能。"
            document = build_skill_document(slug, description, document)
        frontmatter = parse_frontmatter(document)
        skill_name = str(frontmatter.get("name") or "").strip()
        directory_name = ensure_unique_skill_name(
            base_dir, slugify_skill_name(skill_name) or slugify_skill_name(name or "") or "skill"
        )
        try:
            _store(skill_name, directory_name, document)
        except SkillValidationError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"created": created, "failed": failed}

    if not filename.endswith(".zip"):
        raise HTTPException(
            status_code=400, detail="只支持上传 .zip 技能包或单个 .md 文件"
        )

    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise HTTPException(status_code=400, detail="ZIP 包已损坏或格式不正确")

    with archive:
        members = [item for item in archive.infolist() if not item.is_dir()]
        if len(members) > MAX_SKILL_TOTAL_FILES:
            raise HTTPException(
                status_code=400,
                detail=f"技能包文件数过多（上限 {MAX_SKILL_TOTAL_FILES} 个）",
            )

        grouped: dict[str, dict[str, bytes]] = {}
        for item in members:
            normalized_name = item.filename.replace("\\", "/")
            parts = [part for part in normalized_name.split("/") if part not in {"", "."}]
            if not parts or ".." in parts or normalized_name.startswith("/"):
                failed.append({"path": item.filename, "reason": "路径非法"})
                continue
            if re.match(r"^[A-Za-z]:", normalized_name):
                failed.append({"path": item.filename, "reason": "路径非法"})
                continue
            if item.file_size > MAX_SKILL_TOTAL_SIZE:
                failed.append({"path": item.filename, "reason": "单文件过大"})
                continue
            if parts[-1] == SKILL_FILE_NAME:
                if len(parts) < 2:
                    grouped.setdefault("", {})[SKILL_FILE_NAME] = archive.read(item)
                    continue
                grouped.setdefault("/".join(parts[:-1]), {})[SKILL_FILE_NAME] = archive.read(item)
            else:
                if len(parts) < 2:
                    continue
                grouped.setdefault("/".join(parts[:-1]), {})[parts[-1]] = archive.read(item)

        if not grouped:
            raise HTTPException(status_code=400, detail="技能包中未找到 SKILL.md")

        skill_roots = sorted(
            (key for key, files in grouped.items() if SKILL_FILE_NAME in files),
            key=len,
        )

        def _owner_root(relative_dir: str) -> str | None:
            for root in skill_roots:
                if relative_dir == root or relative_dir.startswith(f"{root}/"):
                    return root
            return None

        for root in skill_roots:
            document = grouped[root][SKILL_FILE_NAME].decode("utf-8", errors="replace")
            frontmatter = parse_frontmatter(document)
            skill_name = str(frontmatter.get("name") or "").strip()
            leaf = Path(root).name if root else ""
            candidate = slugify_skill_name(skill_name) or slugify_skill_name(leaf) or "skill"
            try:
                validate_skill_content(document)
            except SkillValidationError as e:
                failed.append({"path": root or leaf or SKILL_FILE_NAME, "reason": str(e)})
                continue

            attachments: dict[str, bytes] = {}
            for relative_dir, files in grouped.items():
                if _owner_root(relative_dir) != root:
                    continue
                prefix = "" if relative_dir == root else f"{relative_dir[len(root) + 1:]}/"
                for path, payload in files.items():
                    if path == SKILL_FILE_NAME:
                        continue
                    attachments[f"{prefix}{path}"] = payload

            try:
                _store(skill_name or candidate, candidate, document, attachments)
            except SkillValidationError as e:
                failed.append({"path": root or leaf, "reason": str(e)})

    if not created:
        detail = "技能包中没有可用的技能"
        if failed:
            detail = f"{detail}：{failed[0]['reason']}"
        raise HTTPException(status_code=400, detail=detail)

    logger.info(
        "已上传技能：scope=%s project=%s created=%d failed=%d",
        normalized,
        project,
        len(created),
        len(failed),
    )
    _refresh_sandbox_skills(normalized, project)
    return {"created": created, "failed": failed}
