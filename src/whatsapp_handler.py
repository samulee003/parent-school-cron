"""WhatsApp Cloud API 處理模組

接收家長透過 WhatsApp 發送的消息，回覆課程資訊。
無需 ICP，直接架在 Zeabur 上。
"""

import logging
import os
import hashlib
import hmac
import json
import re
from typing import Optional, List, Dict, Any
import requests

from bot_webhook import ZeaburBot
from scraper import AGE_GROUP_LABELS, TOPICS, TARGETS
from whatsapp_memory import WhatsAppMemoryStore

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

PAGE_SIZE = 3
NEXT_PAGE_KEYWORDS = {
    "更多", "下一頁", "下頁", "還有嗎", "還有沒有", "還有",
    "有其他嗎", "還有別的嗎", "再來", "繼續", "more", "next",
}
ALL_COURSE_KEYWORDS = {"全部課程", "全部", "all"}
RESET_KEYWORDS = {"重設", "重新設定", "reset"}
PROFILE_KEYWORDS = {"我的偏好", "偏好", "設定", "狀態", "profile"}
NEGATION_WORDS = ("不要", "不用", "不想", "不是", "唔要", "唔係", "排除", "非")


def get_phone_number_id() -> str:
    """從環境變量獲取 Phone Number ID"""
    return os.environ.get("WHATSAPP_PHONE_NUMBER_ID", "")


def get_access_token() -> str:
    """從環境變量獲取 Access Token"""
    return os.environ.get("WHATSAPP_ACCESS_TOKEN", "")


def get_verify_token() -> str:
    """從環境變量獲取 Webhook Verify Token"""
    return os.environ.get("WHATSAPP_VERIFY_TOKEN", "")


def get_deepseek_api_key() -> str:
    return os.environ.get("DEEPSEEK_API_KEY", "")


def get_deepseek_base_url() -> str:
    return os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")


def get_deepseek_model() -> str:
    return os.environ.get("DEEPSEEK_MODEL", "deepseek-v4-flash")


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


def detect_child_age_group(text: str) -> Optional[str]:
    """從自然語句推測孩子年齡層，例如「小朋友1歲半」「孩子7歲」。"""
    text_lower = text.strip().lower().replace("岁", "歲")
    match = re.search(r"(\d+(?:\.\d+)?)\s*歲", text_lower)
    if not match:
        return None
    age = float(match.group(1))
    if 0 <= age < 3:
        return "0-2歲"
    if 3 <= age < 7:
        return "3-6歲"
    if 7 <= age < 13:
        return "7-12歲"
    if 13 <= age <= 18:
        return "13-18歲"
    return None


def detect_child_age_groups(text: str) -> List[str]:
    """從自然語句抓多個孩子年齡，例如「4歲和13歲」。"""
    text_lower = text.strip().lower().replace("岁", "歲")
    groups: List[str] = []
    for match in re.finditer(r"(\d+(?:\.\d+)?)\s*歲", text_lower):
        age = float(match.group(1))
        group = ""
        if 0 <= age < 3:
            group = "0-2歲"
        elif 3 <= age < 7:
            group = "3-6歲"
        elif 7 <= age < 13:
            group = "7-12歲"
        elif 13 <= age <= 18:
            group = "13-18歲"
        if group and group not in groups:
            groups.append(group)
    return groups


def detect_age_groups(text: str) -> List[str]:
    """從文字中找出所有年齡層線索。"""
    text_lower = text.strip().lower().replace("岁", "歲")
    groups = detect_child_age_groups(text_lower)

    for age in AGE_GROUP_LABELS:
        if age.lower() in text_lower and age not in groups:
            groups.append(age)

    for age, keywords in AGE_KEYWORDS.items():
        if any(keyword.lower() in text_lower for keyword in keywords):
            if age not in groups:
                groups.append(age)

    return groups


def detect_target(text: str) -> str:
    text_normalized = text.strip()
    for target in TARGETS:
        if target in text_normalized:
            return target
    return ""


