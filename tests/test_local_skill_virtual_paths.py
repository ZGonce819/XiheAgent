"""本地 Backend 的技能虚拟路径映射。"""

from __future__ import annotations

from pathlib import Path

from backends.local_shell import LocalShellBackend


def test_workspace_mode_resolves_global_and_project_set_aliases(tmp_path: Path) -> None:
    backend = LocalShellBackend(tmp_path / "workspace")

    assert backend._resolve_virtual_path("/skills-global") == backend.global_skills_dir
    assert backend._resolve_virtual_path("/skills-set/Xihe") == (
        backend.project_set_skills_dir / "Xihe"
    ).resolve()


def test_local_folder_mode_resolves_same_skill_aliases(tmp_path: Path) -> None:
    backend = LocalShellBackend(
        tmp_path / "project",
        local_folder_mode=True,
        global_workspace_root=tmp_path / "workspace",
    )

    assert backend._resolve_virtual_path("/skills-global") == backend.global_skills_dir
    assert backend._resolve_virtual_path("/skills-set/Xihe") == (
        backend.project_set_skills_dir / "Xihe"
    ).resolve()
