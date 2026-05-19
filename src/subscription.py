"""訂閱管理模組 - 管理用戶訂閱信息"""

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import List, Dict, Optional

logger = logging.getLogger(__name__)


@dataclass
class Subscriber:
    """訂閱者數據模型"""
    user_id: str                       # 企業微信用戶ID
    name: str = ""                     # 用戶名稱
    child_age_groups: List[str] = field(default_factory=list)  # ["0-2歲", "3-6歲"]
    subscribed_topics: List[str] = field(default_factory=list) # ["家庭關係", ...]
    created_at: str = ""              # ISO 格式時間

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now().isoformat()

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Subscriber":
        return cls(**data)


class SubscriptionManager:
    """訂閱管理器"""

    def __init__(self, data_file: str = "./data/subscribers.json"):
        self.data_file = Path(data_file)
        self.data_file.parent.mkdir(parents=True, exist_ok=True)
        self._subscribers: Dict[str, Subscriber] = {}
        self._lock = RLock()
        self.load_from_file()

    def add_subscriber(self, subscriber: Subscriber) -> bool:
        """
        添加訂閱者

        Args:
            subscriber: 訂閱者對象

        Returns:
            是否成功
        """
        with self._lock:
            if subscriber.user_id in self._subscribers:
                logger.warning(f"訂閱者 {subscriber.user_id} 已存在，將更新")

            self._subscribers[subscriber.user_id] = subscriber
            self.save_to_file()
            logger.info(f"添加訂閱者: {subscriber.user_id} ({subscriber.name})")
            return True

    def remove_subscriber(self, user_id: str) -> bool:
        """
        移除訂閱者

        Args:
            user_id: 用戶ID

        Returns:
            是否成功
        """
        with self._lock:
            if user_id not in self._subscribers:
                logger.warning(f"訂閱者 {user_id} 不存在")
                return False

            subscriber = self._subscribers.pop(user_id)
            self.save_to_file()
            logger.info(f"移除訂閱者: {user_id} ({subscriber.name})")
            return True

    def update_preferences(
        self,
        user_id: str,
        age_groups: Optional[List[str]] = None,
        topics: Optional[List[str]] = None,
    ) -> bool:
        """
        更新訂閱者偏好設置

        Args:
            user_id: 用戶ID
            age_groups: 新的年齡層列表
            topics: 新的主題列表

        Returns:
            是否成功
        """
        with self._lock:
            if user_id not in self._subscribers:
                logger.warning(f"訂閱者 {user_id} 不存在")
                return False

            subscriber = self._subscribers[user_id]

            if age_groups is not None:
                subscriber.child_age_groups = list(set(age_groups))

            if topics is not None:
                subscriber.subscribed_topics = list(set(topics))

            self.save_to_file()
            logger.info(f"更新訂閱者偏好: {user_id}")
            return True

    def get_subscribers_by_age(self, age_group: str) -> List[Subscriber]:
        """
        獲取訂閱指定年齡層的用戶

        Args:
            age_group: 年齡層

        Returns:
            訂閱者列表
        """
        with self._lock:
            return [
                s for s in self._subscribers.values()
                if age_group in s.child_age_groups
            ]

    def get_subscribers_by_topic(self, topic: str) -> List[Subscriber]:
        """
        獲取訂閱指定主題的用戶

        Args:
            topic: 主題

        Returns:
            訂閱者列表
        """
        with self._lock:
            return [
                s for s in self._subscribers.values()
                if topic in s.subscribed_topics
            ]

    def get_all_subscribers(self) -> List[Subscriber]:
        """獲取所有訂閱者"""
        with self._lock:
            return list(self._subscribers.values())

    def get_subscriber(self, user_id: str) -> Optional[Subscriber]:
        """獲取指定訂閱者"""
        with self._lock:
            return self._subscribers.get(user_id)

    def load_from_file(self) -> None:
        """從文件加載訂閱數據"""
        if not self.data_file.exists():
            logger.info(f"訂閱文件不存在，創建空列表: {self.data_file}")
            self.save_to_file()
            return

        try:
            with open(self.data_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            subscribers_list = data if isinstance(data, list) else data.get("subscribers", [])

            with self._lock:
                self._subscribers = {}
                for item in subscribers_list:
                    subscriber = Subscriber.from_dict(item)
                    self._subscribers[subscriber.user_id] = subscriber

            logger.info(f"加載了 {len(self._subscribers)} 個訂閱者")

        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.error(f"訂閱文件損壞: {e}，創建空列表")
            self._subscribers = {}
            self.save_to_file()

    def save_to_file(self) -> None:
        """保存訂閱數據到文件"""
        try:
            with self._lock:
                data = {
                    "version": "1.0",
                    "updated_at": datetime.now().isoformat(),
                    "subscribers": [s.to_dict() for s in self._subscribers.values()],
                }

            # 原子寫入
            temp_file = self.data_file.with_suffix(".tmp")
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            temp_file.replace(self.data_file)
            logger.debug(f"訂閱數據已保存: {self.data_file}")

        except Exception as e:
            logger.error(f"保存訂閱數據失敗: {e}")
            raise

    def get_age_subscriber_map(self) -> Dict[str, List[Subscriber]]:
        """
        獲取年齡層到訂閱者的映射

        Returns:
            {年齡層: [訂閱者列表]}
        """
        age_map: Dict[str, List[Subscriber]] = {}

        with self._lock:
            for subscriber in self._subscribers.values():
                for age in subscriber.child_age_groups:
                    if age not in age_map:
                        age_map[age] = []
                    age_map[age].append(subscriber)

        return age_map

    def get_age_user_id_map(self) -> Dict[str, List[str]]:
        """
        獲取年齡層到用戶ID的映射

        Returns:
            {年齡層: [用戶ID列表]}
        """
        age_map = self.get_age_subscriber_map()
        return {
            age: [s.user_id for s in subs]
            for age, subs in age_map.items()
        }
