from __future__ import annotations

import json

from api.dashboard import _is_latest_user_message
from store.conversation_version_repo import ConversationVersionRepo


def test_realtime_id_only_matches_latest_message_content() -> None:
    messages = [
        {"id": "old", "role": "user", "content": "first"},
        {"id": "checkpoint-latest", "role": "user", "content": "edited"},
    ]

    assert _is_latest_user_message(
        "thread-1-user-run-2", "thread-1", messages, "edited"
    )
    assert not _is_latest_user_message(
        "thread-1-user-run-1", "thread-1", messages, "first"
    )


def test_version_rows_restore_messages_and_run_events() -> None:
    repo = object.__new__(ConversationVersionRepo)
    repo.fetch_all = lambda *_args, **_kwargs: [
        {
            "version_number": 2,
            "created_at": "2026-09-12T12:00:00+00:00",
            "payload_json": json.dumps({
                "messages": [{"id": "user-2", "author": "user", "chunks": []}],
                "runEvents": [{"id": "event-2"}],
            }),
        }
    ]

    versions = repo.list_versions("thread-1")

    assert versions[0]["number"] == 2
    assert versions[0]["messages"][0]["id"] == "user-2"
    assert versions[0]["runEvents"][0]["id"] == "event-2"
