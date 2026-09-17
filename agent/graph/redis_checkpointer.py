"""Redis checkpoint compatibility for concurrent graph and interrupt writes."""

from langgraph.checkpoint.redis import RedisSaver


class ReliableRedisSaver(RedisSaver):
    def _load_pending_writes_with_registry_check(
        self,
        thread_id,
        checkpoint_ns,
        checkpoint_id,
        checkpoint_has_writes,
        registry_has_writes,
    ):
        # Graph checkpoints and task writes are persisted concurrently. A late
        # checkpoint put can overwrite has_writes=True with False even though
        # the interrupt is already stored and registered. Treat that flag as a
        # hint: the registry (or the parent's search fallback) is authoritative.
        return super()._load_pending_writes_with_registry_check(
            thread_id,
            checkpoint_ns,
            checkpoint_id,
            checkpoint_has_writes=True,
            registry_has_writes=registry_has_writes,
        )
