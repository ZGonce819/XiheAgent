"""xihe-agent 业务实体模型包。

提供与各数据库表对应的 dataclass 实体，用于在 Store 层和 Service 层之间
传递结构化数据，避免直接使用裸字典。

每个实体都提供 ``from_row`` 类方法（从 DictCursor 查询结果构造）和
``to_row`` 方法（序列化为可写入数据库的字典）。
"""

from models.event import RunEvent
from models.finding import ReviewFinding
from models.mapping import RepoWorkspaceMapping
from models.run import Run
from models.thread import Thread

__all__ = [
    "RepoWorkspaceMapping",
    "ReviewFinding",
    "Run",
    "RunEvent",
    "Thread",
]
