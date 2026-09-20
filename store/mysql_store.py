"""LocalMySQLStore — MySQL 存储 Facade。

组合所有 Repo 类，对外提供与旧版一致的方法签名。
内部委托给各 Repo 执行具体操作，并协调跨表事务（如级联删除）。
"""

from __future__ import annotations

from typing import Any

from store.conversation_version_repo import ConversationVersionRepo
from store.event_repo import RunEventRepo
from store.finding_repo import FindingRepo
from store.mapping_repo import MappingRepo
from store.project_folder_repo import ProjectFolderRepo
from store.run_repo import RunRepo
from store.sandbox_repo import SandboxRepo
from store.thread_repo import ThreadRepo


class LocalMySQLStore:
    """MySQL 存储门面类。

    组合所有 Repo 并对外提供统一接口。每个 Repo 独立管理自己的连接和锁，
    本类负责协调跨表操作（如删除 thread 时级联清理附属记录）。
    """

    def __init__(self) -> None:
        """初始化所有 Repo 实例并创建业务表结构。"""
        self.thread_repo = ThreadRepo()
        self.run_repo = RunRepo()
        self.event_repo = RunEventRepo()
        self.finding_repo = FindingRepo()
        self.mapping_repo = MappingRepo()
        self.project_folder_repo = ProjectFolderRepo()
        self.conversation_version_repo = ConversationVersionRepo()
        self.sandbox_repo = SandboxRepo()
        self._init_schema()

    def _init_schema(self) -> None:
        """初始化所有业务表结构，确保各 Repo 对应的表已存在。"""
        self.thread_repo.ensure_table()
        self.run_repo.ensure_table()
        self.event_repo.ensure_table()
        self.finding_repo.ensure_table()
        self.mapping_repo.ensure_table()
        self.project_folder_repo.ensure_table()
        self.conversation_version_repo.ensure_table()
        self.sandbox_repo.ensure_table()

    def close(self) -> None:
        """关闭所有 Repo 的数据库连接，释放连接池资源。"""
        self.thread_repo.close()
        self.run_repo.close()
        self.event_repo.close()
        self.finding_repo.close()
        self.mapping_repo.close()
        self.project_folder_repo.close()
        self.conversation_version_repo.close()
        self.sandbox_repo.close()

    # ── Thread 操作 ──────────────────────────────────────────────────────────

    def upsert_thread(
        self,
        *,
        thread_id: str,
        title: str,
        repo_url: str | None = None,
        repo_owner: str | None = None,
        repo_name: str | None = None,
        branch_name: str | None = None,
        pr_url: str | None = None,
        user_prompt: str | None = None,
        latest_run_status: str = "pending",
        backend_type: str | None = None,
        project_name: str | None = None,
        local_work_dir: str | None = None,
        source: str | None = None,
        plan_source_prompt: str | None = None,
    ) -> None:
        """创建或更新一条 thread 记录。

        Args:
            thread_id: 会话唯一标识。
            title: 会话标题。
            repo_url: 仓库克隆地址。
            repo_owner: 仓库所有者。
            repo_name: 仓库名称。
            branch_name: 工作分支名。
            pr_url: Pull Request 地址。
            user_prompt: 用户原始输入。
            latest_run_status: 最近一次运行状态，默认 ``"pending"``。
            backend_type: 执行环境类型（``"local"`` 或 ``"sandbox"``）。
            project_name: 关联的项目名称（来自 ProjectSelector）。
            source: 会话来源（``"dashboard"`` / ``"schedule"`` 等）。
            plan_source_prompt: 生成技术方案时用的完整原始需求，多轮修订时保持不变。
        """
        self.thread_repo.upsert_thread(
            thread_id=thread_id,
            title=title,
            repo_url=repo_url,
            repo_owner=repo_owner,
            repo_name=repo_name,
            branch_name=branch_name,
            pr_url=pr_url,
            user_prompt=user_prompt,
            latest_run_status=latest_run_status,
            backend_type=backend_type,
            project_name=project_name,
            local_work_dir=local_work_dir,
            source=source,
            plan_source_prompt=plan_source_prompt,
        )

    def get_thread(self, thread_id: str) -> dict[str, Any] | None:
        """按 thread_id 查询单条 thread 记录。

        Args:
            thread_id: 会话唯一标识。

        Returns:
            匹配的 thread 字典，不存在时返回 None。
        """
        return self.thread_repo.get_thread(thread_id)

    def list_threads(self, limit: int = 50) -> list[dict[str, Any]]:
        """按更新时间倒序返回 thread 列表。

        Args:
            limit: 最大返回条数，默认 50。

        Returns:
            thread 字典列表。
        """
        return self.thread_repo.list_threads(limit)

    def list_thread_ids_by_source(
        self, source: str, *, repo_url: str | None = None, user_prompt: str | None = None
    ) -> list[str]:
        """按来源查询匹配的 thread ID 列表。

        Args:
            source: 会话来源标识。
            repo_url: 可选的仓库 URL 过滤条件。
            user_prompt: 可选的用户输入过滤条件。

        Returns:
            匹配的 thread ID 字符串列表。
        """
        return self.thread_repo.list_thread_ids_by_source(
            source, repo_url=repo_url, user_prompt=user_prompt
        )

    def update_thread_status(
        self,
        thread_id: str,
        status: str,
        *,
        pr_url: str | None = None,
        branch_name: str | None = None,
    ) -> None:
        """更新 thread 的运行状态。

        Args:
            thread_id: 会话唯一标识。
            status: 新的运行状态。
            pr_url: 可选的 Pull Request 地址。
            branch_name: 可选的工作分支名。
        """
        self.thread_repo.update_thread_status(
            thread_id, status, pr_url=pr_url, branch_name=branch_name
        )

    def update_thread_title(self, thread_id: str, title: str) -> bool:
        """更新 thread 标题。

        Args:
            thread_id: 会话唯一标识。
            title: 新标题。

        Returns:
            成功更新时返回 ``True``；记录不存在时返回 ``False``。
        """
        return self.thread_repo.update_thread_title(thread_id, title)

    def rename_project(self, old_name: str, new_name: str) -> int:
        """把某个项目名下所有会话批量重命名。

        同时同步该项目绑定的本地文件夹记录，保证改名后绑定不丢失。

        Args:
            old_name: 原项目名称。
            new_name: 新项目名称。

        Returns:
            受影响的会话条数。
        """
        affected = self.thread_repo.rename_project(old_name, new_name)
        self.project_folder_repo.rename(old_name, new_name)
        return affected

    def delete_thread(self, thread_id: str) -> bool:
        """删除一个 Dashboard 会话及其所有业务附属记录。

        级联清理顺序：先删除 thread 主记录（对应用层不可见），再清理附属记录。
        附属记录清理失败时记录日志，不阻断主记录删除。
        注意：LangGraph checkpoint 由独立的 persistence 层管理，此处不清理。

        Args:
            thread_id: 会话唯一标识。

        Returns:
            成功删除时返回 ``True``；记录不存在时返回 ``False``。
        """
        existing = self.thread_repo.get_thread(thread_id)
        if existing is None:
            return False

        self.thread_repo.delete_thread(thread_id)

        for cleanup_name, cleanup_fn in [
            ("findings", self.finding_repo.delete_findings_by_thread),
            ("events", self.event_repo.clear_run_events),
            ("runs", self.run_repo.delete_runs_by_thread),
            ("conversation versions", self.conversation_version_repo.delete_versions),
            ("sandbox registry", self.sandbox_repo.delete_by_thread),
        ]:
            try:
                cleanup_fn(thread_id)
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "级联删除 %s 失败（thread_id=%s），主记录已删除", cleanup_name, thread_id, exc_info=True,
                )
        return True

    # ── Run 操作 ─────────────────────────────────────────────────────────────

    def record_run(
        self,
        *,
        run_id: str,
        thread_id: str,
        status: str,
        error: str | None = None,
        finished: bool = False,
        base_commit: str | None = None,
        head_commit: str | None = None,
    ) -> None:
        """记录或更新一次 Agent 运行。

        Args:
            run_id: 运行唯一标识。
            thread_id: 所属会话 ID。
            status: 运行状态。
            error: 可选的错误信息。
            finished: 是否已结束，默认 ``False``。
        """
        self.run_repo.record_run(
            run_id=run_id,
            thread_id=thread_id,
            status=status,
            error=error,
            finished=finished,
            base_commit=base_commit,
            head_commit=head_commit,
        )

    def get_latest_run(self, thread_id: str) -> dict[str, Any] | None:
        """读取某个 thread 最近一次运行记录。

        Args:
            thread_id: 会话唯一标识。

        Returns:
            最近一次运行的字典，不存在时返回 None。
        """
        return self.run_repo.get_latest_run(thread_id)

    def count_runs(self, thread_id: str) -> int:
        """统计某个 thread 的运行次数。

        Args:
            thread_id: 会话唯一标识。

        Returns:
            运行次数。
        """
        return self.run_repo.count_runs(thread_id)

    def add_conversation_version(self, thread_id: str, payload: dict[str, Any]) -> None:
        """保存一条会话版本快照。

        Args:
            thread_id: 会话唯一标识。
            payload: 版本内容字典。
        """
        self.conversation_version_repo.add_version(thread_id, payload)

    def list_conversation_versions(self, thread_id: str) -> list[dict[str, Any]]:
        """读取某个会话的所有版本快照。

        Args:
            thread_id: 会话唯一标识。

        Returns:
            版本字典列表。
        """
        return self.conversation_version_repo.list_versions(thread_id)

    def save_conversation_view(self, thread_id: str, messages: list[dict[str, Any]]) -> None:
        """保存当前会话的消息视图快照。

        Args:
            thread_id: 会话唯一标识。
            messages: 当前页面完整消息列表。
        """
        self.conversation_version_repo.save_current_view(thread_id, messages)

    def get_conversation_view(self, thread_id: str) -> list[dict[str, Any]] | None:
        """读取某个会话最近保存的消息视图快照。

        Args:
            thread_id: 会话唯一标识。

        Returns:
            消息字典列表，未找到时返回 None。
        """
        return self.conversation_version_repo.get_current_view(thread_id)

    # ── Run Event 操作 ───────────────────────────────────────────────────────

    def add_run_event(
        self,
        *,
        event_id: str,
        thread_id: str,
        kind: str,
        title: str,
        status: str,
        detail: str | None = None,
    ) -> None:
        """记录或更新一条运行事件。

        Args:
            event_id: 事件唯一标识。
            thread_id: 所属会话 ID。
            kind: 事件类型。
            title: 事件标题。
            status: 事件状态。
            detail: 可选的事件详情。
        """
        self.event_repo.add_run_event(
            event_id=event_id,
            thread_id=thread_id,
            kind=kind,
            title=title,
            status=status,
            detail=detail,
        )

    def list_run_events(self, thread_id: str) -> list[dict[str, Any]]:
        """按创建顺序读取运行步骤。

        Args:
            thread_id: 会话唯一标识。

        Returns:
            事件字典列表。
        """
        return self.event_repo.list_run_events(thread_id)

    def clear_run_events(self, thread_id: str) -> None:
        """清空某个 thread 的临时过程事件。

        Args:
            thread_id: 会话唯一标识。
        """
        self.event_repo.clear_run_events(thread_id)

    def finish_open_run_events(self, thread_id: str, *, status: str = "completed") -> None:
        """把仍处于运行中的展示事件收尾。

        Args:
            thread_id: 会话唯一标识。
            status: 收尾状态值，默认 ``"completed"``。
        """
        self.event_repo.finish_open_run_events(thread_id, status=status)

    # ── Finding 操作 ─────────────────────────────────────────────────────────

    def add_finding(
        self,
        *,
        finding_id: str,
        thread_id: str,
        file: str,
        line: int | None,
        severity: str,
        title: str,
        description: str,
        status: str = "open",
    ) -> None:
        """记录一条审查发现。

        Args:
            finding_id: 发现唯一标识。
            thread_id: 所属会话 ID。
            file: 发现所在文件路径。
            line: 发现所在行号。
            severity: 严重程度。
            title: 发现标题。
            description: 发现描述。
            status: 发现状态，默认 ``"open"``。
        """
        self.finding_repo.add_finding(
            finding_id=finding_id,
            thread_id=thread_id,
            file=file,
            line=line,
            severity=severity,
            title=title,
            description=description,
            status=status,
        )

    def list_findings(self, thread_id: str) -> list[dict[str, Any]]:
        """读取某个 thread 的审查发现。

        Args:
            thread_id: 会话唯一标识。

        Returns:
            审查发现字典列表。
        """
        return self.finding_repo.list_findings(thread_id)

    # ── Mapping 操作 ─────────────────────────────────────────────────────────

    def upsert_mapping(
        self,
        *,
        mapping_id: str,
        repo_url: str,
        workspace_path: str,
        repo_owner: str | None = None,
        repo_name: str | None = None,
    ) -> None:
        """创建或更新仓库到工作区的映射。

        Args:
            mapping_id: 映射唯一标识。
            repo_url: 仓库克隆地址。
            workspace_path: 本地工作区路径。
            repo_owner: 仓库所有者。
            repo_name: 仓库名称。
        """
        self.mapping_repo.upsert_mapping(
            mapping_id=mapping_id,
            repo_url=repo_url,
            workspace_path=workspace_path,
            repo_owner=repo_owner,
            repo_name=repo_name,
        )

    def get_mapping_by_repo(self, repo_url: str) -> dict[str, Any] | None:
        """按仓库 URL 查询映射记录。

        Args:
            repo_url: 仓库 URL。

        Returns:
            匹配的映射字典，不存在时返回 None。
        """
        return self.mapping_repo.get_mapping_by_repo(repo_url)

    def list_mappings(self) -> list[dict[str, Any]]:
        """返回所有映射记录。

        Returns:
            映射字典列表。
        """
        return self.mapping_repo.list_mappings()

    # ── Project Folder 操作（项目名 → 本地工作文件夹绑定）─────────────────────

    def upsert_project_folder(self, project_name: str, local_folder: str, display_name: str | None = None) -> None:
        """创建或更新项目到本地工作文件夹的绑定。

        Args:
            project_name: 项目名称。
            local_folder: 本地文件夹绝对路径。
            display_name: 项目显示名称（可选，用于 UI 展示）。
        """
        self.project_folder_repo.upsert(project_name, local_folder, display_name)

    def get_project_folder(self, project_name: str) -> dict[str, Any] | None:
        """按项目名查询本地文件夹绑定。

        Args:
            project_name: 项目名称。

        Returns:
            绑定记录字典，不存在时返回 None。
        """
        return self.project_folder_repo.get(project_name)

    def list_project_folders(self) -> list[dict[str, Any]]:
        """返回所有项目本地文件夹绑定，按更新时间降序。

        Returns:
            绑定记录字典列表。
        """
        return self.project_folder_repo.list_all()
