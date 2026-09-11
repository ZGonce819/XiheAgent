"""Xihe 数据存储子包。

提供 MySQL 持久化存储，管理仓库映射、任务状态和业务事件。

存储层采用 per-table Repo 拆分架构：

* :class:`ConnectionManager` — 连接管理基类。
* :class:`ThreadRepo` / :class:`RunRepo` / :class:`RunEventRepo` …
  各表独立 CRUD。
* :class:`LocalMySQLStore` — Facade，组合所有 Repo 并对外提供
  与旧版一致的方法签名。

Example:
    >>> from store import LocalMySQLStore
    >>> store = LocalMySQLStore(host="localhost", database="xihe")
    >>> store.upsert_thread(thread_id="t1", title="User task")
"""

# 导入连接管理基类和 UTC 时间工具函数
from database.mysql import ConnectionManager, utc_now
# 导入各表 Repo 类，每个 Repo 负责一张表的 CRUD
from store.finding_repo import FindingRepo
from store.mapping_repo import MappingRepo
from store.message_repo import MessageRepo
# 导入 Facade 类，组合所有 Repo 对外提供统一接口
from store.mysql_store import LocalMySQLStore
from store.plan_repo import PlanRepo
from store.event_repo import RunEventRepo
from store.run_repo import RunRepo
from store.setting_repo import SettingRepo
from store.thread_repo import ThreadRepo

# 向后兼容别名：旧代码可能引用 LocalSqliteStore，实际指向 LocalMySQLStore
LocalSqliteStore = LocalMySQLStore

# 公开导出的符号列表
__all__ = [
    "ConnectionManager",   # 连接管理基类
    "FindingRepo",         # review_findings 表 Repo
    "LocalMySQLStore",     # MySQL 存储 Facade
    "LocalSqliteStore",    # 向后兼容别名
    "MappingRepo",         # repo_workspace_mappings 表 Repo
    "MessageRepo",         # thread_messages 表 Repo
    "PlanRepo",            # thread_plans 表 Repo
    "RunEventRepo",        # run_events 表 Repo
    "RunRepo",             # runs 表 Repo
    "SettingRepo",         # settings 表 Repo
    "ThreadRepo",          # threads 表 Repo
    "utc_now",             # UTC 时间戳工具函数
]
