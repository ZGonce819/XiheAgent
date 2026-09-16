"""Conversation version snapshots used by the read-only history switcher."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from database.mysql import ConnectionManager, utc_now


class ConversationVersionRepo(ConnectionManager):
    """Persist the conversation view immediately before a message edit."""

    def ensure_table(self) -> None:
        self.execute("""
            CREATE TABLE IF NOT EXISTS conversation_versions (
                id BIGINT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
                thread_id VARCHAR(64) NOT NULL,
                version_number INT NOT NULL,
                fingerprint CHAR(64) NOT NULL,
                payload_json LONGTEXT NOT NULL,
                created_at VARCHAR(32) NOT NULL,
                UNIQUE KEY uq_thread_version (thread_id, version_number),
                UNIQUE KEY uq_thread_fingerprint (thread_id, fingerprint),
                INDEX idx_thread_id (thread_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        self.execute("""
            CREATE TABLE IF NOT EXISTS conversation_view_snapshots (
                thread_id VARCHAR(64) NOT NULL PRIMARY KEY,
                payload_json LONGTEXT NOT NULL,
                updated_at VARCHAR(32) NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

    def add_version(self, thread_id: str, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)
        fingerprint = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        self.execute(
            """
            INSERT IGNORE INTO conversation_versions
                (thread_id, version_number, fingerprint, payload_json, created_at)
            SELECT %s, COALESCE(MAX(version_number), 0) + 1, %s, %s, %s
            FROM conversation_versions
            WHERE thread_id = %s
            """,
            (thread_id, fingerprint, encoded, utc_now(), thread_id),
        )

    def list_versions(self, thread_id: str) -> list[dict[str, Any]]:
        rows = self.fetch_all(
            """
            SELECT version_number, payload_json, created_at
            FROM conversation_versions
            WHERE thread_id = %s
            ORDER BY version_number ASC
            """,
            (thread_id,),
        )
        versions: list[dict[str, Any]] = []
        for row in rows:
            try:
                payload = json.loads(row.get("payload_json") or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            if not isinstance(payload, dict):
                continue
            versions.append({
                "number": int(row.get("version_number") or len(versions) + 1),
                "createdAt": row.get("created_at"),
                "messages": payload.get("messages") or [],
                "runEvents": payload.get("runEvents") or [],
                "editedMessageId": payload.get("editedMessageId"),
                "editedUserIndex": payload.get("editedUserIndex"),
            })
        return versions

    def delete_versions(self, thread_id: str) -> int:
        deleted = self.execute(
            "DELETE FROM conversation_versions WHERE thread_id = %s",
            (thread_id,),
        )
        deleted += self.execute(
            "DELETE FROM conversation_view_snapshots WHERE thread_id = %s",
            (thread_id,),
        )
        return deleted

    def save_current_view(self, thread_id: str, messages: list[dict[str, Any]]) -> None:
        encoded = json.dumps(messages, ensure_ascii=False, separators=(",", ":"), default=str)
        self.execute(
            """
            INSERT INTO conversation_view_snapshots (thread_id, payload_json, updated_at)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                payload_json = VALUES(payload_json),
                updated_at = VALUES(updated_at)
            """,
            (thread_id, encoded, utc_now()),
        )

    def get_current_view(self, thread_id: str) -> list[dict[str, Any]] | None:
        row = self.fetch_one(
            "SELECT payload_json FROM conversation_view_snapshots WHERE thread_id = %s",
            (thread_id,),
        )
        if not row:
            return None
        try:
            payload = json.loads(row.get("payload_json") or "[]")
        except (TypeError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, list) else None
