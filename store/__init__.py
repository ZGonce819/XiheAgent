"""xihe-agent 数据存储子包。

提供 MySQL 持久化存储，管理仓库映射、任务状态和业务事件。

存储层采用 per-table Repo 拆分架构：

* :class:`ConnectionManager` — 连接管理基类。
* :class:`ThreadRepo` / :class:`RunRepo` / :class:`RunEventRepo` …
  各表独立 CRUD。
* :class:`LocalMySQLStore` — Facade，组合所有 Repo 并对外提供
  与旧版一致的方法签名。

Example:
    >>> from store import LocalMySQLStore
    >>> store = LocalMySQLStore(host="localhost", database="xihe-agent")
    >>> store.upsert_thread(thread_id="t1", title="User task")
"""

from database.mysql import ConnectionManager, utc_now
from store.finding_repo import FindingRepo
from store.mapping_repo import MappingRepo
from store.mysql_store import LocalMySQLStore
from store.event_repo import RunEventRepo
from store.run_repo import RunRepo
from store.thread_repo import ThreadRepo
from store.conversation_version_repo import ConversationVersionRepo
from store.sandbox_repo import SandboxRepo

__all__ = [
    "ConnectionManager",
    "FindingRepo",
    "LocalMySQLStore",
    "MappingRepo",
    "RunEventRepo",
    "RunRepo",
    "ThreadRepo",
    "ConversationVersionRepo",
    "SandboxRepo",
    "utc_now",
]
