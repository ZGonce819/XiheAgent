"""Redis checkpoint 并发写入兼容模块。

解决 LangGraph 图执行过程中 checkpoint 持久化与 interrupt 写入并发时的
数据覆盖问题。通过子类化 ``RedisSaver``，强制将 ``checkpoint_has_writes``
标志交由注册表（registry）裁决，避免晚到的 checkpoint 写入错误地覆盖
已存储的 interrupt 状态。
"""

from langgraph.checkpoint.redis import RedisSaver


class ReliableRedisSaver(RedisSaver):
    """可靠的 Redis checkpoint 保存器。

    继承自 ``RedisSaver``，修复图 checkpoint 与任务写入并发持久化时
    ``has_writes`` 标志被错误覆盖的问题。
    """

    def _load_pending_writes_with_registry_check(
        self,
        thread_id,
        checkpoint_ns,
        checkpoint_id,
        checkpoint_has_writes,
        registry_has_writes,
    ):
        """加载待处理写入并校验注册表一致性。

        图 checkpoint 与任务写入是并发持久化的。晚到的 checkpoint put
        可能将 ``has_writes`` 从 ``True`` 错误覆盖为 ``False``，即使
        interrupt 已经存储并注册。因此将该标志视为提示，以注册表
        （或父级的 search 回退）的结果为权威依据。

        Args:
            thread_id: LangGraph 会话标识。
            checkpoint_ns: checkpoint 命名空间。
            checkpoint_id: checkpoint 唯一标识。
            checkpoint_has_writes: checkpoint 自身记录的写入标志（视为提示）。
            registry_has_writes: 注册表中的写入标志（权威依据）。

        Returns:
            父类方法的返回值，包含已加载的待处理写入列表。
        """
        # 图 checkpoint 与任务写入是并发持久化的。晚到的 checkpoint put
        # 可能将 has_writes 从 True 错误覆盖为 False，即使 interrupt
        # 已经存储并注册。将该标志视为提示：以注册表（或父级的 search
        # 回退）的结果为权威依据。
        return super()._load_pending_writes_with_registry_check(
            thread_id,
            checkpoint_ns,
            checkpoint_id,
            checkpoint_has_writes=True,
            registry_has_writes=registry_has_writes,
        )