def detect_topic(text: str) -> str:
    text_normalized = text.strip()
    for topic in TOPICS:
        if topic in text_normalized:
            return topic
    return ""


class WhatsAppHandler:
    """WhatsApp Cloud API 消息處理器"""

    def __init__(self, memory_store: Optional[WhatsAppMemoryStore] = None):
        self.phone_number_id = get_phone_number_id()
        self.access_token = get_access_token()
        self.api_url = f"{get_graph_api_base()}/{self.phone_number_id}/messages"
        self._bot: Optional[ZeaburBot] = None
        self._last_queries: Dict[str, Dict[str, Any]] = {}
        self._profiles: Dict[str, Dict[str, Any]] = {}
        self._memory = memory_store or WhatsAppMemoryStore()

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
    def _course_values(course: Any, attr: str) -> List[str]:
        if isinstance(course, dict):
            value = course.get(attr, [])
        else:
            value = getattr(course, attr, [])
        if isinstance(value, list):
            return [str(v) for v in value if v]
        if value:
            return [str(value)]
        return []

    @staticmethod
    def _parse_page_request(text: str) -> Optional[int]:
        normalized = re.sub(r"[\s\?？!！。,.、，；;:：]+", "", text.strip().lower())
        if normalized in NEXT_PAGE_KEYWORDS:
            return -1
        match = re.search(r"(?:第)?(\d+)(?:頁|页|page)?", normalized)
        if match and ("頁" in normalized or "页" in normalized or "page" in normalized):
            return max(int(match.group(1)), 1)
        return None

    @staticmethod
    def _parse_detail_request(text: str) -> Optional[int]:
        normalized = text.strip().lower().replace(" ", "")
        match = re.fullmatch(r"(?:詳情|詳細|detail|link|連結)(\d+)", normalized)
        if match:
            return int(match.group(1))
        return None

    @staticmethod
    def _is_course_intent(text: str) -> bool:
        text_lower = text.strip().lower()
        return any(k in text_lower for k in ["課程", "course", "最新", "推薦", "幫我揀", "幫我選"])

    @staticmethod
    def _is_all_courses_request(text: str) -> bool:
        normalized = text.strip().lower().replace(" ", "")
        return normalized in ALL_COURSE_KEYWORDS

    @staticmethod
    def _normalize_age_groups(age_group: Any = "") -> List[str]:
        if isinstance(age_group, list):
            return [str(a) for a in age_group if a]
        if age_group:
            return [str(age_group)]
        return []

    def _profile_age_groups(self, profile: Dict[str, Any]) -> List[str]:
        groups = self._normalize_age_groups(profile.get("age_groups", []))
        legacy_age = profile.get("age_group", "")
        if legacy_age and legacy_age not in groups:
            groups.append(str(legacy_age))
        return groups

    @staticmethod
    def _mentions_negative(text: str, value: str) -> bool:
        text_normalized = text.replace(" ", "")
        if value not in text_normalized:
            return False
        return any(
            f"{word}{value}" in text_normalized
            or re.search(rf"{re.escape(word)}.{{0,2}}{re.escape(value)}", text_normalized)
            for word in NEGATION_WORDS
        )

    def _detect_positive_option(self, text: str, options: List[str]) -> str:
        text_normalized = text.strip()
        for option in options:
            if option in text_normalized and not self._mentions_negative(text_normalized, option):
                return option
        return ""

    @staticmethod
    def _query_title(age_group: Any = "", target: str = "", topic: str = "") -> str:
        filters = []
        age_groups = WhatsAppHandler._normalize_age_groups(age_group)
        for age in age_groups:
            label = AGE_GROUP_LABELS.get(age, age)
            filters.append(f"{label}（{age}）")
        if target:
            filters.append(target)
        if topic:
            filters.append(topic)
        if filters:
            return f"📚 *{' / '.join(filters)}課程*"
        return "📚 *澳門家長學堂精選課程*"

    def _filter_courses(
        self,
        courses: List[Any],
        age_group: Any = "",
        target: str = "",
        topic: str = "",
    ) -> List[Any]:
        age_groups = self._normalize_age_groups(age_group)
        if age_groups:
            courses = [
                c for c in courses
                if any(
                    self._course_value(c, "age_group") == age
                    or age in self._course_values(c, "age_groups")
                    for age in age_groups
                )
            ]
        if target:
            courses = [
                c for c in courses
                if self._course_value(c, "target") == target
            ]
        if topic:
            courses = [
                c for c in courses
                if self._course_value(c, "topic") == topic
            ]
        return courses

    def _course_key(self, course: Any) -> str:
        course_id = self._course_value(course, "id")
        if course_id:
            return f"id:{course_id}"
        return "|".join([
            self._course_value(course, "name"),
            self._course_value(course, "date"),
            self._course_value(course, "age_group"),
        ])

    def _dedupe_courses(self, courses: List[Any]) -> List[Any]:
        seen = set()
        unique = []
        for course in courses:
            key = self._course_key(course)
            if key in seen:
                continue
            seen.add(key)
            unique.append(course)
        return unique

    def _fetch_courses(self, course_source: Any, age_group: Any = "") -> List[Any]:
        age_groups = self._normalize_age_groups(age_group)
        if hasattr(course_source, "fetch_courses"):
            if age_groups:
                courses = []
                for age in age_groups:
                    courses.extend(course_source.fetch_courses(
                        age_group=age,
                        status="",
                        max_retries=2,
                        delay=1.0,
                    ))
                return self._dedupe_courses(courses)

            courses = []
            for age in AGE_GROUP_LABELS:
                try:
                    courses.extend(course_source.fetch_courses(
                        age_group=age,
                        status="",
                        max_retries=2,
                        delay=1.0,
                    ))
                except Exception as e:
                    logger.warning("抓取 %s 課程失敗: %s", age, e)
            if courses:
                return self._dedupe_courses(courses)

        return course_source.fetch_all_open_courses(max_retries=2, delay=1.0)

    def _load_profile(self, from_number: str) -> Dict[str, Any]:
        if from_number not in self._profiles:
            self._profiles[from_number] = self._memory.get_profile(from_number)
        return dict(self._profiles.get(from_number, {}))

    def _save_profile(self, from_number: str, profile: Dict[str, Any]) -> None:
        self._profiles[from_number] = dict(profile)
        self._memory.save_profile(from_number, profile)

    def _update_profile_from_text(self, from_number: str, text: str) -> Dict[str, Any]:
        profile = self._load_profile(from_number)
        age_groups = detect_age_groups(text)
        target = self._detect_positive_option(text, TARGETS)
        topic = self._detect_positive_option(text, TOPICS)
        negative_targets = [t for t in TARGETS if self._mentions_negative(text, t)]
        negative_topics = [t for t in TOPICS if self._mentions_negative(text, t)]

        if age_groups:
            previous_groups = self._profile_age_groups(profile)
            profile["age_groups"] = age_groups
            profile["age_group"] = age_groups[0]
            if previous_groups and previous_groups != age_groups and not target and not topic:
                profile.pop("target", None)
                profile.pop("topic", None)
        if target:
            profile["target"] = target
        elif profile.get("target") in negative_targets:
            profile.pop("target", None)
        if topic:
            profile["topic"] = topic
        elif profile.get("topic") in negative_topics:
            profile.pop("topic", None)
        self._profiles[from_number] = profile
        self._memory.save_profile(from_number, profile)
        return profile

    @staticmethod
    def _profile_has_signal(profile: Dict[str, Any]) -> bool:
        return any(profile.get(k) for k in ["age_group", "age_groups", "target", "topic"])

    def _profile_text(self, profile: Dict[str, Any]) -> str:
        if not self._profile_has_signal(profile):
            return "我暫時未知道孩子年齡或你想找哪類課程。"
        parts = []
        for age_group in self._profile_age_groups(profile):
            parts.append(f"{AGE_GROUP_LABELS.get(age_group, age_group)}（{age_group}）")
        if profile.get("target"):
            parts.append(profile["target"])
        if profile.get("topic"):
            parts.append(profile["topic"])
        return "我會先按「" + " / ".join(parts) + "」幫你縮窄。"

    def _onboarding_text(self, profile: Dict[str, Any]) -> str:
        if self._profile_has_signal(profile):
            return (
                f"好，我先不把全部課程丟給你。\n{self._profile_text(profile)}\n\n"
                "你可以補一句，例如：*想親子*、*想家長課*、*重視身心健康*。\n"
                "我會按你的條件推薦少量課程。"
            )
        return (
            "我先幫你縮窄，不直接丟一堆課程。\n\n"
            "請回覆一句就可以：\n"
            "例：*小朋友1歲，想親子活動*\n"
            "例：*孩子7歲，想環境適應*\n"
            "例：*家長，想身心健康*\n\n"
            "如果你真的要看全列表，回覆 *全部課程*。"
        )

    def _get_courses_text(
        self,
        from_number: str,
        age_group: Any = "",
        target: str = "",
        topic: str = "",
        page: int = 1,
        agentic: bool = False,
    ) -> str:
        """獲取課程列表文字"""
        course_source = self._get_course_source()
        if not course_source:
            return "課程資料暫時無法取得，請稍後再試。"

        try:
            courses = self._fetch_courses(course_source, age_group=age_group)
            courses = self._filter_courses(courses, age_group, target, topic)

            if not courses:
                return (
                    "目前沒有找到符合條件的報名中課程。\n\n"
                    "可以試試：*課程*、*0-2歲*、*親子*、*家長*、*身心健康*。"
                )

            total_pages = max((len(courses) + PAGE_SIZE - 1) // PAGE_SIZE, 1)
            page = min(max(page, 1), total_pages)
            start = (page - 1) * PAGE_SIZE
            page_courses = courses[start:start + PAGE_SIZE]
            self._last_queries[from_number] = {
                "age_group": age_group,
                "target": target,
                "topic": topic,
                "page": page,
                "last_courses": page_courses,
                "last_start": start,
            }
            self._memory.save_last_query(from_number, self._last_queries[from_number])

            lines = [
                f"{self._query_title(age_group, target, topic)}\n第 {page}/{total_pages} 頁",
            ]
            if agentic:
                lines.append(
                    "我先挑最貼近你條件的少量選項，報名連結直接附在下面。"
                )
            if page == 1 and not any([age_group, target, topic]):
                lines.append(
                    "想少一點雜訊，可以直接回覆：*0-2歲*、*3-6歲*、*親子*、*家長*、*身心健康*。"
                )
            if page < total_pages:
                lines.append(
                    "下一頁請在下方輸入框傳送：*更多* 或 *下一頁*。"
                    "如果 WhatsApp 顯示「閱讀更多」，那只是展開本訊息。"
                )
            elif total_pages > 1:
                lines.append("已經是最後一頁。輸入 *課程* 可重新從第一頁開始。")

            for i, c in enumerate(page_courses, start + 1):
                title = self._course_value(c, "name", "未命名課程")
                date_str = self._course_value(c, "date")
                course_topic = self._course_value(c, "topic")
                course_target = self._course_value(c, "target")
                status = self._course_value(c, "status")
                link = self._course_value(c, "detail_url")
                lines.append(f"\n*{i}. {title}*")
                if date_str:
                    lines.append(f"📅 {date_str}")
                tags = " | ".join([v for v in [course_topic, course_target] if v])
                if tags:
                    lines.append(f"🏷️ {tags}")
                if status:
                    lines.append(f"狀態：{status}")
                if link:
                    lines.append(f"🔗 {link}")
                if agentic:
                    reason_bits = []
                    matched_age = False
                    for age in self._normalize_age_groups(age_group):
                        if (
                            self._course_value(c, "age_group") == age
                            or age in self._course_values(c, "age_groups")
                        ):
                            matched_age = True
                            break
                    if matched_age:
                        reason_bits.append("年齡吻合")
                    if target and self._course_value(c, "target") == target:
                        reason_bits.append(f"適合{target}")
                    if topic and self._course_value(c, "topic") == topic:
                        reason_bits.append(f"主題是{topic}")
                    if reason_bits:
                        lines.append(f"為什麼推薦：{'、'.join(reason_bits)}")

            remaining = len(courses) - (start + len(page_courses))
            if remaining > 0:
                lines.append(f"\n...還有 {remaining} 個課程")
                lines.append("輸入 *更多* 或 *下一頁* 查看下一批。")

            return "\n".join(lines)
        except Exception as e:
            logger.exception(f"獲取課程失敗: {e}")
            return "課程資料獲取失敗，請稍後再試。"

    def _course_summary_for_llm(self, course: Any, number: int) -> Dict[str, str]:
        return {
            "number": str(number),
            "name": self._course_value(course, "name"),
            "date": self._course_value(course, "date"),
            "age_group": self._course_value(course, "age_group"),
            "age_groups": self._course_values(course, "age_groups"),
            "topic": self._course_value(course, "topic"),
            "target": self._course_value(course, "target"),
            "status": self._course_value(course, "status"),
            "detail_url": self._course_value(course, "detail_url"),
        }

    def _available_age_summary(self, courses: List[Any]) -> str:
        counts: Dict[str, int] = {}
        for course in courses:
            age_groups = self._course_values(course, "age_groups") or [self._course_value(course, "age_group") or "未標明"]
            for age_group in age_groups:
                counts[age_group] = counts.get(age_group, 0) + 1

        parts = []
        for age_group in AGE_GROUP_LABELS:
            count = counts.get(age_group, 0)
            if count:
                label = AGE_GROUP_LABELS.get(age_group, age_group)
                parts.append(f"{label}（{age_group}）{count}個")
        if counts.get("未標明"):
            parts.append(f"未標明年齡{counts['未標明']}個")
        return "、".join(parts) if parts else "暫時沒有報名中課程"

    def _no_match_text(self, profile: Dict[str, Any], courses: List[Any]) -> str:
        age_groups = self._profile_age_groups(profile)
        target = profile.get("target", "")
        topic = profile.get("topic", "")
        filters = []
        for age_group in age_groups:
            filters.append(f"{AGE_GROUP_LABELS.get(age_group, age_group)}（{age_group}）")
        if target:
            filters.append(target)
        if topic:
            filters.append(topic)

        if age_groups:
            age_courses = self._filter_courses(courses, age_group=age_groups)
            if not age_courses:
                return (
                    f"我查了目前報名中的課程，暫時沒有 *{' / '.join(filters)}* "
                    "適用的課程。\n\n"
                    f"現在有的年齡層是：{self._available_age_summary(courses)}。\n"
                    "你可以回覆其他年齡層，例如 *0-2歲*、*3-6歲*，或回覆 *全部課程* 看現有列表。"
                )

        filter_text = " / ".join(filters) if filters else "這些條件"
        return (
            f"我查了目前報名中的課程，暫時沒有完全符合 *{filter_text}* 的選項。\n\n"
            f"現在有的年齡層是：{self._available_age_summary(courses)}。\n"
            "你可以放寬一個條件再試，例如只回覆年齡層，或回覆 *全部課程* 看現有列表。"
        )

    def _call_deepseek(self, messages: List[Dict[str, str]], max_tokens: int = 650) -> Optional[str]:
        api_key = get_deepseek_api_key()
        if not api_key:
            return None

        payload = {
            "model": get_deepseek_model(),
            "messages": messages,
            "thinking": {"type": "disabled"},
            "stream": False,
            "temperature": 0.4,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        try:
            resp = requests.post(
                f"{get_deepseek_base_url()}/chat/completions",
                headers=headers,
                json=payload,
                timeout=25,
            )
            if resp.status_code != 200:
                logger.warning("DeepSeek API 失敗: %s %s", resp.status_code, resp.text[:500])
                return None
            data = resp.json()
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            return content.strip() or None
        except Exception as e:
            logger.warning("DeepSeek API 異常: %s", e)
            return None

    def _get_llm_recommendation_text(
        self,
        from_number: str,
        user_text: str,
        profile: Dict[str, Any],
    ) -> Optional[str]:
        course_source = self._get_course_source()
        if not course_source or not get_deepseek_api_key():
            return None

        try:
            requested_ages = self._profile_age_groups(profile)
            courses = self._fetch_courses(course_source, age_group=requested_ages)
            filtered = self._filter_courses(
                courses,
                requested_ages,
                profile.get("target", ""),
                profile.get("topic", ""),
            )
            if not filtered:
                age_group = requested_ages
                has_secondary_filter = bool(profile.get("target") or profile.get("topic"))
                if age_group and has_secondary_filter:
                    filtered = self._filter_courses(courses, age_group=age_group)
                if not filtered:
                    return self._no_match_text(profile, courses)

            candidates = filtered[:8]
            self._last_queries[from_number] = {
                "age_group": requested_ages,
                "target": profile.get("target", ""),
                "topic": profile.get("topic", ""),
                "page": 1,
                "last_courses": candidates,
                "last_start": 0,
            }
            self._memory.save_last_query(from_number, self._last_queries[from_number])
            candidate_payload = [
                self._course_summary_for_llm(course, i)
                for i, course in enumerate(candidates, 1)
            ]
            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是澳門家長學堂 WhatsApp agentic 課程助手。"
                        "你要先理解家長情境，再從候選課程中挑少量選項。"
                        "只能根據候選課程回答，不可創造課程、日期、名額或連結。"
                        "最多推薦 3 個課程。每個推薦要有一句人話理由。"
                        "每個推薦都要直接貼上候選課程提供的 detail_url 報名連結。"
                        "不要叫用戶再回覆「詳情1」才看連結。"
                        "如果資料不足，只問 1 個最關鍵問題。"
                        "用繁體中文，口吻自然、簡短、像真人助手，不要像公告。"
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "家長訊息": user_text,
                            "已知偏好": profile,
                            "候選課程": candidate_payload,
                            "輸出限制": "WhatsApp 短訊格式，最多 900 字。",
                        },
                        ensure_ascii=False,
                    ),
                },
            ]
            return self._call_deepseek(messages)
        except Exception as e:
            logger.warning("LLM 推薦建立失敗: %s", e)
            return None

    def _get_agentic_recommendation_text(
        self,
        from_number: str,
        profile: Dict[str, Any],
        user_text: str = "",
    ) -> str:
        if not self._profile_has_signal(profile):
            return self._onboarding_text(profile)

        llm_reply = self._get_llm_recommendation_text(from_number, user_text, profile)
        if llm_reply:
            return llm_reply

        return self._get_courses_text(
            from_number=from_number,
            age_group=self._profile_age_groups(profile),
            target=profile.get("target", ""),
            topic=profile.get("topic", ""),
            page=1,
            agentic=True,
        )

    def _get_course_detail_text(self, from_number: str, item_number: int) -> str:
        query = self._last_queries.get(from_number, {})
        page_courses = query.get("last_courses") or []
        start = int(query.get("last_start", 0))
        index = item_number - start - 1
        if index < 0 or index >= len(page_courses):
            return "找不到這個編號。請先傳 *課程*，再回覆例如 *詳情1*。"

        c = page_courses[index]
        title = self._course_value(c, "name", "未命名課程")
        date_str = self._course_value(c, "date")
        topic = self._course_value(c, "topic")
        target = self._course_value(c, "target")
        status = self._course_value(c, "status")
        link = self._course_value(c, "detail_url")
        lines = [f"🔎 *{title}*"]
        if date_str:
            lines.append(f"📅 {date_str}")
        tags = " | ".join([v for v in [topic, target] if v])
        if tags:
            lines.append(f"🏷️ {tags}")
        if status:
            lines.append(f"狀態：{status}")
        if link:
            lines.append(f"🔗 {link}")
        return "\n".join(lines)

    def _handle_text_message(self, from_number: str, text: str) -> None:
        """處理家長發送的文字消息"""
        text_lower = text.strip().lower()
        normalized = text.strip().lower().replace(" ", "")
        logger.info(f"收到消息 from={from_number}: {text}")

        # 關鍵詞匹配
        if normalized in RESET_KEYWORDS:
            self._profiles.pop(from_number, None)
            self._last_queries.pop(from_number, None)
            self._memory.clear_user(from_number)
            self._send_text(from_number, "已重設。你可以回覆：*小朋友幾歲，想找哪類課程*。")
            return

        if normalized in PROFILE_KEYWORDS:
            profile = self._load_profile(from_number)
            reply = (
                "📌 *我目前記得的偏好*\n\n"
                f"{self._profile_text(profile)}\n\n"
                "你可以直接補充，例如：*不要親子，要家長課*、*只要青少年*、*想身心健康*。"
            )
            self._send_text(from_number, reply)
            return

        profile = self._update_profile_from_text(from_number, text)
        age_groups = detect_age_groups(text)
        target = self._detect_positive_option(text, TARGETS)
        topic = self._detect_positive_option(text, TOPICS)
        page_request = self._parse_page_request(text)
        detail_request = self._parse_detail_request(text)
        if detail_request is not None:
            reply = self._get_course_detail_text(from_number, detail_request)
        elif age_groups or target or topic:
            reply = self._get_agentic_recommendation_text(from_number, profile, text)
        elif page_request is not None:
            if from_number not in self._last_queries:
                persisted_query = self._memory.get_last_query(from_number)
                if persisted_query:
                    self._last_queries[from_number] = persisted_query
                else:
                    reply = self._get_agentic_recommendation_text(from_number, profile, text)
                    self._send_text(from_number, reply)
                    return
            query = self._last_queries[from_number]
            if page_request == -1:
                query["page"] = int(query.get("page", 1)) + 1
            else:
                query["page"] = page_request
            reply = self._get_courses_text(
                from_number=from_number,
                age_group=query.get("age_group", ""),
                target=str(query.get("target", "")),
                topic=str(query.get("topic", "")),
                page=int(query.get("page", 1)),
                agentic=bool(query.get("age_group") or query.get("target") or query.get("topic")),
            )
        elif self._is_all_courses_request(text):
            reply = self._get_courses_text(from_number=from_number, page=1)
        elif self._is_course_intent(text):
            reply = self._get_agentic_recommendation_text(from_number, profile, text)
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
                "• *小朋友1歲，想親子活動* — 讓我按情境推薦\n"
                "• *推薦* / *幫我揀* — 用已知偏好推薦\n"
                "• *全部課程* — 查看精簡課程列表\n"
                "• *更多* / *下一頁* — 查看下一批課程\n"
                "• *重設* — 清除本次偏好\n"
                "• *報名* — 獲取報名資訊\n\n"
                "有什麼可以幫你的嗎？"
            )
        else:
            reply = (
                "🤔 我不太明白你的意思。\n\n"
                "試試發送：\n"
                "• *小朋友1歲，想親子活動* — 我幫你推薦\n"
                "• *幫我揀* — 按已知偏好推薦\n"
                "• *全部課程* — 看精簡列表\n"
                "• *重設* — 重新設定偏好"
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
