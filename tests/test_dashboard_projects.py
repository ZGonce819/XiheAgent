"""项目列表与创建接口的测试。"""

from __future__ import annotations

from types import SimpleNamespace

from api import dashboard
from schemas.request_schema import DashboardProjectBindFolderRequest


class _Store:
    def __init__(self, records: list[dict[str, str]]) -> None:
        self.records = records
        self.saved: tuple[str, str] | None = None

    def list_project_folders(self) -> list[dict[str, str]]:
        return self.records

    def upsert_project_folder(
        self, project_name: str, local_folder: str, display_name: str | None = None
    ) -> None:
        self.saved = (project_name, local_folder)


def test_list_projects_separates_created_projects_from_workspace_folders(
    monkeypatch, tmp_path
) -> None:
    """下拉数据按来源分组：已创建项目在上，工作目录文件夹在下。"""
    projects_dir = tmp_path / "projects"
    projects_dir.mkdir()
    workspace_folder = projects_dir / "workspace-repo"
    workspace_folder.mkdir()
    monkeypatch.setattr(
        dashboard,
        "get_settings",
        lambda: SimpleNamespace(projects_dir=projects_dir),
    )
    monkeypatch.setattr(
        dashboard,
        "get_store",
        lambda: _Store(
            [{"project_name": "XiheAgentTest", "local_folder": ""}]
        ),
    )

    result = dashboard.dashboard_list_projects()

    assert result["created_projects"] == [
        {
            "name": "XiheAgentTest",
            "path": "",
            "local_folder": None,
            "source": "created",
        }
    ]
    assert result["workspace_projects"] == [
        {
            "name": "workspace-repo",
            "path": str(workspace_folder),
            "local_folder": str(workspace_folder),
            "source": "workspace",
        }
    ]


def test_create_project_without_local_folder_is_persisted(monkeypatch) -> None:
    """沙箱项目不填本地文件夹也必须被持久化，刷新后仍能出现在下拉中。"""
    store = _Store([])
    monkeypatch.setattr(dashboard, "get_store", lambda: store)

    result = dashboard.dashboard_bind_project_folder(
        DashboardProjectBindFolderRequest(project_name="SandboxProject")
    )

    assert store.saved == ("SandboxProject", "")
    assert result == {"project_name": "SandboxProject"}
