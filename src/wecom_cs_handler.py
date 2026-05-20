"""企業微信客服業務邏輯處理器

處理家長的課程查詢、訂閱管理、歡迎語等交互
"""

import json
import logging
import os
import re
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from scraper import CourseScraper, AGE_GROUP_LABELS
from classifier import CourseClassifier
from user_store import UserStore, UserProfile
from wecom_cs_api import WeComCSAPI

logger = logging.getLogger("wecom_cs_handler")


class CSMessageHandler:
    """企業微信客服消息處理器"""

    def __init__(self):
        self.data_dir = os.environ.get("WXAGENT_DATA_DIR", "./data")
        self.api_base = os.environ.get("API_BASE_URL", "https://portal.dsedj.gov.mo")

        self.store = UserStore(f"{self.data_dir}/cs_users.db")
        self.scraper = CourseScraper()
        self.classifier = CourseClassifier()
        self.api: Optional[WeComCSAPI] = None

        # 嘗試初始化 API
        corp_id = os.environ.get("WECOM_CORP_ID", "")
        secret = os.environ.get("WECOM_CS_SECRET", "")
        if corp_id and secret:
            self.api = WeComCSAPI(corp_id, secret)
            logger.info("WeCom CS API 初始化成功")
        else:
            logger.warning("WECOM_CORP_ID 或 WECOM_CS_SECRET 未設置，API 功能不可用")

    # ============== 事件處理 ==============

    def handle_event(self, event_data: dict) -> Optional[str]:
        """
        處理企業微信客服事件

        Args:
            event_data: 解密後的事件字典

        Returns:
            需要回覆的明文（XML），或 None
        """
        msg_type = event_data.get("MsgType", "")
        event = event_data.get("Event", "")

        logger.info(f"處理事件: MsgType={msg_type}, Event={event}")

        if msg_type == "event" and event == "kf_msg_or_event":
            # 客服消息/事件通知
            token = event_data.get("Token", "")
            open_kfid = event_data.get("OpenKfId", "")

            if token:
                # 有 token，說明有新消息，需要 sync_msg 獲取詳情
                return self._handle_kf_event(token, open_kfid)

        # 進入會話事件（可發送歡迎語）
        if event == "enter_session":
            welcome_code = event_data.get("WelcomeCode", "")
            open_kfid = event_data.get("OpenKfId", "")
            external_userid = event_data.get("ExternalUserId", "")
            if welcome_code and self.api:
                self._send_welcome(welcome_code, open_kfid, external_userid)

        return None

    def _handle_kf_event(self, token: str, open_kfid: str) -> Optional[str]:
        """處理客服消息事件，sync_msg 獲取消息列表並回覆"""
        if not self.api:
            logger.warning("API 未初始化，無法處理消息")
            return None

        try:
            # 同步消息
            result = self.api.sync_msg(token, open_kfid=open_kfid, limit=50)

            if result.get("errcode") != 0:
                logger.warning(f"sync_msg 失敗: {result}")
                return None

            msg_list = result.get("msg_list", [])
            logger.info(f"同步到 {len(msg_list)} 條消息")

            for msg in msg_list:
                self._handle_single_msg(msg)

        except Exception as e:
            logger.exception(f"處理客服事件失敗: {e}")

        return None

    def _handle_single_msg(self, msg: dict):
        """處理單條消息"""
        msg_type = msg.get("msgtype", "")
        origin = msg.get("origin", 0)  # 3=客戶, 4=系統
        external_userid = msg.get("external_userid", "")
        open_kfid = msg.get("open_kfid", "")

        # 只處理客戶發送的消息
        if origin != 3:
            return

        logger.info(f"處理客戶消息: {external_userid}, type={msg_type}")

        if msg_type == "text":
            content = msg.get("text", {}).get("content", "").strip()
            reply = self._handle_text_command(content, external_userid)
            if reply and self.api:
                self.api.send_text_msg(open_kfid, external_userid, reply)

        elif msg_type == "event":
            # 菜單點擊等事件
            event_type = msg.get("event", {}).get("event_type", "")
            if event_type == "menu_click":
                click_content = msg.get("event", {}).get("event_key", "")
                reply = self._handle_text_command(click_content, external_userid)
                if reply and self.api:
                    self.api.send_text_msg(open_kfid, external_userid, reply)

    # ============== 命令處理 ==============

    def _handle_text_command(self, text: str, user_id: str) -> Optional[str]:
        """
        處理文字命令

        Returns:
            回覆文本，或 None
        """
        text_lower = text.lower().strip()

        # 幫助
        if text_lower in ("幫助", "help", "?", "？", "說明"):
            return self._help_text()

        # 課程查詢
        if any(k in text_lower for k in ("課程", "活動", "報名", "查詢", "搜尋", "找")):
            return self._query_courses(text)

        # 年齡篩選查詢
        if any(k in text_lower for k in ("0-2", "3-6", "7-12", "13-18", "嬰兒", "幼兒", "小學", "中學")):
            return self._query_by_age(text)

        # 修改訂閱
        if any(text_lower.startswith(k) for k in ("修改", "change", "set", "設定")):
            return self._cmd_modify(user_id, text)

        # 狀態查詢
        if text_lower in ("狀態", "status", "info", "我的設定"):
            return self._cmd_status(user_id)

        # 停止
        if text_lower in ("停止", "stop", "暫停", "取消"):
            return self._cmd_stop(user_id)

        # 開始
        if text_lower in ("開始", "start", "恢復", "啟用"):
            return self._cmd_start(user_id)

        # 問候語
        if any(k in text_lower for k in ("你好", "您好", "hi", "hello", "喂", "哈囉")):
            return (
                "您好！我是家長學堂課程小助手 👋\n\n"
                "我可以幫您：\n"
                "• 查詢最新課程與活動\n"
                "• 按孩子年齡篩選適合的課程\n"
                "• 設定訂閱偏好\n\n"
                "請直接輸入「課程」查看最新活動，"
                "或「幫助」查看完整說明。"
            )

        # 模糊匹配，給提示
        return (
            f「收到「{text}」\n\n"
            "不太確定您的意思，您可以試試：\n"
            "• **課程** — 查看最新活動\n"
            "• **0-2歲** / **3-6歲** — 按年齡查詢\n"
            "• **幫助** — 查看完整說明"
        )

    def _help_text(self) -> str:
        """幫助文本"""
        return (
            "📖 **使用說明**\n\n"
            "🔹 **查詢課程**\n"
            "  輸入「課程」查看最新活動\n"
            "  輸入「0-2歲」篩選特定年齡\n\n"
            "🔹 **設定訂閱**\n"
            "  修改 0-2歲\n"
            "  修改 0-2歲,3-6歲\n\n"
            "🔹 **查看設定**\n"
            "  狀態\n\n"
            "🔹 **管理訂閱**\n"
            "  停止 — 暫停推送\n"
            "  開始 — 恢復推送\n\n"
            "📌 年齡選項: 0-2歲、3-6歲、7-12歲、13-18歲"
        )

    # ============== 課程查詢 ==============

    def _query_courses(self, text: str) -> str:
        """查詢課程"""
        try:
            courses = self.scraper.fetch_all_open_courses(max_retries=2, delay=1.0)

            if not courses:
                return "📭 暫無報名中的課程，請稍後再試。"

            by_age = self.classifier.by_age_group(courses)
            weekly = {}
            for age, cs in by_age.items():
                this_week = self.classifier.filter_by_week(cs, week_offset=0)
                if this_week:
                    weekly[age] = this_week

            if not weekly:
                # 沒有本週的，顯示未來7天
                for age, cs in by_age.items():
                    upcoming = self.classifier.filter_upcoming(cs, days=7)
                    if upcoming:
                        weekly[age] = upcoming

            if not weekly:
                return "📭 未來7天暫無新課程，請稍後再查詢。"

            return self._format_course_reply(weekly)

        except Exception as e:
            logger.exception(f"查詢課程失敗: {e}")
            return "❌ 查詢失敗，請稍後再試。"

    def _query_by_age(self, text: str) -> str:
        """按年齡查詢"""
        age_map = {
            "0-2": "0-2歲", "嬰兒": "0-2歲", "寶寶": "0-2歲",
            "3-6": "3-6歲", "幼兒": "3-6歲", "幼兒園": "3-6歲",
            "7-12": "7-12歲", "小學": "7-12歲", "小學生": "7-12歲",
            "13-18": "13-18歲", "中學": "13-18歲", "中學生": "13-18歲", "青少年": "13-18歲",
        }

        target_age = None
        for keyword, age in age_map.items():
            if keyword in text:
                target_age = age
                break

        if not target_age:
            return "請指定年齡層，如「0-2歲」、「小學」等。"

        try:
            courses = self.scraper.fetch_all_open_courses(max_retries=2, delay=1.0)
            by_age = self.classifier.by_age_group(courses)
            age_courses = by_age.get(target_age, [])

            if not age_courses:
                return f「📭 暫無 {AGE_GROUP_LABELS.get(target_age, target_age)} 的報名中課程。"

            weekly = self.classifier.filter_by_week(age_courses, week_offset=0)
            if not weekly:
                weekly = self.classifier.filter_upcoming(age_courses, days=14)

            if not weekly:
                return f「📭 未來兩週暫無 {AGE_GROUP_LABELS.get(target_age, target_age)} 的課程。"

            return self._format_single_age_reply(target_age, weekly)

        except Exception as e:
            logger.exception(f"按年齡查詢失敗: {e}")
            return "❌ 查詢失敗，請稍後再試。"

    def _format_course_reply(self, weekly: dict) -> str:
        """格式化多年龄層課程回覆"""
        lines = [
            "📚 **家長學堂 — 最新課程**",
            "",
        ]

        for age in ["0-2歲", "3-6歲", "7-12歲", "13-18歲"]:
            if age not in weekly:
                continue
            age_courses = weekly[age]
            label = AGE_GROUP_LABELS.get(age, age)
            lines.append(f「**{label}** — {len(age_courses)} 個活動")
            lines.append("")

            for c in age_courses[:3]:
                lines.append(f「• {c.name}」)
                if c.date and c.date != "詳見活動內容":
                    lines.append(f「  📅 {c.date}」)
                if c.location:
                    lines.append(f「  📍 {c.location}」)
                lines.append("")

        lines.append("輸入年齡層可查看詳情，如「0-2歲」")
        return "\n".join(lines)

    def _format_single_age_reply(self, age: str, courses: list) -> str:
        """格式化單一年齡層課程回覆"""
        label = AGE_GROUP_LABELS.get(age, age)
        lines = [
            f「📚 **{label} 課程**",
            "",
        ]

        for c in courses[:5]:
            lines.append(f「**{c.name}**」)
            if c.date and c.date != "詳見活動內容":
                lines.append(f「  📅 {c.date}」)
            if c.location:
                lines.append(f「  📍 {c.location}」)
            tags = [t for t in [c.topic, c.target] if t]
            if tags:
                lines.append(f「  🏷️ {' | '.join(tags)}」)
            lines.append(f「  🟢 {c.status}」)
            lines.append("")

        return "\n".join(lines)

    # ============== 訂閱管理命令 ==============

    def _cmd_modify(self, user_id: str, text: str) -> str:
        """修改訂閱"""
        age_map = {"1": "0-2歲", "2": "3-6歲", "3": "7-12歲", "4": "13-18歲"}
        valid_ages = set(AGE_GROUP_LABELS.keys())
        selected = []

        # 去掉命令前綴
        for prefix in ("修改", "change", "set", "設定"):
            if text.lower().startswith(prefix):
                text = text[len(prefix):].strip()
                break

        parts = text.replace("。", ".").split(",")
        for p in parts:
            p = p.strip()
            if not p:
                continue
            if p in age_map:
                p = age_map[p]
            p = p.replace("岁", "歲")
            for va in valid_ages:
                if va in p or p in va:
                    if va not in selected:
                        selected.append(va)
                    break

        if not selected:
            return (
                "⚠️ 無法識別年齡層，請使用以下格式：\n\n"
                "• 修改 0-2歲\n"
                "• 修改 1,3（數字對應）\n\n"
                "年齡選項：\n"
                "1️⃣ 0-2歲  2️⃣ 3-6歲  3️⃣ 7-12歲  4️⃣ 13-18歲"
            )

        user = self.store.get_or_create(user_id, user_id)
        user.child_age_groups = selected
        user.is_active = True
        self.store.upsert_user(user)

        labels = [f「{AGE_GROUP_LABELS[a]}（{a}）" for a in selected]
        return (
            f「✅ 設定完成！\n\n"
            f「👶 訂閱年齡: {', '.join(labels)}\n\n"
            "有新課程時會主動通知您。\n"
            "輸入「課程」可隨時查看最新活動。"
        )

    def _cmd_status(self, user_id: str) -> str:
        """查看狀態"""
        user = self.store.get_user(user_id)
        if not user or not user.child_age_groups:
            return "❓ 您還沒有設定訂閱。\n\n請輸入「修改 年齡層」進行設定"

        labels = [f「{AGE_GROUP_LABELS[a]}（{a}）" for a in user.child_age_groups]
        status = "✅ 已啟用" if user.is_active else "⏸️ 已暫停"

        return (
            f「📋 **您的設定**\n\n"
            f「👶 訂閱年齡: {', '.join(labels)}\n"
            f「📬 推送狀態: {status}\n\n"
            "輸入「修改」可調整設定"
        )

    def _cmd_stop(self, user_id: str) -> str:
        """停止推送"""
        user = self.store.get_user(user_id)
        if user:
            user.is_active = False
            self.store.upsert_user(user)
        return "⏸️ 已暫停接收課程推送。\n\n隨時輸入「開始」恢復推送。"

    def _cmd_start(self, user_id: str) -> str:
        """開始推送"""
        user = self.store.get_user(user_id)
        if user and user.child_age_groups:
            user.is_active = True
            self.store.upsert_user(user)
            labels = [AGE_GROUP_LABELS.get(a, a) for a in user.child_age_groups]
            return (
                f「✅ 已恢復推送！\n\n"
                f「訂閱年齡: {', '.join(labels)}\n"
                "有新課程時會通知您。"
            )
        return "請先設定訂閱年齡：輸入「修改 0-2歲」"

    # ============== 歡迎語 ==============

    def _send_welcome(self, welcome_code: str, open_kfid: str, external_userid: str):
        """發送歡迎語"""
        if not self.api:
            return

        text = (
            "您好！歡迎使用家長學堂課程助手 👋\n\n"
            "我可以幫您查詢澳門家長學堂的最新課程與活動，"
            "並按您孩子的年齡推送適合的內容。\n\n"
            "試試輸入：\n"
            "• **課程** — 查看所有活動\n"
            "• **0-2歲** — 按年齡篩選\n"
            "• **幫助** — 查看完整說明"
        )

        try:
            result = self.api.send_welcome_text(welcome_code, text)
            logger.info(f"歡迎語發送結果: {result}")
        except Exception as e:
            logger.exception(f"發送歡迎語失敗: {e}")

    # ============== 主動推送（受 48h 限制） ==============

    def push_to_user(self, open_kfid: str, external_userid: str, courses: dict) -> bool:
        """
        主動推送課程給用戶（需在 48h 窗口內）

        Returns:
            是否成功
        """
        if not self.api:
            return False

        try:
            msg = self._format_course_reply(courses)
            result = self.api.send_text_msg(open_kfid, external_userid, msg)
            return result.get("errcode") == 0
        except Exception as e:
            logger.exception(f"推送失敗: {e}")
            return False
