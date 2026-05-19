"""用戶數據存儲 — SQLite 持久化"""

import json
import logging
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import List, Dict, Optional

from scraper import AGE_GROUP_LABELS

logger = logging.getLogger(__name__)


@dataclass
class UserProfile:
    """用戶資料"""
    wx_id: str                          # 微信用戶ID
    wx_name: str = ""                   # 微信暱稱
    child_age_groups: List[str] = field(default_factory=list)  # ["0-2歲"]
    subscribed_topics: List[str] = field(default_factory=list)
    chat_state: str = "idle"            # idle/welcome/select_age/confirm/active
    created_at: str = ""               # ISO
    updated_at: str = ""               # ISO
    last_push_at: str = ""             # ISO
    push_count: int = 0                # 推送次數
    is_active: bool = True             # 是否啟用

    def __post_init__(self):
        now = datetime.now().isoformat()
        if not self.created_at:
            self.created_at = now
        if not self.updated_at:
            self.updated_at = now

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "UserProfile":
        # 過濾無效鍵
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in d.items() if k in valid_keys}
        return cls(**filtered)


class UserStore:
    """SQLite 用戶存儲"""

    def __init__(self, db_path: str = "./data/users.db"):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._init_db()

    def _init_db(self):
        """初始化數據庫表"""
        with self._lock, sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    wx_id TEXT PRIMARY KEY,
                    wx_name TEXT DEFAULT '',
                    child_age_groups TEXT DEFAULT '[]',
                    subscribed_topics TEXT DEFAULT '[]',
                    chat_state TEXT DEFAULT 'idle',
                    created_at TEXT,
                    updated_at TEXT,
                    last_push_at TEXT DEFAULT '',
                    push_count INTEGER DEFAULT 0,
                    is_active INTEGER DEFAULT 1
                )
            """)
            conn.commit()
            logger.info(f"用戶數據庫初始化完成: {self.db_path}")

    def _row_to_profile(self, row: sqlite3.Row) -> UserProfile:
        """數據庫行轉 UserProfile"""
        d = dict(row)
        d["child_age_groups"] = json.loads(d.get("child_age_groups", "[]"))
        d["subscribed_topics"] = json.loads(d.get("subscribed_topics", "[]"))
        d["is_active"] = bool(d.get("is_active", 1))
        d["push_count"] = d.get("push_count", 0)
        return UserProfile.from_dict(d)

    def upsert_user(self, profile: UserProfile) -> bool:
        """插入或更新用戶"""
        profile.updated_at = datetime.now().isoformat()
        with self._lock, sqlite3.connect(str(self.db_path)) as conn:
            conn.execute("""
                INSERT INTO users (wx_id, wx_name, child_age_groups, subscribed_topics,
                    chat_state, created_at, updated_at, last_push_at, push_count, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(wx_id) DO UPDATE SET
                    wx_name=excluded.wx_name,
                    child_age_groups=excluded.child_age_groups,
                    subscribed_topics=excluded.subscribed_topics,
                    chat_state=excluded.chat_state,
                    updated_at=excluded.updated_at,
                    last_push_at=excluded.last_push_at,
                    push_count=excluded.push_count,
                    is_active=excluded.is_active
            """, (
                profile.wx_id,
                profile.wx_name,
                json.dumps(profile.child_age_groups, ensure_ascii=False),
                json.dumps(profile.subscribed_topics, ensure_ascii=False),
                profile.chat_state,
                profile.created_at,
                profile.updated_at,
                profile.last_push_at,
                profile.push_count,
                int(profile.is_active),
            ))
            conn.commit()
        logger.info(f"用戶已保存: {profile.wx_id} ({profile.wx_name})")
        return True

    def get_user(self, wx_id: str) -> Optional[UserProfile]:
        """獲取用戶"""
        with self._lock, sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM users WHERE wx_id = ?", (wx_id,)
            ).fetchone()
            if row:
                return self._row_to_profile(row)
        return None

    def get_or_create(self, wx_id: str, wx_name: str = "") -> UserProfile:
        """獲取或創建用戶"""
        user = self.get_user(wx_id)
        if not user:
            user = UserProfile(wx_id=wx_id, wx_name=wx_name, chat_state="welcome")
            self.upsert_user(user)
            logger.info(f"新用戶: {wx_id} ({wx_name})")
        return user

    def update_state(self, wx_id: str, state: str) -> bool:
        """更新用戶對話狀態"""
        with self._lock, sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "UPDATE users SET chat_state = ?, updated_at = ? WHERE wx_id = ?",
                (state, datetime.now().isoformat(), wx_id),
            )
            conn.commit()
        return True

    def set_age_groups(self, wx_id: str, age_groups: List[str]) -> bool:
        """設置用戶年齡層偏好"""
        with self._lock, sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "UPDATE users SET child_age_groups = ?, updated_at = ? WHERE wx_id = ?",
                (json.dumps(age_groups, ensure_ascii=False), datetime.now().isoformat(), wx_id),
            )
            conn.commit()
        return True

    def set_active(self, wx_id: str, active: bool) -> bool:
        """設置用戶啟用狀態"""
        with self._lock, sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "UPDATE users SET is_active = ?, updated_at = ? WHERE wx_id = ?",
                (int(active), datetime.now().isoformat(), wx_id),
            )
            conn.commit()
        return True

    def record_push(self, wx_id: str) -> bool:
        """記錄推送"""
        with self._lock, sqlite3.connect(str(self.db_path)) as conn:
            conn.execute(
                "UPDATE users SET last_push_at = ?, push_count = push_count + 1, updated_at = ? WHERE wx_id = ?",
                (datetime.now().isoformat(), datetime.now().isoformat(), wx_id),
            )
            conn.commit()
        return True

    def get_active_users(self) -> List[UserProfile]:
        """獲取所有活躍用戶"""
        with self._lock, sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM users WHERE is_active = 1"
            ).fetchall()
            return [self._row_to_profile(r) for r in rows]

    def get_users_by_age(self, age_group: str) -> List[UserProfile]:
        """獲取訂閱指定年齡層的活躍用戶"""
        users = self.get_active_users()
        return [u for u in users if age_group in u.child_age_groups]

    def get_stats(self) -> Dict[str, int]:
        """統計"""
        with sqlite3.connect(str(self.db_path)) as conn:
            total = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
            active = conn.execute("SELECT COUNT(*) FROM users WHERE is_active = 1").fetchone()[0]
            configured = conn.execute(
                "SELECT COUNT(*) FROM users WHERE is_active = 1 AND child_age_groups != '[]'"
            ).fetchone()[0]
        return {"total": total, "active": active, "configured": configured}

    def get_user_count(self) -> int:
        """獲取用戶總數"""
        with sqlite3.connect(str(self.db_path)) as conn:
            row = conn.execute("SELECT COUNT(*) FROM users").fetchone()
            return row[0] if row else 0
