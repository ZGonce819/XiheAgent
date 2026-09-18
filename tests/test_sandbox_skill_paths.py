"""技能分层到沙箱落点的路径契约。

这一组断言锁死的是「同步落点」与「挂载源」必须一致。历史上两者是错位的
——技能被写进 ``/workspace/skills``，而会话拿到的挂载源是 ``/skills-global/``，
由于沙箱 backend 不做虚拟路径映射，``SkillsMiddleware`` 一直在报
``path_not_found``，沙箱里的技能实际上从未加载成功过。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services.skill_registry import (
    SANDBOX_SKILL_ROOT,
    load_sandbox_skill_documents,
    sandbox_project_set_root,
    sandbox_skill_roots,
    sandbox_skill_sources,
)


def test_sandbox_root_is_container_absolute_path():
    """沙箱根目录必须是容器内绝对路径，不能是虚拟路径。"""
    assert SANDBOX_SKILL_ROOT == "/workspace/skills"
    assert SANDBOX_SKILL_ROOT.startswith("/")


@pytest.mark.parametrize("project_name", [None, "", "   "])
def test_sources_without_project_only_mount_global(project_name: str | None):
    """不属于任何项目集合时，只挂全局层。"""
    assert sandbox_skill_sources(project_name) == ["/workspace/skills/"]


def test_sources_with_project_mount_global_then_project_set():
    """属于项目集合时挂两层，且项目集合层排在后面（后写覆盖先写）。"""
    assert sandbox_skill_sources("Xihe") == [
        "/workspace/skills/",
        "/workspace/skills/.project-set/Xihe/",
    ]


def test_project_set_root_preserves_case_and_blocks_escape():
    """集合名保留大小写；路径分隔符等危险字符被清洗掉。"""
    assert sandbox_project_set_root("Xihe") == "/workspace/skills/.project-set/Xihe"
    escaped = sandbox_project_set_root("../../etc/passwd")
    assert ".." not in escaped
    assert escaped.startswith("/workspace/skills/.project-set/")


def test_project_set_root_rejects_empty_name():
    """空集合名无法定位目录，应显式报错而不是静默落到父目录。"""
    from services.skill_registry import SkillValidationError

    with pytest.raises(SkillValidationError):
        sandbox_project_set_root("///")


def test_roots_always_include_global_even_without_skills():
    """没有任何技能时也要维护全局根目录，否则加载器会刷 path_not_found。"""
    assert sandbox_skill_roots(None) == ["/workspace/skills"]
    assert sandbox_skill_roots("Xihe") == [
        "/workspace/skills",
        "/workspace/skills/.project-set/Xihe",
    ]


def test_roots_are_prefix_of_sources():
    """维护的目录必须能覆盖所有挂载源，避免出现「挂了但没建」。"""
    for project_name in (None, "Xihe"):
        roots = sandbox_skill_roots(project_name)
        for source in sandbox_skill_sources(project_name):
            assert source.rstrip("/") in roots


def test_documents_global_only_when_no_project(skills_root: Path, make_skill):
    """没有项目时只同步全局层的技能。"""
    make_skill(skills_root / "alpha", "alpha")

    documents = load_sandbox_skill_documents(None)

    assert [path for path, _ in documents] == ["/workspace/skills/alpha/SKILL.md"]
    assert b"name: alpha" in documents[0][1]


def test_documents_append_project_set_layer(skills_root: Path, make_skill):
    """有项目时全局层在前、项目集合层在后，同名由后者覆盖。"""
    make_skill(skills_root / "alpha", "alpha")
    make_skill(skills_root / ".project-set" / "Xihe" / "beta", "beta")

    documents = load_sandbox_skill_documents("Xihe")

    assert [path for path, _ in documents] == [
        "/workspace/skills/alpha/SKILL.md",
        "/workspace/skills/.project-set/Xihe/beta/SKILL.md",
    ]


def test_documents_include_extra_files(skills_root: Path, make_skill):
    """技能目录里的附属文件要一并同步，否则技能里的引用会断链。"""
    directory = make_skill(skills_root / "alpha", "alpha")
    (directory / "reference.md").write_text("参考内容", encoding="utf-8")

    documents = load_sandbox_skill_documents(None)
    paths = [path for path, _ in documents]

    assert "/workspace/skills/alpha/SKILL.md" in paths
    assert "/workspace/skills/alpha/reference.md" in paths


def test_documents_empty_when_no_skills(skills_root: Path):
    """一个技能都没有时返回空清单，但仍需由 roots 建出目录。"""
    assert load_sandbox_skill_documents(None) == []
    assert load_sandbox_skill_documents("Xihe") == []
    assert sandbox_skill_roots("Xihe")


def test_documents_targets_match_sources(skills_root: Path, make_skill):
    """每条同步路径都必须落在某个挂载源之下——这是本次修复的核心不变量。"""
    make_skill(skills_root / "alpha", "alpha")
    make_skill(skills_root / ".project-set" / "Xihe" / "beta", "beta")

    sources = [source.rstrip("/") + "/" for source in sandbox_skill_sources("Xihe")]
    for path, _ in load_sandbox_skill_documents("Xihe"):
        assert any(path.startswith(source) for source in sources), path
