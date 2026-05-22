"""Persistent WhatsApp conversation memory."""

import hashlib
import json
import logging
import os
import sqlite3
from datetime import date, datetime, timedelta
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
                        consent_status TEXT NOT NULL DEFAULT 'unknown',
                        tags_json TEXT NOT NULL DEFAULT '[]',
                        notes TEXT NOT NULL DEFAULT '',
                        proactive_notes TEXT NOT NULL DEFAULT '',
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
                        meta_json TEXT NOT NULL DEFAULT '{}',
                        resolved_at TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS whatsapp_proactive_drafts (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        phone TEXT NOT NULL,
                        status TEXT NOT NULL DEFAULT 'draft',
                        draft_text TEXT NOT NULL,
                        original_text TEXT NOT NULL DEFAULT '',
                        sent_text TEXT NOT NULL DEFAULT '',
                        matches_json TEXT NOT NULL DEFAULT '[]',
                        profile_json TEXT NOT NULL DEFAULT '{}',
                        meta_json TEXT NOT NULL DEFAULT '{}',
                        fingerprint TEXT NOT NULL DEFAULT '',
                        error_text TEXT NOT NULL DEFAULT '',
                        sent_message_type TEXT NOT NULL DEFAULT '',
                        sent_at TEXT NOT NULL DEFAULT '',
                        skipped_at TEXT NOT NULL DEFAULT '',
                        failed_at TEXT NOT NULL DEFAULT '',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_whatsapp_proactive_drafts_status_updated
                    ON whatsapp_proactive_drafts (status, updated_at)
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_whatsapp_proactive_drafts_phone_status
                    ON whatsapp_proactive_drafts (phone, status)
                    """
                )
                conn.execute(
                    """
                    CREATE TABLE IF NOT EXISTS whatsapp_qa_feedback (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        phone TEXT NOT NULL,
                        message_id INTEGER NOT NULL DEFAULT 0,
                        rating TEXT NOT NULL DEFAULT 'bad',
                        issue_type TEXT NOT NULL DEFAULT 'other',
                        summary TEXT NOT NULL DEFAULT '',
                        expected_behavior TEXT NOT NULL DEFAULT '',
                        anonymized_json TEXT NOT NULL DEFAULT '{}',
                        status TEXT NOT NULL DEFAULT 'open',
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_whatsapp_qa_feedback_status_updated
                    ON whatsapp_qa_feedback (status, updated_at)
                    """
                )
                conn.execute(
                    """
                    CREATE INDEX IF NOT EXISTS idx_whatsapp_qa_feedback_phone_created
                    ON whatsapp_qa_feedback (phone, created_at)
                    """
                )
                self._ensure_column(
                    conn,
                    "whatsapp_agent_flags",
                    "meta_json",
                    "TEXT NOT NULL DEFAULT '{}'",
                )
                self._ensure_column(
                    conn,
                    "whatsapp_conversations",
                    "consent_status",
                    "TEXT NOT NULL DEFAULT 'unknown'",
                )
                self._ensure_column(
                    conn,
                    "whatsapp_conversations",
                    "proactive_notes",
                    "TEXT NOT NULL DEFAULT ''",
                )
                self._ensure_column(
                    conn,
                    "whatsapp_conversations",
                    "last_harness_route",
                    "TEXT NOT NULL DEFAULT ''",
                )
                self._ensure_column(
                    conn,
                    "whatsapp_conversations",
                    "last_harness_intent",
                    "TEXT NOT NULL DEFAULT ''",
                )
                self._ensure_column(
                    conn,
                    "whatsapp_conversations",
                    "last_harness_action",
                    "TEXT NOT NULL DEFAULT ''",
                )
                self._ensure_column(
                    conn,
                    "whatsapp_conversations",
                    "last_harness_allow_llm",
                    "INTEGER NOT NULL DEFAULT 0",
                )
                self._ensure_column(
                    conn,
                    "whatsapp_conversations",
                    "last_harness_llm_purpose",
                    "TEXT NOT NULL DEFAULT ''",
                )
                self._ensure_column(
                    conn,
                    "whatsapp_conversations",
                    "last_harness_at",
                    "TEXT NOT NULL DEFAULT ''",
                )
                self._ensure_column(
                    conn,
                    "whatsapp_proactive_drafts",
                    "original_text",
                    "TEXT NOT NULL DEFAULT ''",
                )
                self._ensure_column(
                    conn,
                    "whatsapp_proactive_drafts",
                    "sent_text",
                    "TEXT NOT NULL DEFAULT ''",
                )
                self._ensure_column(
                    conn,
                    "whatsapp_qa_feedback",
                    "anonymized_json",
                    "TEXT NOT NULL DEFAULT '{}'",
                )
                conn.commit()

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        existing = {
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

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
                        c.consent_status,
                        c.tags_json,
                        c.notes,
                        c.proactive_notes,
                        c.last_harness_route,
                        c.last_harness_intent,
                        c.last_harness_action,
                        c.last_harness_allow_llm,
                        c.last_harness_llm_purpose,
                        c.last_harness_at,
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

    def count_agent_flags(self, phone: str = "", unresolved_only: bool = True) -> int:
        clauses: List[str] = []
        values: List[Any] = []
        if unresolved_only:
            clauses.append("resolved_at = ''")
        if phone:
            clauses.append("phone = ?")
            values.append(phone)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                row = conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM whatsapp_agent_flags
                    {where}
                    """,
                    values,
                ).fetchone()
        return int(row[0]) if row else 0

    def count_proactive_drafts(self, phone: str = "", status: str = "draft") -> int:
        clauses: List[str] = []
        values: List[Any] = []
        clean_status = str(status or "draft").strip().lower()
        if clean_status and clean_status != "all":
            clauses.append("status = ?")
            values.append(self._clean_draft_status(clean_status))
        if phone:
            clauses.append("phone = ?")
            values.append(phone)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                row = conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM whatsapp_proactive_drafts
                    {where}
                    """,
                    values,
                ).fetchone()
        return int(row[0]) if row else 0

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
                           consent_status, proactive_notes, last_message_at,
                           last_harness_route, last_harness_intent,
                           last_harness_action, last_harness_allow_llm,
                           last_harness_llm_purpose, last_harness_at,
                           created_at, updated_at
                    FROM whatsapp_conversations
                    WHERE phone = ?
                    """,
                    (phone,),
                ).fetchone()
        return self._conversation_row_to_dict(row) if row else {}

    def record_harness_trace(
        self,
        phone: str,
        *,
        route: str,
        intent: str = "",
        recommended_action: str = "",
        allow_llm: bool = False,
        llm_purpose: str = "",
    ) -> None:
        if not phone:
            return

        now = datetime.now().isoformat()
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                self._upsert_conversation(conn, phone, now)
                conn.execute(
                    """
                    UPDATE whatsapp_conversations
                    SET last_harness_route = ?,
                        last_harness_intent = ?,
                        last_harness_action = ?,
                        last_harness_allow_llm = ?,
                        last_harness_llm_purpose = ?,
                        last_harness_at = ?,
                        updated_at = ?
                    WHERE phone = ?
                    """,
                    (
                        str(route or ""),
                        str(intent or ""),
                        str(recommended_action or ""),
                        1 if allow_llm else 0,
                        str(llm_purpose or ""),
                        now,
                        now,
                        phone,
                    ),
                )
                conn.commit()

    def update_conversation(
        self,
        phone: str,
        display_name: str | None = None,
        tags: List[str] | None = None,
        notes: str | None = None,
        consent_status: str | None = None,
        proactive_notes: str | None = None,
    ) -> Dict[str, Any]:
        if not phone:
            return {}

        now = datetime.now().isoformat()
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                conn.row_factory = sqlite3.Row
                self._upsert_conversation(conn, phone, now)
                updates = ["updated_at = ?"]
                values: List[Any] = [now]
                if display_name is not None:
                    updates.append("display_name = ?")
                    values.append(display_name.strip())
                if tags is not None:
                    clean_tags = self._clean_tags(tags)
                    updates.append("tags_json = ?")
                    values.append(json.dumps(clean_tags, ensure_ascii=False))
                if notes is not None:
                    updates.append("notes = ?")
                    values.append(notes.strip())
                if consent_status is not None:
                    updates.append("consent_status = ?")
                    values.append(self._clean_consent_status(consent_status))
                if proactive_notes is not None:
                    updates.append("proactive_notes = ?")
                    values.append(proactive_notes.strip())
                values.append(phone)
                conn.execute(
                    f"""
                    UPDATE whatsapp_conversations
                    SET {', '.join(updates)}
                    WHERE phone = ?
                    """,
                    values,
                )
                conn.commit()
                row = conn.execute(
                    """
                    SELECT phone, display_name, status, tags_json, notes,
                           consent_status, proactive_notes, last_message_at,
                           created_at, updated_at
                    FROM whatsapp_conversations
                    WHERE phone = ?
                    """,
                    (phone,),
                ).fetchone()
        return self._conversation_row_to_dict(row) if row else {}

    def add_conversation_tags(self, phone: str, tags: List[str]) -> Dict[str, Any]:
        current = self.get_conversation(phone)
        existing = current.get("tags", []) if current else []
        return self.update_conversation(phone, tags=[*existing, *tags])

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
                           consent_status, proactive_notes, last_message_at,
                           created_at, updated_at
                    FROM whatsapp_conversations
                    WHERE phone = ?
                    """,
                    (phone,),
                ).fetchone()
        return self._conversation_row_to_dict(row) if row else {}

    def is_human_takeover(self, phone: str) -> bool:
        return self.get_conversation(phone).get("status") == "human"

    def add_agent_flag(
        self,
        phone: str,
        flag_type: str,
        summary: str,
        meta: Dict[str, Any] | None = None,
    ) -> int:
        if flag_type not in {"no_match", "uncertain", "handoff_needed", "error"}:
            raise ValueError(f"Unsupported flag type: {flag_type}")
        if not phone or not summary:
            return 0

        now = datetime.now().isoformat()
        meta_payload = json.dumps(meta or {}, ensure_ascii=False)
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                self._upsert_conversation(conn, phone, now)
                cursor = conn.execute(
                    """
                    INSERT INTO whatsapp_agent_flags (
                        phone, flag_type, summary, meta_json, resolved_at, created_at
                    )
                    VALUES (?, ?, ?, ?, '', ?)
                    """,
                    (phone, flag_type, summary.strip(), meta_payload, now),
                )
                conn.commit()
                return int(cursor.lastrowid or 0)

    def list_agent_flags(
        self,
        unresolved_only: bool = True,
        limit: int = 100,
        phone: str = "",
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 100), 300))
        clauses: List[str] = []
        values: List[Any] = []
        if unresolved_only:
            clauses.append("resolved_at = ''")
        if phone:
            clauses.append("phone = ?")
            values.append(phone)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    f"""
                    SELECT id, phone, flag_type, summary, meta_json, resolved_at, created_at
                    FROM whatsapp_agent_flags
                    {where}
                    ORDER BY created_at DESC, id DESC
                    LIMIT ?
                    """,
                    values,
                ).fetchall()
        return [self._flag_row_to_dict(row) for row in rows]

    def resolve_agent_flag(self, flag_id: int) -> bool:
        now = datetime.now().isoformat()
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                cursor = conn.execute(
                    """
                    UPDATE whatsapp_agent_flags
                    SET resolved_at = ?
                    WHERE id = ? AND resolved_at = ''
                    """,
                    (now, flag_id),
                )
                conn.commit()
                return cursor.rowcount > 0

    def add_qa_feedback(
        self,
        phone: str,
        message_id: int = 0,
        rating: str = "bad",
        issue_type: str = "other",
        summary: str = "",
        expected_behavior: str = "",
        anonymized_sample: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Persist an operator QA mark plus a privacy-scrubbed learning sample."""
        if not phone:
            return {}

        now = datetime.now().isoformat()
        clean_rating = self._clean_qa_rating(rating)
        clean_issue_type = self._clean_qa_issue_type(issue_type)
        sample_json = json.dumps(anonymized_sample or {}, ensure_ascii=False)
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                self._upsert_conversation(conn, phone, now)
                cursor = conn.execute(
                    """
                    INSERT INTO whatsapp_qa_feedback (
                        phone, message_id, rating, issue_type, summary,
                        expected_behavior, anonymized_json, status, created_at, updated_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
                    """,
                    (
                        phone,
                        max(0, int(message_id or 0)),
                        clean_rating,
                        clean_issue_type,
                        str(summary or "").strip()[:500],
                        str(expected_behavior or "").strip()[:800],
                        sample_json,
                        now,
                        now,
                    ),
                )
                conn.commit()
                feedback_id = int(cursor.lastrowid or 0)
        return self.get_qa_feedback(feedback_id)

    def get_qa_feedback(self, feedback_id: int) -> Dict[str, Any]:
        if not feedback_id:
            return {}
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT *
                    FROM whatsapp_qa_feedback
                    WHERE id = ?
                    """,
                    (feedback_id,),
                ).fetchone()
        return self._qa_feedback_row_to_dict(row) if row else {}

    def list_qa_feedback(
        self,
        status: str = "open",
        phone: str = "",
        issue_type: str = "",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 100), 300))
        clauses: List[str] = []
        values: List[Any] = []
        clean_status = self._clean_qa_status(status) if status else ""
        if clean_status and clean_status != "all":
            clauses.append("status = ?")
            values.append(clean_status)
        if phone:
            clauses.append("phone = ?")
            values.append(phone)
        clean_issue = self._clean_qa_issue_type(issue_type) if issue_type else ""
        if clean_issue:
            clauses.append("issue_type = ?")
            values.append(clean_issue)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    f"""
                    SELECT *
                    FROM whatsapp_qa_feedback
                    {where}
                    ORDER BY updated_at DESC, id DESC
                    LIMIT ?
                    """,
                    values,
                ).fetchall()
        return [self._qa_feedback_row_to_dict(row) for row in rows]

    def mark_qa_feedback(self, feedback_id: int, status: str = "closed") -> Dict[str, Any]:
        clean_status = self._clean_qa_status(status)
        if not feedback_id:
            return {}
        now = datetime.now().isoformat()
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                cursor = conn.execute(
                    """
                    UPDATE whatsapp_qa_feedback
                    SET status = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (clean_status, now, feedback_id),
                )
                conn.commit()
                if cursor.rowcount <= 0:
                    return {}
        return self.get_qa_feedback(feedback_id)

    def count_qa_feedback(self, status: str = "open", phone: str = "") -> int:
        clauses: List[str] = []
        values: List[Any] = []
        clean_status = self._clean_qa_status(status) if status else ""
        if clean_status and clean_status != "all":
            clauses.append("status = ?")
            values.append(clean_status)
        if phone:
            clauses.append("phone = ?")
            values.append(phone)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                row = conn.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM whatsapp_qa_feedback
                    {where}
                    """,
                    values,
                ).fetchone()
        return int(row[0]) if row else 0

    def save_proactive_draft(
        self,
        phone: str,
        draft_text: str,
        matches: List[Dict[str, Any]] | None = None,
        profile: Dict[str, Any] | None = None,
        meta: Dict[str, Any] | None = None,
    ) -> Dict[str, Any]:
        """Create a queued proactive draft, reusing the same open draft if present."""
        if not phone or not draft_text.strip():
            return {}

        matches_payload = matches or []
        profile_payload = profile or {}
        meta_payload = meta or {}
        fingerprint = self._proactive_draft_fingerprint(phone, matches_payload)
        now = datetime.now().isoformat()
        matches_json = json.dumps(matches_payload, ensure_ascii=False)
        profile_json = json.dumps(profile_payload, ensure_ascii=False)
        meta_json = json.dumps(meta_payload, ensure_ascii=False)

        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                conn.row_factory = sqlite3.Row
                self._upsert_conversation(conn, phone, now)
                existing = conn.execute(
                    """
                    SELECT *
                    FROM whatsapp_proactive_drafts
                    WHERE phone = ? AND fingerprint = ? AND status = 'draft'
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (phone, fingerprint),
                ).fetchone()
                if existing:
                    conn.execute(
                        """
                        UPDATE whatsapp_proactive_drafts
                        SET matches_json = ?, profile_json = ?, meta_json = ?,
                            updated_at = ?
                        WHERE id = ?
                        """,
                        (matches_json, profile_json, meta_json, now, existing["id"]),
                    )
                    conn.commit()
                    return self.get_proactive_draft(int(existing["id"]))

                cursor = conn.execute(
                    """
                    INSERT INTO whatsapp_proactive_drafts (
                        phone, status, draft_text, original_text, sent_text,
                        matches_json, profile_json, meta_json, fingerprint,
                        error_text, sent_message_type,
                        sent_at, skipped_at, failed_at, created_at, updated_at
                    )
                    VALUES (?, 'draft', ?, ?, '', ?, ?, ?, ?, '', '', '', '', '', ?, ?)
                    """,
                    (
                        phone,
                        draft_text.strip(),
                        draft_text.strip(),
                        matches_json,
                        profile_json,
                        meta_json,
                        fingerprint,
                        now,
                        now,
                    ),
                )
                conn.commit()
                return self.get_proactive_draft(int(cursor.lastrowid or 0))

    def list_proactive_drafts(
        self,
        status: str = "draft",
        phone: str = "",
        search: str = "",
        consent_status: str = "",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 100), 300))
        clauses: List[str] = []
        values: List[Any] = []
        clean_status = str(status or "draft").strip().lower()
        if clean_status and clean_status != "all":
            clauses.append("d.status = ?")
            values.append(self._clean_draft_status(clean_status))
        if phone:
            clauses.append("d.phone = ?")
            values.append(phone)
        clean_consent = self._clean_consent_status(consent_status) if consent_status else ""
        if clean_consent:
            clauses.append("COALESCE(c.consent_status, 'unknown') = ?")
            values.append(clean_consent)
        query = str(search or "").strip()
        if query:
            like = f"%{query}%"
            clauses.append(
                """
                (
                    d.phone LIKE ?
                    OR d.draft_text LIKE ?
                    OR d.original_text LIKE ?
                    OR d.sent_text LIKE ?
                    OR d.matches_json LIKE ?
                    OR d.profile_json LIKE ?
                    OR COALESCE(c.tags_json, '') LIKE ?
                    OR COALESCE(c.notes, '') LIKE ?
                    OR COALESCE(c.proactive_notes, '') LIKE ?
                )
                """
            )
            values.extend([like] * 9)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        values.append(limit)
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    f"""
                    SELECT
                        d.*,
                        c.consent_status,
                        c.status AS conversation_status,
                        c.tags_json,
                        c.notes,
                        c.proactive_notes,
                        c.last_message_at
                    FROM whatsapp_proactive_drafts d
                    LEFT JOIN whatsapp_conversations c ON c.phone = d.phone
                    {where}
                    ORDER BY d.updated_at DESC, d.id DESC
                    LIMIT ?
                    """,
                    values,
                ).fetchall()
        return [self._draft_row_to_dict(row) for row in rows]

    def prune_private_history(self, older_than_days: int = 90, dry_run: bool = True) -> Dict[str, Any]:
        """Count or delete old operational records while preserving parent profiles."""
        days = max(1, min(int(older_than_days or 90), 3650))
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        targets = {
            "messages": (
                "whatsapp_messages",
                "created_at < ?",
                [cutoff],
            ),
            "llm_cache": (
                "llm_response_cache",
                "created_at < ?",
                [cutoff],
            ),
            "processed_message_ids": (
                "processed_whatsapp_messages",
                "created_at < ?",
                [cutoff],
            ),
            "resolved_flags": (
                "whatsapp_agent_flags",
                "resolved_at != '' AND created_at < ?",
                [cutoff],
            ),
            "closed_proactive_drafts": (
                "whatsapp_proactive_drafts",
                "status IN ('sent', 'skipped', 'failed') AND updated_at < ?",
                [cutoff],
            ),
            "closed_qa_feedback": (
                "whatsapp_qa_feedback",
                "status IN ('closed', 'converted') AND updated_at < ?",
                [cutoff],
            ),
        }
        counts: Dict[str, int] = {}
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                for label, (table, where, values) in targets.items():
                    row = conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {where}",
                        values,
                    ).fetchone()
                    counts[label] = int(row[0]) if row else 0
                if not dry_run:
                    for table, where, values in targets.values():
                        conn.execute(f"DELETE FROM {table} WHERE {where}", values)
                    conn.commit()
        return {
            "dry_run": bool(dry_run),
            "older_than_days": days,
            "cutoff": cutoff,
            "counts": counts,
            "total": sum(counts.values()),
        }

    def get_proactive_draft(self, draft_id: int) -> Dict[str, Any]:
        if not draft_id:
            return {}
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    """
                    SELECT
                        d.*,
                        c.consent_status,
                        c.status AS conversation_status,
                        c.tags_json,
                        c.notes,
                        c.proactive_notes,
                        c.last_message_at
                    FROM whatsapp_proactive_drafts d
                    LEFT JOIN whatsapp_conversations c ON c.phone = d.phone
                    WHERE d.id = ?
                    """,
                    (draft_id,),
                ).fetchone()
        return self._draft_row_to_dict(row) if row else {}

    def update_proactive_draft_body(
        self,
        draft_id: int,
        draft_text: str,
    ) -> Dict[str, Any]:
        if not draft_id or not draft_text.strip():
            return {}
        now = datetime.now().isoformat()
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                cursor = conn.execute(
                    """
                    UPDATE whatsapp_proactive_drafts
                    SET draft_text = ?, updated_at = ?
                    WHERE id = ? AND status = 'draft'
                    """,
                    (draft_text.strip(), now, draft_id),
                )
                conn.commit()
                if cursor.rowcount <= 0:
                    return {}
        return self.get_proactive_draft(draft_id)

    def claim_proactive_draft_for_send(
        self,
        draft_id: int,
        draft_text: str = "",
    ) -> Dict[str, Any]:
        """Atomically move a draft into sending state before hitting WhatsApp."""
        if not draft_id:
            return {}
        now = datetime.now().isoformat()
        updates = ["status = 'sending'", "updated_at = ?"]
        values: List[Any] = [now]
        if draft_text.strip():
            updates.append("draft_text = ?")
            values.append(draft_text.strip())
        values.append(draft_id)
        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                cursor = conn.execute(
                    f"""
                    UPDATE whatsapp_proactive_drafts
                    SET {', '.join(updates)}
                    WHERE id = ? AND status = 'draft'
                    """,
                    values,
                )
                conn.commit()
                if cursor.rowcount <= 0:
                    return {}
        return self.get_proactive_draft(draft_id)

    def mark_proactive_draft(
        self,
        draft_id: int,
        status: str,
        error_text: str = "",
        sent_message_type: str = "",
        sent_text: str = "",
        only_status: str = "",
    ) -> Dict[str, Any]:
        clean_status = self._clean_draft_status(status)
        clean_only_status = self._clean_draft_status(only_status) if only_status else ""
        now = datetime.now().isoformat()
        timestamp_column = {
            "sent": "sent_at",
            "skipped": "skipped_at",
            "failed": "failed_at",
        }.get(clean_status, "")

        updates = ["status = ?", "updated_at = ?", "error_text = ?", "sent_message_type = ?"]
        values: List[Any] = [
            clean_status,
            now,
            str(error_text or "")[:500],
            str(sent_message_type or "")[:32],
        ]
        if sent_text:
            updates.append("sent_text = ?")
            values.append(sent_text.strip())
        if timestamp_column:
            updates.append(f"{timestamp_column} = ?")
            values.append(now)
        values.append(draft_id)
        where = "WHERE id = ?"
        if clean_only_status:
            where += " AND status = ?"
            values.append(clean_only_status)

        with self._lock:
            with closing(sqlite3.connect(str(self.db_path))) as conn:
                cursor = conn.execute(
                    f"""
                    UPDATE whatsapp_proactive_drafts
                    SET {', '.join(updates)}
                    {where}
                    """,
                    values,
                )
                conn.commit()
                if cursor.rowcount <= 0:
                    return {}
        return self.get_proactive_draft(draft_id)

    def iter_parent_profiles(self, limit: int = 200) -> List[Dict[str, Any]]:
        limit = max(1, min(int(limit or 200), 1000))
        conversations = self.list_conversations(limit=limit)
        parents = []
        for conversation in conversations:
            phone = conversation.get("phone", "")
            profile = self.get_profile(phone)
            if profile:
                parents.append({
                    "phone": phone,
                    "conversation": conversation,
                    "profile": profile,
                })
        return parents

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

    @staticmethod
    def _clean_tags(tags: List[str]) -> List[str]:
        clean: List[str] = []
        for tag in tags:
            tag_text = str(tag or "").strip()
            if not tag_text or tag_text in clean:
                continue
            clean.append(tag_text[:32])
        return clean[:20]

    @staticmethod
    def _clean_consent_status(status: str) -> str:
        status_text = str(status or "unknown").strip().lower()
        if status_text in {"allowed", "paused", "unknown"}:
            return status_text
        return "unknown"

    @staticmethod
    def _clean_draft_status(status: str) -> str:
        status_text = str(status or "draft").strip().lower()
        if status_text in {"draft", "sending", "sent", "skipped", "failed"}:
            return status_text
        return "draft"

    @staticmethod
    def _clean_qa_rating(rating: str) -> str:
        rating_text = str(rating or "bad").strip().lower()
        if rating_text in {"good", "bad", "neutral"}:
            return rating_text
        return "bad"

    @staticmethod
    def _clean_qa_issue_type(issue_type: str) -> str:
        issue_text = str(issue_type or "other").strip().lower()
        allowed = {
            "good_reply",
            "classification_error",
            "missed_course",
            "off_topic_should_block",
            "unclear_reply",
            "handoff_needed",
            "link_error",
            "onboarding_gap",
            "other",
        }
        return issue_text if issue_text in allowed else "other"

    @staticmethod
    def _clean_qa_status(status: str) -> str:
        status_text = str(status or "open").strip().lower()
        if status_text in {"open", "closed", "converted", "all"}:
            return status_text
        return "open"

    @staticmethod
    def _proactive_draft_fingerprint(
        phone: str,
        matches: List[Dict[str, Any]],
    ) -> str:
        course_keys = []
        for match in matches[:5]:
            course = match.get("course", {}) if isinstance(match, dict) else {}
            key = (
                course.get("id")
                or course.get("reply_url")
                or course.get("registration_url")
                or course.get("detail_url")
                or f"{course.get('name', '')}|{course.get('date', '')}"
            )
            if key:
                course_keys.append(str(key))
        payload = json.dumps(
            {"phone": phone, "courses": course_keys},
            ensure_ascii=False,
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def _upsert_conversation(self, conn: sqlite3.Connection, phone: str, now: str) -> None:
        conn.execute(
            """
            INSERT INTO whatsapp_conversations (
                phone, display_name, status, consent_status, tags_json, notes,
                proactive_notes, last_message_at, created_at, updated_at
            )
            VALUES (?, '', 'ai', 'unknown', '[]', '', '', '', ?, ?)
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

    def _flag_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        result["meta"] = self._safe_json(result.pop("meta_json", "{}"), {})
        return result

    def _draft_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        result["matches"] = self._safe_json(result.pop("matches_json", "[]"), [])
        result["profile"] = self._safe_json(result.pop("profile_json", "{}"), {})
        result["meta"] = self._safe_json(result.pop("meta_json", "{}"), {})
        tags_json = result.pop("tags_json", "[]",)
        result["conversation"] = {
            "phone": result.get("phone", ""),
            "status": result.pop("conversation_status", "") or "ai",
            "consent_status": result.pop("consent_status", "") or "unknown",
            "tags": self._safe_json(tags_json, []),
            "notes": result.pop("notes", "") or "",
            "proactive_notes": result.pop("proactive_notes", "") or "",
            "last_message_at": result.pop("last_message_at", "") or "",
        }
        return result

    def _qa_feedback_row_to_dict(self, row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        result["anonymized_sample"] = self._safe_json(
            result.pop("anonymized_json", "{}"),
            {},
        )
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
