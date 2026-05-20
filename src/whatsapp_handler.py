"""WhatsApp Cloud API 處理模組

接收家長透過 WhatsApp 發送的消息，回覆課程資訊。
無需 ICP，直接架在 Zeabur 上。
"""

import logging
import os
import hashlib
import hmac
import re
from typing import Optional, List, Dict, Any
import requests

from bot_webhook import ZeaburBot
from scraper import AGE_GROUP_LABELS

logger = logging.getLogger("whatsapp_handler")

# WhatsApp Cloud API 配置
def get_graph_api_base() -> str:
    version = os.environ.get("WHATSAPP_API_VERSION", "v25.0").strip() or "v25.0"
    if not version.startswith("v"):
        version = f"v{version}"
    return f"https://graph.facebook.com/{version}"


AGE_KEYWORDS = {
    "0-2歲": ("0-2", "0至2", "0到2", "嬰兒", "嬰幼", "寶寶", "bb"),
    "3-6歲": ("3-6", "3至6", "3到6", "幼兒", "幼稚園", "幼兒園"),
    "7-12歲": ("7-12", "7至12", "7到12", "小學", "小學生", "兒童"),
    "13-18歲": ("13-18", "13至18", "13到18", "中學", "中學生", "青少年"),
}

PAGE_SIZE = 5
NEXT_PAGE_KEYWORDS = {"更多", "下一頁", "下頁", "more", "next"}


def get_phone_number_id() -> str:
    """從環境變量獲取 Phone Number ID"""
    return os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")


def get_access_token() -> str:
    """從環境變量獲取 Access Token"""
    return os.environ.get("WHATSAPP_ACCESS_TOKEN", "")


def get_verify_token() -> str:
    """從環境變量獲取 Webhook Verify Token"""
    return os.environ.get("WHATSAPP_VERIFY_TOKEN", "")


def is_valid_meta_signature(payload: bytes, signature_header: str, app_secret: str) -> bool:
    """驗證 Meta Webhook 的 X-Hub-Signature-256。"""
    if not payload or not signature_header or not app_secret:
        return False

    prefix = "sha256="
    if not signature_header.startswith(prefix):
        return False

    expected = hmac.new(
        app_secret.encode("utf-8"),
        payload,
        hashlib.sha256,
    ).hexdigest()
    received = signature_header[len(prefix):]
    return hmac.compare_digest(expected, received)


def detect_age_group(text: str) -> Optional[str]:
    """從用戶文字中找出年齡層。"""
    text_lower = text.strip().lower().replace("岁", "歲")
    for age in AGE_GROUP_LABELS:
        if age.lower() in text_lower:
            return age
    for age, keywords in AGE_KEYWORDS.items():
        if any(keyword.lower() in text_lower for keyword in keywords):
            return age
    return None


