"""Persistent WhatsApp conversation memory."""

import json
import logging
import os
import sqlite3
from datetime import date, datetime
from pathlib import Path
from threading import RLock
from typing import Any, Dict
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
                conn.commit()
                return True

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
