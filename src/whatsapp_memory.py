"""Persistent WhatsApp conversation memory."""

import json
import logging
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Dict, List
from contextlib import closing

logger = logging.getLogger(__name__)


class WhatsAppMemoryStore:
    """Small SQLite store for WhatsApp profile and last-query context."""

    def __init__(self, db_path: str = ""):
        if not db_path:
            db_path = os.environ.get("WHATSAPP_MEMORY_DB", "")
        if not db_path:
            data_dir = os.environ.get("WXAGENT_DATA_DIR", "./data")
            db_path = str(Path(data_dir) / "whatsapp_memory.db")

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._init_db()

    def _init_db(self) -> None:
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS whatsapp_memory (
                        phone TEXT PRIMARY KEY,
                        profile_json TEXT NOT NULL DEFAULT '{}',
                        last_query_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS processed_whatsapp_messages (
                        message_id TEXT PRIMARY KEY,
                        phone TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS llm_daily_usage (
                        phone TEXT NOT NULL,
                        usage_date TEXT NOT NULL,
                        count INTEGER NOT NULL DEFAULT 0,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (phone, usage_date)
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS llm_response_cache (
                        cache_key TEXT PRIMARY KEY,
                        response_text TEXT NOT NULL,
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS whatsapp_conversations (
                        phone TEXT PRIMARY KEY,
                        display_name TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL DEFAULT 'ai',
                        tags_json TEXT NOT NULL DEFAULT '[]',
                        notes TEXT NOT NULL DEFAULT '',
                        last_message_at TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS whatsapp_messages (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        phone TEXT NOT NULL,
                        direction TEXT NOT NULL,
                        source TEXT NOT NULL,
                        body TEXT NOT NULL,
                        meta_json TEXT NOT NULL DEFAULT '{}',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_whatsapp_messages_phone_created
                    ON whatsapp_messages (phone, created_at)
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS whatsapp_agent_flags (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        phone TEXT NOT NULL,
                        flag_type TEXT NOT NULL,
                        summary TEXT NOT NULL,
                        resolved_at TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.commit()

    def get_profile(self, phone: str) -> Dict[str, Any]:
        return dict(self._get_json(phone, "profile_json"))

    def save_profile(self, phone: str, profile: Dict[str, Any]) -> None:
        self._upsert_json(phone, "profile_json", profile)

    def clear_profile(self, phone: str) -> None:
        self.save_profile(phone, {})

    def get_last_query(self, phone: str) -> Dict[str, Any]:
        return dict(self._get_json(phone, "last_query_json"))

    def save_last_query(self, phone: str, query: Dict[str, Any]) -> None:
        safe_query = {
            "age_group": query.get("age_group", ""),
            "target": query.get("target", ""),
            "topic": query.get("topic", ""),
            "page": query.get("page", 1),
        }
        self._upsert_json(phone, "last_query_json", safe_query)

    def clear_last_query(self, phone: str) -> None:
        self.save_last_query(phone, {})

    def clear_user(self, phone: str) -> None:
        now = datetime.now().isoformat()
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                conn.execute(
                    """
                    INSERT INTO whatsapp_memory (
                        phone, profile_json, last_query_json, created_at, updated_at
                    )
                    VALUES (?, '{}', '{}', ?, ?)
                    ON CONFLICT(phone) DO UPDATE SET
                        profile_json='{}',
                        last_query_json='{}',
                        updated_at=excluded.updated_at
                    """,
                    (phone, now, now),
                )
                conn.commit()

    def record_message(
        self,
        phone: str,
        direction: str,
        source: str,
        body: str,
        meta: Dict[str, Any] | None = None,
    ) -> None:
        """Append a transcript message and touch the conversation row."""
        if not phone or not body:
            return
        if direction not in {"inbound", "outbound"}:
            raise ValueError(f"Unsupported direction: {direction}")
        if source not in {"parent", "ai", "admin", "system"}:
            raise ValueError(f"Unsupported source: {source}")

        now = datetime.now().isoformat()
        meta_payload = json.dumps(meta or {}, ensure_ascii=False)
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                self._upsert_conversation(conn, phone, now)
                conn.execute(
                    """
                    INSERT INTO whatsapp_messages (
                        phone, direction, source, body, meta_json, created_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (phone, direction, source, body, meta_payload, now),
                )
                conn.execute(
                    """
                    UPDATE whatsapp_conversations
                    SET last_message_at = ?, updated_at = ?
                    WHERE phone = ?
                    """,
                    (now, now, phone),
                )
                conn.commit()

    def list_conversations(self, limit: int = 50) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 50), 200))
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT
                        c.phone,
                        c.display_name,
                        c.status,
                        c.tags_json,
                        c.notes,
                        c.last_message_at,
                        c.created_at,
                        c.updated_at,
                        (
                            SELECT body FROM whatsapp_messages m
                            WHERE m.phone = c.phone
                            ORDER BY m.created_at DESC, m.id DESC
                            LIMIT 1
                        ) AS latest_message
                    FROM whatsapp_conversations c
                    ORDER BY COALESCE(NULLIF(c.last_message_at, ''), c.updated_at) DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [self._conversation_row_to_dict(row) for row in rows]

    def get_conversation(self, phone: str) -> Dict[str, Any]:
        if not phone:
            return {}
        now = datetime.now().isoformat()
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                conn.row_factory = sqlite3.Row
                self._upsert_conversation(conn, phone, now)
                conn.commit()
                row = conn.execute(
                    """
                    SELECT phone, display_name, status, tags_json, notes,
                           last_message_at, created_at, updated_at
                    FROM whatsapp_conversations
                    WHERE phone = ?
                    """,
                    (phone,),
                ).fetchone()
        return self._conversation_row_to_dict(row) if row else {}

    def get_messages(self, phone: str, limit: int = 100) -> List[Dict[str, Any]]:
        if not phone:
            return []
        limit = max(1, min(int(limit or 100), 300))
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    """
                    SELECT id, phone, direction, source, body, meta_json, created_at
                    FROM whatsapp_messages
                    WHERE phone = ?
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    (phone, limit),
                ).fetchall()
        return [self._message_row_to_dict(row) for row in reversed(rows)]

    def set_conversation_status(self, phone: str, status: str) -> Dict[str, Any]:
        if status not in {"ai", "human"}:
            raise ValueError(f"Unsupported conversation status: {status}")
        now = datetime.now().isoformat()
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                conn.row_factory = sqlite3.Row
                self._upsert_conversation(conn, phone, now)
                conn.execute(
                    """
                    UPDATE whatsapp_conversations
                    SET status = ?, updated_at = ?
                    WHERE phone = ?
                    """,
                    (status, now, phone),
                )
                conn.commit()
                row = conn.execute(
                    """
                    SELECT phone, display_name, status, tags_json, notes,
                           last_message_at, created_at, updated_at
                    FROM whatsapp_conversations
                    WHERE phone = ?
                    """,
                    (phone,),
                ).fetchone()
        return self._conversation_row_to_dict(row) if row else {}

    def is_human_takeover(self, phone: str) -> bool:
        return self.get_conversation(phone).get("status") == "human"

    def claim_message(self, message_id: str, phone: str = "") -> bool:
        """Return True only for the first time a WhatsApp message id is seen."""
        if not message_id:
            return True

        now = datetime.now().isoformat()
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                try:
                    conn.execute(
                        """
                        INSERT INTO processed_whatsapp_messages (
                            message_id, phone, created_at
                        )
                        VALUES (?, ?, ?)
                        """,
                        (message_id, phone, now),
                    )
                    conn.commit()
                    return True
                except sqlite3.IntegrityError:
                    return False

    def get_llm_cached_response(self, cache_key: str) -> str:
        if not cache_key:
            return ""

        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                row = conn.execute(
                    """
                    SELECT response_text FROM llm_response_cache
                    WHERE cache_key = ?
                    """,
                    (cache_key,),
                ).fetchone()
        return str(row[0]) if row and row[0] else ""

    def save_llm_cached_response(self, cache_key: str, response_text: str) -> None:
        if not cache_key or not response_text:
            return

        now = datetime.now().isoformat()
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                conn.execute(
                    """
                    INSERT INTO llm_response_cache (cache_key, response_text, created_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(cache_key) DO UPDATE SET
                        response_text=excluded.response_text,
                        created_at=excluded.created_at
                    """,
                    (cache_key, response_text, now),
                )
                conn.commit()

    def get_llm_usage_count(self, phone: str, usage_date: str = "") -> int:
        usage_date = usage_date or date.today().isoformat()
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                row = conn.execute(
                    """
                    SELECT count FROM llm_daily_usage
                    WHERE phone = ? AND usage_date = ?
                    """,
                    (phone, usage_date),
                ).fetchone()
        return int(row[0]) if row else 0

    def try_consume_llm_quota(self, phone: str, daily_limit: int, usage_date: str = "") -> bool:
        """Increment and return True when the user still has daily LLM quota."""
        return self.try_consume_llm_quotas(
            phone=phone,
            per_user_daily_limit=daily_limit,
            global_daily_limit=0,
            usage_date=usage_date,
        )

    def try_consume_llm_quotas(
        self,
        phone: str,
        per_user_daily_limit: int,
        global_daily_limit: int = 0,
        usage_date: str = "",
    ) -> bool:
        """Increment user/global usage when both quotas still allow a call."""
        daily_limit = per_user_daily_limit
        if daily_limit <= 0:
            return False

        usage_date = usage_date or date.today().isoformat()
        now = datetime.now().isoformat()
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                row = conn.execute(
                    """
                    SELECT count FROM llm_daily_usage
                    WHERE phone = ? AND usage_date = ?
                    """,
                    (phone, usage_date),
                ).fetchone()
                current_count = int(row[0]) if row else 0
                if current_count >= daily_limit:
                    return False

                global_phone = "__global__"
                if global_daily_limit > 0:
                    global_row = conn.execute(
                        """
                        SELECT count FROM llm_daily_usage
                        WHERE phone = ? AND usage_date = ?
                        """,
                        (global_phone, usage_date),
                    ).fetchone()
                    global_count = int(global_row[0]) if global_row else 0
                    if global_count >= global_daily_limit:
                        return False

                conn.execute(
                    """
                    INSERT INTO llm_daily_usage (phone, usage_date, count, updated_at)
                    VALUES (?, ?, 1, ?)
                    ON CONFLICT(phone, usage_date) DO UPDATE SET
                        count=count + 1,
                        updated_at=excluded.updated_at
                    """,
                    (phone, usage_date, now),
                )
                if global_daily_limit > 0:
                    conn.execute(
                        """
                        INSERT INTO llm_daily_usage (phone, usage_date, count, updated_at)
                        VALUES (?, ?, 1, ?)
                        ON CONFLICT(phone, usage_date) DO UPDATE SET
                            count=count + 1,
                            updated_at=excluded.updated_at
                        """,
                        (global_phone, usage_date, now),
                    )
                conn.commit()
                return True

    @staticmethod
    def _safe_json(raw: str, fallback: Any) -> Any:
        if not raw:
            return fallback
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return fallback

    def _upsert_conversation(self, conn: sqlite3.Connection, phone: str, now: str) -> None:
        conn.execute(
            """
            INSERT INTO whatsapp_conversations (
                phone, display_name, status, tags_json, notes,
                last_message_at, created_at, updated_at
            )
            VALUES (?, '', 'ai', '[]', '', '', ?, ?)
            ON CONFLICT(phone) DO UPDATE SET
                updated_at=excluded.updated_at
            """,
            (phone, now, now),
        )

    def _conversation_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        result["tags"] = self._safe_json(result.pop("tags_json", "[]"), [])
        return result

    def _message_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        result["meta"] = self._safe_json(result.pop("meta_json", "{}"), {})
        return result

    def _get_json(self, phone: str, column: str) -> Dict[str, Any]:
        if column not in {"profile_json", "last_query_json"}:
            raise ValueError(f"Unsupported column: {column}")

        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                row = conn.execute(
                    f"SELECT {column} FROM whatsapp_memory WHERE phone = ?",
                    (phone,),
                ).fetchone()

        if not row or not row[0]:
            return {}
        try:
            value = json.loads(row[0])
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            logger.warning("WhatsApp memory JSON damaged for %s column %s", phone, column)
            return {}

    def _upsert_json(self, phone: str, column: str, value: Dict[str, Any]) -> None:
        if column not in {"profile_json", "last_query_json"}:
            raise ValueError(f"Unsupported column: {column}")

        now = datetime.now().isoformat()
        payload = json.dumps(value, ensure_ascii=False)
        other_column = "last_query_json" if column == "profile_json" else "profile_json"

        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                conn.execute(
                    f"""
                    INSERT INTO whatsapp_memory (
                        phone, {column}, {other_column}, created_at, updated_at
                    )
                    VALUES (?, ?, '{{}}', ?, ?)
                    ON CONFLICT(phone) DO UPDATE SET
                        {column}=excluded.{column},
                        updated_at=excluded.updated_at
                    """,
                    (phone, payload, now, now),
                )
                conn.commit()