class WhatsAppHandler:
    """WhatsApp Cloud API 消息處理器"""

    def __init__(self):
        self.phone_number_id = get_phone_number_id()
        self.access_token = get_access_token()
        self.api_url = f"{get_graph_api_base()}/{self.phone_number_id}/messages"
        self._bot: Optional[ZeaburBot] = None
        self._last_queries: Dict[str, Dict[str, Any]] = {}

    def _get_bot(self) -> Optional[ZeaburBot]:
        """惰性初始化課程查詢 bot"""
        if self._bot is None:
            try:
                self._bot = ZeaburBot()
            except Exception as e:
                logger.warning(f"課程 bot 初始化失敗: {e}")
        return self._bot

    def _get_course_source(self):
        bot = self._get_bot()
        if not bot:
            return None
        return getattr(bot, "scraper", None) or getattr(bot, "crawler", None)

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

    @staticmethod
    def _course_value(course: Any, attr: str, fallback: str = "") -> str:
        """同時支援 Course dataclass 與 dict，避免來源轉換時炸掉。"""
        if isinstance(course, dict):
            return str(course.get(attr, fallback) or fallback)
        return str(getattr(course, attr, fallback) or fallback)

    @staticmethod
    def _parse_page_request(text: str) -> Optional[int]:
        normalized = text.strip().lower().replace(" ", "")
        if normalized in NEXT_PAGE_KEYWORDS:
            return -1
        match = re.search(r"(?:第)?(\d+)(?:頁|页|page)?", normalized)
        if match and ("頁" in normalized or "页" in normalized or "page" in normalized):
            return max(int(match.group(1)), 1)
        return None

    def _get_courses_text(self, age_group: str = "", page: int = 1) -> str:
        """獲取課程列表文字"""
        course_source = self._get_course_source()
        if not course_source:
            return "課程資料暫時無法取得，請稍後再試。"

        try:
            courses = course_source.fetch_all_open_courses(max_retries=2, delay=1.0)
            if age_group:
                courses = [
                    c for c in courses
                    if self._course_value(c, "age_group") == age_group
                ]

            if not courses:
                if age_group:
                    label = AGE_GROUP_LABELS.get(age_group, age_group)
                    return f"目前沒有找到 {label}（{age_group}）的報名中課程，請稍後再查詢。"
                return "目前沒有找到報名中課程，請稍後再查詢。"

            if age_group:
                label = AGE_GROUP_LABELS.get(age_group, age_group)
                title = f"📚 *澳門家長學堂 {label}（{age_group}）課程*"
            else:
                title = "📚 *澳門家長學堂最新課程*"

            total_pages = max((len(courses) + PAGE_SIZE - 1) // PAGE_SIZE, 1)
            page = min(max(page, 1), total_pages)
            start = (page - 1) * PAGE_SIZE
            page_courses = courses[start:start + PAGE_SIZE]
            lines = [f"{title}\n第 {page}/{total_pages} 頁"]

            for i, c in enumerate(page_courses, start + 1):
                title = self._course_value(c, "name", "未命名課程")
                date_str = self._course_value(c, "date")
                topic = self._course_value(c, "topic")
                target = self._course_value(c, "target")
                link = self._course_value(c, "detail_url")
                lines.append(f"\n*{i}. {title}*")
                if date_str:
                    lines.append(f"📅 {date_str}")
                tags = " | ".join([v for v in [topic, target] if v])
                if tags:
                    lines.append(f"🏷️ {tags}")
                if link:
                    lines.append(f"🔗 {link}")

            remaining = len(courses) - (start + len(page_courses))
            if remaining > 0:
                lines.append(f"\n...還有 {remaining} 個課程")
                lines.append("輸入 *更多* 或 *下一頁* 查看下一批。")
            elif total_pages > 1:
                lines.append("\n已經是最後一頁。輸入 *課程* 可重新從第一頁開始。")

            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"獲取課程失敗: {e}")
            return "課程資料獲取失敗，請稍後再試。"

    def _handle_text_message(self, from_number: str, text: str) -> None:
        """處理家長發送的文字消息"""
        text_lower = text.strip().lower()
        logger.info(f"收到消息 from={from_number}: {text}")

        # 關鍵詞匹配
        age_group = detect_age_group(text)
        page_request = self._parse_page_request(text)
        if age_group:
            self._last_queries[from_number] = {"age_group": age_group, "page": 1}
            reply = self._get_courses_text(age_group=age_group, page=1)
        elif page_request is not None:
            query = self._last_queries.get(from_number, {"age_group": "", "page": 1})
            if page_request == -1:
                query["page"] = int(query.get("page", 1)) + 1
            else:
                query["page"] = page_request
            self._last_queries[from_number] = query
            reply = self._get_courses_text(
                age_group=str(query.get("age_group", "")),
                page=int(query.get("page", 1)),
            )
        elif any(k in text_lower for k in ["課程", "course", "最新"]):
            self._last_queries[from_number] = {"age_group": "", "page": 1}
            reply = self._get_courses_text(page=1)
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
                "• *更多* / *下一頁* — 查看下一批課程\n"
                "• *報名* — 獲取報名資訊\n\n"
                "有什麼可以幫你的嗎？"
            )
        else:
            reply = (
                "🤔 我不太明白你的意思。\n\n"
                "試試發送：\n"
                "• *課程* — 查看最新課程\n"
                "• *更多* — 查看下一批課程\n"
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
