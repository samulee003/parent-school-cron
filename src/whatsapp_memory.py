"""Persistent WhatsApp conversation memory."""

import json
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any, Dict

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
        with self._lock, sqlite3.connect(str(self.db_path)) as conn:
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
        with self._lock, sqlite3.connect(str(self.db_path)) as conn:
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

    def _get_json(self, phone: str, column: str) -> Dict[str, Any]:
        if column not in {"profile_json", "last_query_json"}:
            raise ValueError(f"Unsupported column: {column}")

        with self._lock, sqlite3.connect(str(self.db_path)) as conn:
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

        with self._lock, sqlite3.connect(str(self.db_path)) as conn:
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
