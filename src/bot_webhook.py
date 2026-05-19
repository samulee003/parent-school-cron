"""Zeabur 適配版 — 企業微信群機器人 Webhook 模式

不需要 Wechaty 長連接，純 HTTP 方案：
1. Cron 定時抓取課程 → Webhook 推送到群
2. 企業微信群機器人回調 → HTTP API 接收消息
3. 家長在群裡 @機器人 即可修改設定
"""

import asyncio
import logging
import os
import sys
import time
import traceback
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

# 路徑設置
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

logger = logging.getLogger("bot_webhook")

import requests
import sqlite3
import json
from dataclasses import dataclass, asdict
from scraper import CourseScraper, AGE_GROUP_LABELS
from classifier import CourseClassifier
from user_store import UserStore, UserProfile


# ============== 企業微信 Webhook 工具 ==============

class WeComWebhook:
    """企業微信機器人 Webhook"""

    def __init__(self, webhook_url: str):
        self.webhook_url = webhook_url
        self.session = requests.Session()
        self.session.headers["Content-Type"] = "application/json"

    def send_markdown(self, content: str) -> bool:
        """發送 Markdown 消息"""
        if len(content) > 4096:
            content = content[:4080] + "\n\n...(內容已截斷)"

        data = {"msgtype": "markdown", "markdown": {"content": content}}
        return self._send(data)

    def send_text(self, content: str) -> bool:
        """發送純文本（支持 @提醒）"""
        data = {"msgtype": "text", "text": {"content": content, "mentioned_list": ["@all"]}}
        return self._send(data)

    def send_image(self, base64_data: str, md5: str) -> bool:
        """發送圖片"""
        data = {"msgtype": "image", "image": {"base64": base64_data, "md5": md5}}
        return self._send(data)

    def _send(self, data: dict) -> bool:
        """發送請求"""
        for attempt in range(3):
            try:
                resp = self.session.post(
                    self.webhook_url,
                    json=data,
                    timeout=30,
                )
                result = resp.json()
                if result.get("errcode") == 0:
                    return True
                logger.warning(f"Webhook 發送失敗: {result}")
                if result.get("errcode") == 45009:  # 頻率限制
                    time.sleep(5)
            except Exception as e:
                logger.error(f"Webhook 發送異常: {e}")
                time.sleep(2 ** attempt)
        return False


# ============== 核心 Bot 邏輯 ==============

