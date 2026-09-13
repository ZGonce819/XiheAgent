"""LocalMySQLStore — MySQL 存储 Facade。

组合所有 Repo 类，对外提供与旧版一致的方法签名。
内部委托给各 Repo 执行具体操作，并协调跨表事务（如级联删除）。
"""

from __future__ import annotations

from typing import Any

from store.event_repo import RunEventRepo
from store.finding_repo import FindingRepo
from store.mapping_repo import MappingRepo
from store.message_repo import MessageRepo
from store.plan_repo import PlanRepo
from store.run_repo import RunRepo
from store.setting_repo import SettingRepo
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
        self.message_repo = MessageRepo()
        self.plan_repo = PlanRepo()
        self.finding_repo = FindingRepo()
        self.setting_repo = SettingRepo()
        self.mapping_repo = MappingRepo()
        self._init_schema()

    def _init_schema(self) -> None:
        """初始化所有业务表结构，确保各 Repo 对应的表已存在。"""
        self.thread_repo.ensure_table()
        self.run_repo.ensure_table()
        self.event_repo.ensure_table()
        self.message_repo.ensure_table()
        self.plan_repo.ensure_table()
        self.finding_repo.ensure_table()
        self.setting_repo.ensure_table()
        self.mapping_repo.ensure_table()

    def close(self) -> None:
        """关闭所有 Repo 的数据库连接，释放连接池资源。"""
        self.thread_repo.close()
        self.run_repo.close()
        self.event_repo.close()
        self.message_repo.close()
        self.plan_repo.close()
        self.finding_repo.close()
        self.setting_repo.close()
        self.mapping_repo.close()

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
            ("plans", self.plan_repo.delete_plans_by_thread),
            ("messages", self.message_repo.delete_messages_by_thread),
            ("events", self.event_repo.clear_run_events),
            ("runs", self.run_repo.delete_runs_by_thread),
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
        )

    def get_latest_run(self, thread_id: str) -> dict[str, Any] | None:
        """读取某个 thread 最近一次运行记录。

        Args:
            thread_id: 会话唯一标识。

        Returns:
            最近一次运行的字典，不存在时返回 None。
        """
        return self.run_repo.get_latest_run(thread_id)

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

    # ── Message 操作 ─────────────────────────────────────────────────────────

    def add_thread_message(
        self,
        *,
        message_id: str,
        thread_id: str,
        author: str,
        content: str,
        run_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """追加保存一条 Dashboard 会话消息。

        Args:
            message_id: 消息唯一标识。
            thread_id: 所属会话 ID。
            author: 消息作者。
            content: 消息正文。
            run_id: 关联的运行 ID。
            metadata: 可选的消息元数据。
        """
        self.message_repo.add_thread_message(
            message_id=message_id,
            thread_id=thread_id,
            author=author,
            content=content,
            run_id=run_id,
            metadata=metadata,
        )

    def list_thread_messages(self, thread_id: str) -> list[dict[str, Any]]:
        """按写入顺序读取会话消息。

        Args:
            thread_id: 会话唯一标识。

        Returns:
            消息字典列表。
        """
        return self.message_repo.list_thread_messages(thread_id)

    # ── Plan 操作 ────────────────────────────────────────────────────────────

    def add_thread_plan(
        self,
        *,
        plan_id: str,
        thread_id: str,
        prompt: str,
        plan_text: str,
        plan_path: str,
        run_id: str | None = None,
        status: str = "pending",
    ) -> None:
        """保存一份编码前技术方案。

        Args:
            plan_id: 方案唯一标识。
            thread_id: 所属会话 ID。
            prompt: 用户原始输入。
            plan_text: 方案正文。
            plan_path: 方案 Markdown 文件路径。
            run_id: 关联的运行 ID。
            status: 方案状态，默认 ``"pending"``。
        """
        self.plan_repo.add_thread_plan(
            plan_id=plan_id,
            thread_id=thread_id,
            prompt=prompt,
            plan_text=plan_text,
            plan_path=plan_path,
            run_id=run_id,
            status=status,
        )

    def get_latest_thread_plan(
        self,
        thread_id: str,
        *,
        status: str | None = None,
    ) -> dict[str, Any] | None:
        """读取某个 thread 最新一份技术方案。

        Args:
            thread_id: 所属会话 ID。
            status: 可选状态过滤条件。

        Returns:
            最新的方案记录字典；不存在时返回 ``None``。
        """
        return self.plan_repo.get_latest_thread_plan(thread_id, status=status)

    def list_thread_plans(self, thread_id: str) -> list[dict[str, Any]]:
        """按创建顺序读取方案历史。

        Args:
            thread_id: 会话唯一标识。

        Returns:
            方案字典列表。
        """
        return self.plan_repo.list_thread_plans(thread_id)

    def approve_thread_plan(self, plan_id: str) -> dict[str, Any] | None:
        """把指定方案标记为已确认。

        Args:
            plan_id: 方案唯一标识。

        Returns:
            更新后的方案字典，不存在时返回 None。
        """
        return self.plan_repo.approve_thread_plan(plan_id)

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

    # ── Setting 操作 ─────────────────────────────────────────────────────────

    def set_setting(self, key: str, value: Any) -> None:
        """写入或更新一个配置项。

        Args:
            key: 配置键名。
            value: 配置值。
        """
        self.setting_repo.set_setting(key, value)

    def get_setting(self, key: str, default: Any = None) -> Any:
        """读取一个配置项。

        Args:
            key: 配置键名。
            default: 键不存在时的默认返回值。

        Returns:
            配置值，或 ``default``。
        """
        return self.setting_repo.get_setting(key, default)

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
