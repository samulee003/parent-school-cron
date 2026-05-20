"""WhatsApp Cloud API 處理模組

接收家長透過 WhatsApp 發送的消息，回覆課程資訊。
無需 ICP，直接架在 Zeabur 上。
"""

import logging
import os
from typing import Optional, List, Dict
import requests

from bot_webhook import ZeaburBot

logger = logging.getLogger("whatsapp_handler")

# WhatsApp Cloud API 配置
GRAPH_API_BASE = "https://graph.facebook.com/v20.0"


def get_phone_number_id() -> str:
    """從環境變量獲取 Phone Number ID"""
    return os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")


def get_access_token() -> str:
    """從環境變量獲取 Access Token"""
    return os.environ.get("WHATSAPP_ACCESS_TOKEN", "")


def get_verify_token() -> str:
    """從環境變量獲取 Webhook Verify Token"""
    return os.environ.get("WHATSAPP_VERIFY_TOKEN", "")


class WhatsAppHandler:
    """WhatsApp Cloud API 消息處理器"""

    def __init__(self):
        self.phone_number_id = get_phone_number_id()
        self.access_token = get_access_token()
        self.api_url = f"{GRAPH_API_BASE}/{self.phone_number_id}/messages"
        self._bot: Optional[ZeaburBot] = None

    def _get_bot(self) -> Optional[ZeaburBot]:
        """惰性初始化課程查詢 bot"""
        if self._bot is None:
            try:
                self._bot = ZeaburBot()
            except Exception as e:
                logger.warning(f"課程 bot 初始化失敗: {e}")
        return self._bot

    def _send_text(self, to: str, text: str) -> bool:
        """發送文字消息到指定 WhatsApp 號碼"""
        if not self.access_token or not self.phone_number_id:
            logger.error("WhatsApp API 未配置")
            return False

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": text},
        }

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.post(self.api_url, json=payload, headers=headers, timeout=30)
            if resp.status_code == 200:
                logger.info(f"消息發送成功 -> {to}")
                return True
            else:
                logger.warning(f"消息發送失敗: {resp.status_code} {resp.text}")
                return False
        except Exception as e:
            logger.exception(f"發送消息異常: {e}")
            return False

    def _get_courses_text(self) -> str:
        """獲取課程列表文字"""
        bot = self._get_bot()
        if not bot or not bot.crawler:
            return "課程資料暫時無法取得，請稍後再試。"

        try:
            courses = bot.crawler.scrape()
            if not courses:
                return "目前沒有找到課程資訊，請稍後再查詢。"

            lines = ["📚 *澳門家長學堂最新課程*"]
            for i, c in enumerate(courses[:5], 1):
                title = c.get("title", "未命名課程")
                date_str = c.get("date", "")
                link = c.get("link", "")
                lines.append(f"\n*{i}. {title}*")
                if date_str:
                    lines.append(f"📅 {date_str}")
                if link:
                    lines.append(f"🔗 {link}")

            if len(courses) > 5:
                lines.append(f"\n...還有 {len(courses) - 5} 個課程")

            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"獲取課程失敗: {e}")
            return "課程資料獲取失敗，請稍後再試。"

    def _handle_text_message(self, from_number: str, text: str) -> None:
        """處理家長發送的文字消息"""
        text_lower = text.strip().lower()
        logger.info(f"收到消息 from={from_number}: {text}")

        # 關鍵詞匹配
        if any(k in text_lower for k in ["課程", "course", "最新"]):
            reply = self._get_courses_text()
        elif any(k in text_lower for k in ["報名", "報名表", "報名連結"]):
            reply = (
                "📝 *報名方式*\n\n"
                "請瀏覽澳門家長學堂官網查看最新課程及報名詳情：\n"
                "https://www.parentsschool.edu.mo\n\n"
                "如需協助，請致電中心查詢。"
            )
        elif any(k in text_lower for k in ["你好", "hello", "hi", "help", "幫助"]):
            reply = (
                "👋 你好！我是澳門家長學堂課程助手。\n\n"
                "你可以發送以下關鍵詞：\n"
                "• *課程* / *最新* — 查看最新課程列表\n"
                "• *報名* — 獲取報名資訊\n\n"
                "有什麼可以幫你的嗎？"
            )
        else:
            reply = (
                "🤔 我不太明白你的意思。\n\n"
                "試試發送：\n"
                "• *課程* — 查看最新課程\n"
                "• *報名* — 報名資訊\n"
                "• *你好* — 查看幫助"
            )

        self._send_text(from_number, reply)

    def handle_webhook(self, data: dict) -> None:
        """處理 Meta 發來的 webhook 事件"""
        logger.info(f"收到 WhatsApp webhook: {data}")

        # 提取消息
        entries = data.get("entry", [])
        for entry in entries:
            changes = entry.get("changes", [])
            for change in changes:
                value = change.get("value", {})
                messages = value.get("messages", [])

                for msg in messages:
                    msg_type = msg.get("type")
                    from_number = msg.get("from")

                    if msg_type == "text":
                        text_body = msg.get("text", {}).get("body", "")
                        if from_number and text_body:
                            self._handle_text_message(from_number, text_body)
                    else:
                        # 非文字消息（圖片、語音等），回覆提示
                        if from_number:
                            self._send_text(
                                from_number,
                                "抱歉，我目前只支援文字消息查詢課程。\n請發送 *課程* 查看最新課程資訊。"
                            )

    @staticmethod
    def verify_challenge(
        mode: str, verify_token: str, challenge: str
    ) -> Optional[str]:
        """驗證 webhook 訂閱請求

        Returns:
            challenge string if verified, None otherwise
        """
        expected_token = get_verify_token()
        if not expected_token:
            logger.error("WHATSAPP_VERIFY_TOKEN 未設置")
            return None

        if mode == "subscribe" and verify_token == expected_token:
            logger.info("Webhook 驗證成功")
            return challenge
        else:
            logger.warning(f"Webhook 驗證失敗: mode={mode}, token_match={verify_token == expected_token}")
            return None


def is_configured() -> bool:
    """檢查 WhatsApp API 是否已配置"""
    return bool(get_phone_number_id() and get_access_token())
