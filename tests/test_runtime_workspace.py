"""运行时工作区设置的回归测试。"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_local_backend_rebuilds_after_workspace_root_changes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """设置页修改工作区后，新会话不能继续复用旧目录的 backend。"""
    import backends.local_shell as local_shell
    from config.app_config import get_settings

    settings = get_settings()
    first_root = tmp_path / "first-workspace"
    second_root = tmp_path / "second-workspace"
    monkeypatch.setattr(settings, "workspace_root", first_root)
    monkeypatch.setattr(local_shell, "_BACKEND", None)

    first = local_shell.get_local_shell_backend()
    monkeypatch.setattr(settings, "workspace_root", second_root)
    second = local_shell.get_local_shell_backend()

    assert first.root == first_root.resolve()
    assert second.root == second_root.resolve()
    assert second is not first
    assert second.projects_dir == second_root.resolve() / "projects"


def test_repository_path_uses_runtime_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """顶部路径和回退逻辑都必须读取设置页当前保存的根目录。"""
    from config.app_config import get_settings
    from services.repo_rollback import repository_path

    class Store:
        def get_mapping_by_repo(self, _repo_url: str) -> dict[str, str]:
            return {"workspace_path": "projects/example"}

    workspace = tmp_path / "changed-workspace"
    monkeypatch.setattr(get_settings(), "workspace_root", workspace)

    assert repository_path("https://gitee.com/acme/example.git", Store()) == workspace / "projects" / "example"