class ZeaburBot:
    """Zeabur 適配版機器人"""

    def __init__(self):
        self.webhook_url = os.environ.get("WECOM_WEBHOOK_URL", "")
        self.data_dir = os.environ.get("WXAGENT_DATA_DIR", "./data")
        self.push_day = os.environ.get("WXAGENT_PUSH_DAY", "mon")
        self.push_hour = int(os.environ.get("WXAGENT_PUSH_HOUR", "9"))
        self.push_minute = int(os.environ.get("WXAGENT_PUSH_MINUTE", "0"))
        self.api_base = os.environ.get("API_BASE_URL", "https://portal.dsedj.gov.mo")

        Path(self.data_dir).mkdir(parents=True, exist_ok=True)

        self.webhook = WeComWebhook(self.webhook_url) if self.webhook_url else None
        self.store = UserStore(f"{self.data_dir}/users.db")
        self.scraper = CourseScraper()
        self.classifier = CourseClassifier()

        logger.info(f"✅ ZeaburBot 初始化完成")
        logger.info(f"   Webhook: {'已配置' if self.webhook_url else '未配置'}")
        logger.info(f"   推送時間: {self.push_day} {self.push_hour:02d}:{self.push_minute:02d}")

    # ============== 推送功能 ==============

    def run_push(self) -> dict:
        """執行一次推送（Cron 調用）"""
        logger.info("=" * 60)
        logger.info("開始執行課程推送")
        logger.info("=" * 60)

        result = {"success": False, "courses": 0, "users": 0, "error": ""}

        try:
            # 1. 抓取課程
            courses = self.scraper.fetch_all_open_courses(max_retries=3, delay=1.0)
            result["courses"] = len(courses)
            logger.info(f"📚 抓取到 {len(courses)} 條課程")

            if not courses:
                self._send_empty_notice()
                result["success"] = True
                return result

            # 2. 分類
            by_age = self.classifier.by_age_group(courses)

            # 3. 篩選本週/未來7天
            weekly = self._get_weekly_courses(by_age)
            if not weekly:
                logger.info("暫無即將到來的課程")
                self._send_empty_notice()
                result["success"] = True
                return result

            # 4. 推送給所有活躍用戶
            users = self.store.get_active_users()
            configured = [u for u in users if u.child_age_groups]

            if not configured:
                # 沒有設定用戶，推送全部到群
                self._broadcast_to_group(weekly)
            else:
                # 按用戶偏好推送
                self._push_by_subscription(weekly, configured)

            result["users"] = len(configured) if configured else 0
            result["success"] = True

            logger.info(f"✅ 推送完成: {result['courses']} 條課程")

        except Exception as e:
            result["error"] = str(e)
            logger.exception(f"❌ 推送失敗: {e}")

        return result

    def _get_weekly_courses(self, by_age: dict) -> dict:
        """獲取本週/未來7天課程"""
        weekly = {}
        for age, courses in by_age.items():
            this_week = self.classifier.filter_by_week(courses, week_offset=0)
            if this_week:
                weekly[age] = this_week

        if not weekly:
            for age, courses in by_age.items():
                upcoming = self.classifier.filter_upcoming(courses, days=7)
                if upcoming:
                    weekly[age] = upcoming

        return weekly

    def _broadcast_to_group(self, weekly: dict):
        """推送全部課程到群"""
        if not self.webhook:
            logger.warning("未配置 Webhook")
            return

        all_courses = []
        for age, courses in weekly.items():
            all_courses.extend(courses)

        if not all_courses:
            return

        msg = self._format_multi_age_message(all_courses, weekly)
        self.webhook.send_markdown(msg)
        logger.info(f"📢 群廣播: {len(all_courses)} 條課程")

    def _push_by_subscription(self, weekly: dict, users: list):
        """按用戶訂閱推送"""
        if not self.webhook:
            return

        # 企業微信群機器人無法私信，統一發群消息
        # 但按訂閱分類顯示
        msg = self._format_subscription_message(weekly, users)
        self.webhook.send_markdown(msg)

    def _format_multi_age_message(self, courses: list, by_age: dict) -> str:
        """格式化多年龄層推送消息"""
        lines = [
            "📚 **家長學堂 — 本週精選課程**",
            "",
            f"為您找到 **{len(courses)}** 個活動：",
            "",
        ]

        for age in ["0-2歲", "3-6歲", "7-12歲", "13-18歲"]:
            if age not in by_age:
                continue
            age_courses = by_age[age]
            label = AGE_GROUP_LABELS.get(age, age)
            lines.append(f"## {label}（{age}）— {len(age_courses)} 個活動")
            lines.append("")

            for c in age_courses[:5]:
                lines.append(f"**• {c.name}**")
                if c.date and c.date != "詳見活動內容":
                    lines.append(f"  📅 {c.date}")
                tags = [t for t in [c.topic, c.target] if t]
                if tags:
                    lines.append(f"  🏷️ {' | '.join(tags)}")
                lines.append(f"  🟢 {c.status}")
                lines.append("")

            lines.append("---")
            lines.append("")

        lines.append("💡 回覆「修改 年齡」可調整訂閱，如「修改 0-2歲,3-6歲")
        lines.append("📖 回覆「幫助」查看說明")

        return "\n".join(lines)

    def _format_subscription_message(self, weekly: dict, users: list) -> str:
        """格式化訂閱推送"""
        lines = [
            "📚 **家長學堂 — 本週課程推送**",
            "",
            f"👥 當前訂閱用戶: {len(users)} 位",
            "",
        ]

        for age in ["0-2歲", "3-6歲", "7-12歲", "13-18歲"]:
            if age not in weekly:
                continue

            age_courses = weekly[age]
            label = AGE_GROUP_LABELS.get(age, age)
            age_users = [u.wx_name for u in users if age in u.child_age_groups]

            lines.append(f"## {label}（{age}）")
            if age_users:
                lines.append(f"👤 訂閱: {', '.join(age_users[:5])}")
            lines.append("")

            for c in age_courses[:3]:
                lines.append(f"**• {c.name}**")
                if c.date and c.date != "詳見活動內容":
                    lines.append(f"  📅 {c.date}")
                lines.append(f"  🟢 {c.status}")
                lines.append("")

            lines.append("---")
            lines.append("")

        lines.append("💡 @機器人 回覆「修改 年齡」調整訂閱")

        return "\n".join(lines)

    def _send_empty_notice(self):
        """無課程通知"""
        if self.webhook:
            self.webhook.send_markdown(
                "📭 **家長學堂**\n\n"
                "本週暫無新的報名中課程。\n\n"
                "課程會定期更新，請留意後續推送。"
            )

    # ============== 命令處理 ==============

    def handle_group_message(self, msg_data: dict) -> dict:
        """
        處理企業微信群機器人回調消息

        Args:
            msg_data: 企業微信回調數據
                {
                    "msgtype": "text",
                    "text": {"content": "修改 0-2歲", "mentioned_list": ["@bot"]}
                }

        Returns:
            {"reply": "回覆消息", "action": "操作類型"}
        """
        msg_type = msg_data.get("msgtype", "")

        if msg_type == "text":
            content = msg_data.get("text", {}).get("content", "").strip()
            return self._handle_text_command(content, msg_data)

        return {"reply": "", "action": "unknown"}

    def _handle_text_command(self, content: str, msg_data: dict) -> dict:
        """處理文字命令"""
        # 提取用戶信息
        from_user = msg_data.get("sender", "未知用戶")
        sender_id = msg_data.get("sender_id", from_user)

        text = content.strip()
        text_lower = text.lower()

        # 幫助
        if text_lower in ("幫助", "help", "?", "？"):
            return {
                "reply": (
                    "📖 **使用說明**\n\n"
                    "🔹 **訂閱課程**\n"
                    "  @機器人 修改 0-2歲\n"
                    "  @機器人 修改 0-2歲,3-6歲\n\n"
                    "🔹 **查看設定**\n"
                    "  @機器人 狀態\n\n"
                    "🔹 **管理訂閱**\n"
                    "  @機器人 停止 — 暫停推送\n"
                    "  @機器人 開始 — 恢復推送\n\n"
                    "📌 年齡選項: 0-2歲、3-6歲、7-12歲、13-18歲"
                ),
                "action": "help",
            }

        # 修改訂閱
        if any(text_lower.startswith(k) for k in ("修改", "change", "set")):
            return self._cmd_modify(sender_id, from_user, text)

        # 狀態
        if text_lower in ("狀態", "status", "info"):
            return self._cmd_status(sender_id)

        # 停止
        if text_lower in ("停止", "stop", "暫停"):
            return self._cmd_stop(sender_id)

        # 開始
        if text_lower in ("開始", "start", "恢復"):
            return self._cmd_start(sender_id)

        # 模糊命令，給提示
        return {
            "reply": (
                f"收到「{text}」\n\n"
                "可用的命令：\n"
                "• **修改 年齡** — 設定訂閱（如「修改 0-2歲,3-6歲」）\n"
                "• **狀態** — 查看當前設定\n"
                "• **停止** — 暫停推送\n"
                "• **幫助** — 查看說明"
            ),
            "action": "hint",
        }

    def _cmd_modify(self, user_id: str, user_name: str, text: str) -> dict:
        """修改訂閱"""
        # 解析年齡
        age_map = {"1": "0-2歲", "2": "3-6歲", "3": "7-12歲", "4": "13-18歲"}
        valid_ages = set(AGE_GROUP_LABELS.keys())
        selected = []

        # 去掉命令前綴
        for prefix in ("修改", "change", "set"):
            if text.lower().startswith(prefix):
                text = text[len(prefix):].strip()
                break

        # 解析
        parts = text.replace("。", ".").split(",")
        for p in parts:
            p = p.strip()
            if not p:
                continue
            # 數字映射
            if p in age_map:
                p = age_map[p]
            # 清理"歲"
            p = p.replace("岁", "歲").replace("岁", "歲")
            # 標準化
            for va in valid_ages:
                if va in p or p in va:
                    if va not in selected:
                        selected.append(va)
                    break

        if not selected:
            return {
                "reply": (
                    "⚠️ 無法識別年齡層，請使用以下格式：\n\n"
                    "• @機器人 修改 0-2歲\n"
                    "• @機器人 修改 1,3（數字對應）\n\n"
                    "年齡選項：\n"
                    "1️⃣ 0-2歲  2️⃣ 3-6歲  3️⃣ 7-12歲  4️⃣ 13-18歲"
                ),
                "action": "modify_error",
            }

        # 保存
        user = self.store.get_or_create(user_id, user_name)
        user.child_age_groups = selected
        user.is_active = True
        self.store.upsert_user(user)

        labels = [f"{AGE_GROUP_LABELS[a]}（{a}）" for a in selected]
        return {
            "reply": (
                f"✅ **設定完成！**\n\n"
                f"👤 用戶: {user_name}\n"
                f"👶 訂閱年齡: {', '.join(labels)}\n\n"
                f"每週一早上 9 點自動推送課程。\n"
                f"如需修改，隨時 @機器人 修改"
            ),
            "action": "modify_success",
        }

    def _cmd_status(self, user_id: str) -> dict:
        """查看狀態"""
        user = self.store.get_user(user_id)
        if not user or not user.child_age_groups:
            return {
                "reply": "❓ 您還沒有設定訂閱。\n\n請 @機器人 修改 年齡層",
                "action": "status_empty",
            }

        labels = [f"{AGE_GROUP_LABELS[a]}（{a}）" for a in user.child_age_groups]
        status = "✅ 已啟用" if user.is_active else "⏸️ 已暫停"

        return {
            "reply": (
                f"📋 **您的設定**\n\n"
                f"👤 用戶: {user.wx_name or user_id}\n"
                f"👶 訂閱年齡: {', '.join(labels)}\n"
                f"📬 推送狀態: {status}\n\n"
                f"回覆「修改」可調整設定"
            ),
            "action": "status",
        }

    def _cmd_stop(self, user_id: str) -> dict:
        """停止推送"""
        user = self.store.get_user(user_id)
        if user:
            user.is_active = False
            self.store.upsert_user(user)

        return {
            "reply": "⏸️ 已暫停接收課程推送。\n\n隨時 @機器人 開始 恢復推送。",
            "action": "stop",
        }

    def _cmd_start(self, user_id: str) -> dict:
        """開始推送"""
        user = self.store.get_user(user_id)
        if user:
            user.is_active = True
            self.store.upsert_user(user)
            labels = [AGE_GROUP_LABELS.get(a, a) for a in user.child_age_groups]
            return {
                "reply": (
                    f"✅ 已恢復推送！\n\n"
                    f"訂閱年齡: {', '.join(labels)}\n"
                    f"每週一早上 9 點自動推送"
                ),
                "action": "start",
            }

        return {
            "reply": "請先設定訂閱年齡：@機器人 修改 0-2歲",
            "action": "start_no_config",
        }
