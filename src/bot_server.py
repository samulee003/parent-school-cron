"""微信機器人服務器 — Wechaty + FastAPI + 定時推送"""

import asyncio
import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# 將 src 加入路徑
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "src"))

logger = logging.getLogger("bot_server")

# Wechaty 導入
try:
    from wechaty import Wechaty, Contact, Message, Room
    from wechaty_puppet import ScanStatus, EventErrorPayload
    HAS_WECHATY = True
except ImportError:
    HAS_WECHATY = False
    Wechaty = None
    Contact = None
    Message = None
    Room = None
    ScanStatus = None

from config import Config
from scraper import CourseScraper, AGE_GROUP_LABELS
from classifier import CourseClassifier
from user_store import UserStore, UserProfile
from chat_flow import ChatFlow


class ParentAcademyBot:
    """家長學堂微信機器人"""

    def __init__(self, config: Config):
        self.config = config
        self.store = UserStore(config.data_dir + "/users.db")
        self.flow = ChatFlow()
        self.scraper = CourseScraper(config.api_base_url)
        self.classifier = CourseClassifier()

        # Wechaty 實例
        self.bot: Optional[Wechaty] = None
        self.login_user: Optional[Contact] = None

        # 臨時存儲選擇中的年齡（未確認前）
        self._pending_ages: Dict[str, List[str]] = {}

    async def start(self):
        """啟動機器人"""
        if not HAS_WECHATY:
            logger.error("未安裝 Wechaty。請運行: pip install wechaty wechaty-puppet-wechat")
            logger.error("或使用其他 puppet: pip install wechaty-puppet-padlocal")
            sys.exit(1)

        # 獲取 puppet 名稱
        puppet_name = os.environ.get("WECHATY_PUPPET", "wechaty-puppet-wechat")
        token = os.environ.get("WECHATY_PUPPET_PADLOCAL_TOKEN", "")

        options = {"puppet": puppet_name}
        if token:
            options["puppet_options"] = {"token": token}

        self.bot = Wechaty(options)

        # 註冊事件處理
        self.bot.on("scan", self._on_scan)
        self.bot.on("login", self._on_login)
        self.bot.on("logout", self._on_logout)
        self.bot.on("message", self._on_message)
        self.bot.on("error", self._on_error)

        logger.info("啟動微信機器人...")
        logger.info(f"Puppet: {puppet_name}")
        await self.bot.start()

    async def stop(self):
        """停止機器人"""
        if self.bot:
            await self.bot.stop()
        logger.info("機器人已停止")

    # ========== 事件處理 ==========

    async def _on_scan(self, qr_code: str, status: ScanStatus, data: Optional[str] = None):
        """掃碼事件"""
        if status == ScanStatus.Waiting:
            logger.info("請掃描 QR Code 登錄:")
            # 輸出 QR Code URL
            qr_url = f"https://wechaty.js.org/qrcode/{qr_code}"
            logger.info(f"QR Code URL: {qr_url}")
            print(f"\n{'='*60}")
            print(f"請掃碼登錄: {qr_url}")
            print(f"{'='*60}\n")
        elif status == ScanStatus.Scanned:
            logger.info("已掃描，等待確認...")
        elif status == ScanStatus.Confirmed:
            logger.info("登錄確認中...")

    async def _on_login(self, contact: Contact):
        """登錄事件"""
        self.login_user = contact
        logger.info(f"✅ 機器人已登錄: {contact.name} ({contact.contact_id})")
        print(f"\n{'='*60}")
        print(f"✅ 機器人「{contact.name}」已上線！")
        print(f"家長可以掃碼添加好友了")
        print(f"{'='*60}\n")

    async def _on_logout(self, contact: Contact):
        """登出事件"""
        self.login_user = None
        logger.info(f"機器人已登出: {contact.name}")

    async def _on_error(self, payload: EventErrorPayload):
        """錯誤事件"""
        logger.error(f"機器人錯誤: {payload}")

    async def _on_message(self, msg: Message):
        """收到消息"""
        # 忽略自己發的消息
        if msg.is_self():
            return

        # 只處理私聊消息（忽略群消息）
        room = msg.room()
        if room:
            return  # 暫不處理群消息

        contact = msg.talker()
        text = msg.text().strip()
        wx_id = contact.contact_id
        wx_name = contact.name

        logger.info(f"收到消息 [{wx_name}]: {text[:50]}")

        try:
            reply = await self._handle_user_message(wx_id, wx_name, text)
            if reply:
                await msg.say(reply)
        except Exception as e:
            logger.exception(f"處理消息失敗: {e}")
            await msg.say("抱歉，處理出錯了，請稍後再試。")

    # ========== 消息處理核心 ==========

    async def _handle_user_message(
        self, wx_id: str, wx_name: str, text: str
    ) -> Optional[str]:
        """處理用戶消息，返回回覆"""
        # 獲取或創建用戶
        user = self.store.get_or_create(wx_id, wx_name)

        # 更新用戶名（可能變了）
        if wx_name and user.wx_name != wx_name:
            user.wx_name = wx_name
            self.store.upsert_user(user)

        # 檢查是否為新用戶（第一次對話）
        if user.chat_state == "welcome":
            user.chat_state = self.flow.STATE_SELECT_AGE
            self.store.upsert_user(user)
            return self.flow.get_welcome_message()

        # 獲取當前待選年齡
        pending_ages = self._pending_ages.get(wx_id, user.child_age_groups or [])

        # 處理消息
        new_state, reply, selected_ages, should_update = self.flow.handle_message(
            user_id=wx_id,
            message=text,
            current_state=user.chat_state,
            current_ages=pending_ages,
        )

        # 更新待選年齡
        self._pending_ages[wx_id] = selected_ages

        # 更新用戶狀態
        if should_update:
            user.chat_state = new_state
            if selected_ages:
                user.child_age_groups = selected_ages
            self.store.upsert_user(user)

        # 清理已完成的待選
        if new_state == self.flow.STATE_ACTIVE:
            if wx_id in self._pending_ages:
                del self._pending_ages[wx_id]

        return reply

    # ========== 定時推送 ==========

    async def run_scheduled_push(self):
        """執行定時推送任務"""
        logger.info("開始執行定時推送...")
        start_time = datetime.now()

        try:
            # 1. 抓取課程
            all_courses = self.scraper.fetch_all_open_courses(
                max_retries=self.config.request_retry,
                delay=self.config.request_delay,
            )
            logger.info(f"抓取到 {len(all_courses)} 條課程")

            if not all_courses:
                logger.info("無課程數據，跳過推送")
                return

            # 2. 按年齡分類
            courses_by_age = self.classifier.by_age_group(all_courses)

            # 3. 篩選本週課程
            weekly_courses: Dict[str, List] = {}
            for age, courses in courses_by_age.items():
                this_week = self.classifier.filter_by_week(courses, week_offset=0)
                if this_week:
                    weekly_courses[age] = this_week

            # 如果本週沒有，推送未來7天
            if not weekly_courses:
                for age, courses in courses_by_age.items():
                    upcoming = self.classifier.filter_upcoming(courses, days=7)
                    if upcoming:
                        weekly_courses[age] = upcoming

            if not weekly_courses:
                logger.info("暫無即將到來的課程")
                return

            # 4. 按用戶偏好推送
            await self._push_to_users(weekly_courses)

            elapsed = (datetime.now() - start_time).total_seconds()
            logger.info(f"推送完成，耗時 {elapsed:.1f} 秒")

        except Exception as e:
            logger.exception(f"定時推送失敗: {e}")

    async def _push_to_users(self, courses_by_age: Dict[str, List]):
        """推送給所有訂閱用戶"""
        # 獲取所有活躍且已設定的用戶
        users = self.store.get_active_users()
        configured_users = [u for u in users if u.child_age_groups]

        if not configured_users:
            logger.info("沒有已設定的活躍用戶，跳過推送")
            return

        logger.info(f"準備推送給 {len(configured_users)} 位用戶")

        for user in configured_users:
            try:
                # 收集該用戶訂閱的年齡層課程
                user_courses = []
                for age in user.child_age_groups:
                    if age in courses_by_age:
                        user_courses.extend(courses_by_age[age])

                if not user_courses:
                    continue

                # 格式化消息
                message = self._format_push_message(user_courses, user.child_age_groups)

                # 發送消息
                await self._send_to_user(user.wx_id, message)
                self.store.record_push(user.wx_id)

                logger.info(f"已推送給 {user.wx_name} ({len(user_courses)} 條)")

            except Exception as e:
                logger.error(f"推送給 {user.wx_name} 失敗: {e}")

        logger.info(f"推送完成: {len(configured_users)} 位用戶")

    def _format_push_message(
        self, courses: List, user_ages: List[str]
    ) -> str:
        """格式化推送消息"""
        lines = [
            "📚 家長學堂 — 本週精選課程",
            "",
            f"為您精選了 {len(courses)} 個活動：",
            "",
        ]

        for i, c in enumerate(courses[:10], 1):  # 最多10條
            lines.append(f"**{i}. {c.name}**")
            if c.date and c.date != "詳見活動內容":
                lines.append(f"📅 {c.date}")
            tags = []
            if c.topic:
                tags.append(c.topic)
            if c.target:
                tags.append(c.target)
            if c.age_group:
                age_label = AGE_GROUP_LABELS.get(c.age_group, c.age_group)
                tags.insert(0, age_label)
            if tags:
                lines.append(f"🏷️ {' | '.join(tags)}")
            status_emoji = {"報名中": "🟢", "待報名": "🟡", "已完成報名": "🔴"}
            emoji = status_emoji.get(c.status, "⚪")
            lines.append(f"{emoji} {c.status}")
            if c.detail_url:
                lines.append(f"[查看詳情]({c.detail_url})")
            lines.append("")

        if len(courses) > 10:
            lines.append(f"...還有 {len(courses) - 10} 個活動")
            lines.append("")

        lines.append("---")
        lines.append("💡 回覆「修改」可調整年齡設定")
        lines.append("📖 回覆「幫助」查看使用說明")

        return "\n".join(lines)

    async def _send_to_user(self, wx_id: str, message: str):
        """發送消息給指定用戶"""
        if not self.bot or not self.login_user:
            logger.warning("機器人未就緒，無法發送")
            return

        try:
            contact = await self.bot.Contact.find(wx_id)
            if contact:
                await contact.say(message)
            else:
                logger.warning(f"找不到聯繫人: {wx_id}")
        except Exception as e:
            logger.error(f"發送消息失敗 ({wx_id}): {e}")

    # ========== 管理接口 ==========

    def get_stats(self) -> Dict:
        """獲取機器人統計"""
        stats = self.store.get_stats()
        return {
            **stats,
            "bot_online": self.login_user is not None,
            "bot_name": self.login_user.name if self.login_user else None,
        }
