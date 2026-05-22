"""WhatsApp Cloud API 處理模組

接收家長透過 WhatsApp 發送的消息，回覆課程資訊。
無需 ICP，直接架在 Zeabur 上。
"""

import logging
import os
import base64
import hashlib
import hmac
import inspect
import json
import re
import shutil
import subprocess
import tempfile
from datetime import datetime
from typing import Optional, List, Dict, Any
import requests

from bot_webhook import ZeaburBot
from scraper import AGE_GROUP_LABELS, TOPICS, TARGETS, normalize_course_detail_url
import whatsapp_nlu
from whatsapp_harness import decide_message_route
from whatsapp_memory import WhatsAppMemoryStore

logger = logging.getLogger("whatsapp_handler")

# WhatsApp Cloud API 配置
def get_graph_api_base() -> str:
    version = os.environ.get("WHATSAPP_API_VERSION", "v25.0").strip() or "v25.0"
    if not version.startswith("v"):
        version = f"v{version}"
    return f"https://graph.facebook.com/{version}"


AGE_KEYWORDS = whatsapp_nlu.AGE_KEYWORDS
CHINESE_NUMERAL_VALUES = whatsapp_nlu.CHINESE_NUMERAL_VALUES
PAGE_SIZE = 3
NEXT_PAGE_KEYWORDS = whatsapp_nlu.NEXT_PAGE_KEYWORDS
ALL_COURSE_KEYWORDS = whatsapp_nlu.ALL_COURSE_KEYWORDS
RESET_KEYWORDS = {"重設", "重新設定", "reset"}
PROFILE_KEYWORDS = {"我的偏好", "偏好", "設定", "狀態", "profile"}
PROACTIVE_ALLOW_KEYWORDS = {
    "同意收課程提醒", "同意收提醒", "同意推送", "可以推送",
    "可以提醒", "開啟推送", "恢復推送", "接收推送", "收課程提醒",
}
PROACTIVE_DENY_KEYWORDS = {
    "不同意推送", "暫時不同意推送", "暂时不同意推送", "不同意收課程提醒",
    "不同意收提醒", "未同意推送", "暫不同意推送", "暂不同意推送",
}
PROACTIVE_PAUSE_KEYWORDS = {
    "暫停推送", "暂停推送", "停止推送", "取消推送", "不要推送",
    "不用推送", "不想收到", "唔好推送", "停止提醒", "暫停提醒", "暂停提醒",
}
NEGATION_WORDS = ("不要", "不用", "不想", "不是", "唔要", "唔係", "排除", "非")
BARE_RECOMMENDATION_COMMANDS = {
    "推薦", "推介", "有推薦嗎", "有推介嗎", "有咩推薦", "有咩推介",
    "有冇推薦", "有冇推介", "幫我推薦", "幫我推介", "幫我揀", "幫我選",
}
COURSE_DOMAIN_KEYWORDS = whatsapp_nlu.COURSE_DOMAIN_KEYWORDS
COURSE_INTENT_KEYWORDS = whatsapp_nlu.COURSE_INTENT_KEYWORDS
PARENT_CONTEXT_KEYWORDS = (
    "小朋友", "孩子", "子女", "仔女", "兒子", "儿子", "女兒", "女儿",
    "我個仔", "我个仔", "我個女", "我个女", "家長", "家长", "父母",
    "媽媽", "妈妈", "爸爸", "幼兒", "幼儿", "小學生", "小学生",
    "中學生", "中学生", "青少年", "bb", "寶寶", "宝宝",
)
OFF_TOPIC_KEYWORDS = whatsapp_nlu.OFF_TOPIC_KEYWORDS
OUT_OF_SCOPE_AI_KEYWORDS = (
    "chatgpt", "openai", "deepseek", "claude", "生成式ai", "生成式 ai",
    "人工智能", "ai工具", "ai 工具", "大模型", "論文", "论文",
)
LONG_MESSAGE_OFF_TOPIC_CHARS = 120
LLM_PROFILE_EXTRACTION_MAX_CHARS = 180
PAIN_POINT_RULES = whatsapp_nlu.PAIN_POINT_RULES

ONBOARDING_QUESTION = (
    "我先幫你縮窄，不直接丟一堆課程。\n\n"
    "小朋友幾多歲？最近比較想處理："
    "*情緒*、*學習*、*親子溝通*、*升學壓力*，還是其他？"
)

ONBOARDING_CONCERN_QUESTION = (
    "收到，我先記住孩子年齡。\n\n"
    "最近最想處理哪方面？"
    "可以直接回覆：*情緒*、*學習*、*親子溝通*、*升學壓力*，或用一句話說明。"
)

ONBOARDING_AGE_QUESTION = (
    "收到，我先記住你關心的方向。\n\n"
    "小朋友幾多歲？例如：*4歲*、*小學*、*13歲*。"
)

PROACTIVE_CONSENT_PROMPT = (
    "\n\n之後如果有貼近你情況的新課程，我可以偶爾提醒你。"
    "回覆「同意推送」即可。"
)
ONBOARDING_NOTE_MARKER = "[[ai:onboarding]]"


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


def get_deepseek_daily_limit_per_user() -> int:
    raw_limit = os.environ.get("DEEPSEEK_DAILY_LIMIT_PER_USER", "12")
    try:
        return max(int(raw_limit), 0)
    except ValueError:
        logger.warning("DEEPSEEK_DAILY_LIMIT_PER_USER 無效，使用預設值 12")
        return 12


def get_deepseek_daily_limit_global() -> int:
    raw_limit = os.environ.get("DEEPSEEK_DAILY_LIMIT_GLOBAL", "200")
    try:
        return max(int(raw_limit), 0)
    except ValueError:
        logger.warning("DEEPSEEK_DAILY_LIMIT_GLOBAL 無效，使用預設值 200")
        return 200


def get_proactive_template_name() -> str:
    return os.environ.get("WHATSAPP_PROACTIVE_TEMPLATE_NAME", "").strip()


def get_proactive_template_language() -> str:
    return os.environ.get("WHATSAPP_PROACTIVE_TEMPLATE_LANGUAGE", "zh_HK").strip() or "zh_HK"


def get_transcription_provider() -> str:
    provider = os.environ.get("AUDIO_TRANSCRIPTION_PROVIDER", "auto").strip().lower()
    if provider not in {"auto", "stepfun", "openai"}:
        logger.warning("AUDIO_TRANSCRIPTION_PROVIDER 無效，使用 auto")
        return "auto"
    return provider


def get_stepfun_api_key() -> str:
    return os.environ.get("STEPFUN_API_KEY", "").strip()


def get_stepfun_base_url() -> str:
    return os.environ.get("STEPFUN_BASE_URL", "https://api.stepfun.com/v1").rstrip("/")


def get_stepfun_asr_model() -> str:
    return os.environ.get("STEPFUN_ASR_MODEL", "stepaudio-2.5-asr").strip() or "stepaudio-2.5-asr"


def get_openai_api_key() -> str:
    return os.environ.get("OPENAI_API_KEY", "").strip()


def get_openai_base_url() -> str:
    return os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")


def get_transcription_model() -> str:
    return os.environ.get("OPENAI_TRANSCRIPTION_MODEL", "gpt-4o-mini-transcribe").strip() or "gpt-4o-mini-transcribe"


def get_audio_max_bytes() -> int:
    raw_limit = os.environ.get("WHATSAPP_AUDIO_MAX_BYTES", "25000000")
    try:
        return max(int(raw_limit), 1)
    except ValueError:
        logger.warning("WHATSAPP_AUDIO_MAX_BYTES 無效，使用預設值 25000000")
        return 25_000_000


def get_transcription_timeout() -> int:
    raw_timeout = os.environ.get("OPENAI_TRANSCRIPTION_TIMEOUT", "60")
    try:
        return max(int(raw_timeout), 5)
    except ValueError:
        logger.warning("OPENAI_TRANSCRIPTION_TIMEOUT 無效，使用預設值 60")
        return 60


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


def parse_chinese_number(value: str) -> Optional[int]:
    """Parse small Chinese numerals used for child ages, e.g. 八, 十三, 十八."""
    return whatsapp_nlu.parse_chinese_number(value)


def age_to_group(age: float) -> str:
    return whatsapp_nlu.age_to_group(age)


def detect_child_age_group(text: str) -> Optional[str]:
    """從自然語句推測孩子年齡層，例如「小朋友1歲半」「孩子7歲」。"""
    groups = detect_child_age_groups(text)
    return groups[0] if groups else None


def detect_child_age_groups(text: str) -> List[str]:
    """從自然語句抓多個孩子年齡，例如「4歲和13歲」。"""
    return whatsapp_nlu.detect_child_age_groups(text)


def detect_age_groups(text: str) -> List[str]:
    """從文字中找出所有年齡層線索。"""
    text_lower = text.strip().lower().replace("岁", "歲")
    groups = detect_child_age_groups(text)

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


def detect_pain_points(text: str) -> List[Dict[str, str]]:
    """Detect parent pain points and map them to course topics."""
    return whatsapp_nlu.detect_pain_points(text)


def _contains_pain_keyword(text_lower: str, keyword: str) -> bool:
    keyword_lower = keyword.lower()
    if not keyword_lower:
        return False
    return keyword_lower in text_lower


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
        self._last_transcription_error: Dict[str, Any] = {}

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

    def _post_message_payload(self, payload: Dict[str, Any]) -> bool:
        if not self.access_token or not self.phone_number_id:
            logger.error("WhatsApp API 未配置")
            return False

        headers = {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }
        try:
            resp = requests.post(self.api_url, json=payload, headers=headers, timeout=30)
            if resp.status_code == 200:
                logger.info("WhatsApp 消息發送成功 -> %s", payload.get("to", ""))
                return True
            logger.warning("WhatsApp 消息發送失敗: %s %s", resp.status_code, resp.text)
            return False
        except Exception as e:
            logger.exception("WhatsApp 消息發送異常: %s", e)
            return False

    def _reply(self, to: str, text: str, source: str = "ai") -> bool:
        sent = self._send_text(to, text)
        if sent:
            self._memory.record_message(to, "outbound", source, text)
        return sent

    @staticmethod
    def _audio_suffix_from_mime(mime_type: str) -> str:
        mime = (mime_type or "").lower()
        if "mpeg" in mime or "mp3" in mime:
            return ".mp3"
        if "mp4" in mime:
            return ".mp4"
        if "mpga" in mime:
            return ".mpga"
        if "m4a" in mime:
            return ".m4a"
        if "wav" in mime:
            return ".wav"
        if "webm" in mime:
            return ".webm"
        if "ogg" in mime or "opus" in mime:
            return ".ogg"
        return ".bin"

    @staticmethod
    def _openai_upload_mime_for_suffix(suffix: str) -> str:
        return {
            ".mp3": "audio/mpeg",
            ".mp4": "audio/mp4",
            ".mpeg": "audio/mpeg",
            ".mpga": "audio/mpeg",
            ".m4a": "audio/mp4",
            ".wav": "audio/wav",
            ".webm": "audio/webm",
        }.get(suffix, "application/octet-stream")

    @staticmethod
    def _is_openai_supported_audio_suffix(suffix: str) -> bool:
        return suffix in {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm"}

    @staticmethod
    def _stepfun_audio_format_type(mime_type: str) -> str:
        mime = (mime_type or "").lower()
        if "ogg" in mime or "opus" in mime:
            return "ogg"
        if "mpeg" in mime or "mp3" in mime:
            return "mp3"
        if "wav" in mime:
            return "wav"
        return ""

    def _download_whatsapp_media(self, media_id: str) -> Optional[Dict[str, Any]]:
        if not media_id or not self.access_token:
            return None

        headers = {"Authorization": f"Bearer {self.access_token}"}
        try:
            meta_resp = requests.get(
                f"{get_graph_api_base()}/{media_id}",
                headers=headers,
                timeout=20,
            )
            if meta_resp.status_code != 200:
                logger.warning("WhatsApp media metadata 下載失敗: %s", meta_resp.status_code)
                return None
            meta = meta_resp.json()
            media_url = str(meta.get("url", "") or "")
            mime_type = str(meta.get("mime_type", "") or "")
            file_size = int(meta.get("file_size") or 0)
            if not media_url:
                logger.warning("WhatsApp media metadata 缺少 url")
                return None
            max_bytes = get_audio_max_bytes()
            if file_size and file_size > max_bytes:
                logger.warning("WhatsApp audio 超過大小限制: %s bytes", file_size)
                return None

            media_resp = requests.get(media_url, headers=headers, timeout=30)
            if media_resp.status_code != 200:
                logger.warning("WhatsApp media 下載失敗: %s", media_resp.status_code)
                return None
            content = media_resp.content or b""
            if not content:
                logger.warning("WhatsApp media 下載內容為空")
                return None
            if len(content) > max_bytes:
                logger.warning("WhatsApp audio 超過大小限制: %s bytes", len(content))
                return None
            if not mime_type:
                mime_type = str(media_resp.headers.get("Content-Type", "") or "")
            return {"content": content, "mime_type": mime_type}
        except Exception as exc:
            logger.warning("WhatsApp media 下載異常: %s", exc)
            return None

    def _prepare_audio_for_transcription(
        self,
        content: bytes,
        mime_type: str,
        tmpdir: str,
    ) -> Optional[Dict[str, str]]:
        suffix = self._audio_suffix_from_mime(mime_type)
        source_path = os.path.join(tmpdir, f"whatsapp-audio{suffix}")
        with open(source_path, "wb") as f:
            f.write(content)

        if self._is_openai_supported_audio_suffix(suffix):
            return {
                "path": source_path,
                "filename": f"whatsapp-audio{suffix}",
                "mime_type": self._openai_upload_mime_for_suffix(suffix),
            }

        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            logger.warning("WhatsApp audio 是 %s，但環境沒有 ffmpeg 可轉檔", mime_type or suffix)
            return None

        output_path = os.path.join(tmpdir, "whatsapp-audio.webm")
        try:
            subprocess.run(
                [
                    ffmpeg_path,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    source_path,
                    "-vn",
                    "-c:a",
                    "libopus",
                    output_path,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=30,
            )
        except Exception as exc:
            logger.warning("WhatsApp audio 轉檔失敗: %s", exc)
            return None

        return {
            "path": output_path,
            "filename": "whatsapp-audio.webm",
            "mime_type": "audio/webm",
        }

    def _parse_stepfun_sse_transcript(self, response: requests.Response) -> Optional[str]:
        deltas: List[str] = []
        last_error = ""
        for raw_line in response.iter_lines(decode_unicode=True):
            line = str(raw_line or "").strip()
            if not line or line.startswith(":"):
                continue
            if not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if not data or data == "[DONE]":
                continue
            try:
                event = json.loads(data)
            except json.JSONDecodeError:
                continue
            event_type = str(event.get("type", "") or "")
            if event_type == "transcript.text.done":
                transcript = str(event.get("text", "") or "").strip()
                if transcript:
                    return transcript
            if event_type == "transcript.text.delta":
                delta = str(event.get("delta", "") or "")
                if delta:
                    deltas.append(delta)
            if event_type == "error":
                last_error = str(event.get("message", "") or "")

        transcript = "".join(deltas).strip()
        if transcript:
            return transcript
        if last_error:
            self._last_transcription_error = {
                "provider": "stepfun",
                "error_code": "stepfun_sse_error",
                "message": last_error[:220],
            }
        return None

    def _transcribe_audio_bytes_stepfun(self, content: bytes, mime_type: str) -> Optional[str]:
        api_key = get_stepfun_api_key()
        if not api_key:
            logger.info("STEPFUN_API_KEY 未配置，略過 StepFun 語音轉文字")
            self._last_transcription_error = {
                "provider": "stepfun",
                "error_code": "missing_stepfun_api_key",
            }
            return None

        format_type = self._stepfun_audio_format_type(mime_type)
        audio_content = content
        if not format_type:
            with tempfile.TemporaryDirectory() as tmpdir:
                prepared = self._prepare_audio_for_stepfun(content, mime_type, tmpdir)
                if not prepared:
                    self._last_transcription_error = {
                        "provider": "stepfun",
                        "error_code": "audio_prepare_failed",
                    }
                    return None
                with open(prepared["path"], "rb") as f:
                    audio_content = f.read()
                format_type = str(prepared["format_type"])

        payload = {
            "audio": {
                "data": base64.b64encode(audio_content).decode("ascii"),
                "input": {
                    "transcription": {
                        "language": "zh",
                        "hotwords": ["澳門家長學堂", "家長學堂", "課程", "小朋友", "親子", "情緒", "學習"],
                        "model": get_stepfun_asr_model(),
                        "enable_itn": True,
                        "enable_timestamp": False,
                    },
                    "format": {
                        "type": format_type,
                    },
                },
            }
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        try:
            resp = requests.post(
                f"{get_stepfun_base_url()}/audio/asr/sse",
                headers=headers,
                json=payload,
                stream=True,
                timeout=get_transcription_timeout(),
            )
            if resp.status_code != 200:
                message = ""
                error_code = ""
                try:
                    error_payload = resp.json()
                    error = error_payload.get("error") or error_payload
                    message = str(error.get("message", "") or "")
                    error_code = str(error.get("code", "") or "")
                except Exception:
                    message = resp.text[:220]
                self._last_transcription_error = {
                    "provider": "stepfun",
                    "status_code": resp.status_code,
                    "error_code": error_code or f"http_{resp.status_code}",
                    "message": message[:220],
                }
                logger.warning(
                    "StepFun 語音轉文字失敗: status=%s code=%s",
                    resp.status_code,
                    error_code,
                )
                return None
            transcript = self._parse_stepfun_sse_transcript(resp)
            if not transcript and not self._last_transcription_error:
                self._last_transcription_error = {
                    "provider": "stepfun",
                    "error_code": "empty_transcript",
                }
            return transcript
        except Exception as exc:
            logger.warning("StepFun 語音轉文字異常: %s", exc)
            self._last_transcription_error = {
                "provider": "stepfun",
                "error_code": "transcription_exception",
                "error_type": exc.__class__.__name__,
            }
            return None

    def _prepare_audio_for_stepfun(
        self,
        content: bytes,
        mime_type: str,
        tmpdir: str,
    ) -> Optional[Dict[str, str]]:
        source_suffix = self._audio_suffix_from_mime(mime_type)
        source_path = os.path.join(tmpdir, f"whatsapp-audio{source_suffix}")
        with open(source_path, "wb") as f:
            f.write(content)

        ffmpeg_path = shutil.which("ffmpeg")
        if not ffmpeg_path:
            logger.warning("WhatsApp audio 是 %s，但環境沒有 ffmpeg 可轉成 StepFun 格式", mime_type or source_suffix)
            return None

        output_path = os.path.join(tmpdir, "whatsapp-audio.wav")
        try:
            subprocess.run(
                [
                    ffmpeg_path,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    source_path,
                    "-vn",
                    "-ac",
                    "1",
                    "-ar",
                    "16000",
                    output_path,
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=30,
            )
        except Exception as exc:
            logger.warning("WhatsApp audio 轉 StepFun wav 失敗: %s", exc)
            return None

        return {"path": output_path, "format_type": "wav"}

    def _transcribe_audio_bytes_openai(self, content: bytes, mime_type: str) -> Optional[str]:
        self._last_transcription_error = {}
        api_key = get_openai_api_key()
        if not api_key:
            logger.info("OPENAI_API_KEY 未配置，略過 WhatsApp 語音轉文字")
            self._last_transcription_error = {
                "provider": "openai",
                "error_code": "missing_openai_api_key",
            }
            return None

        with tempfile.TemporaryDirectory() as tmpdir:
            prepared = self._prepare_audio_for_transcription(content, mime_type, tmpdir)
            if not prepared:
                self._last_transcription_error = {"error_code": "audio_prepare_failed"}
                return None

            headers = {"Authorization": f"Bearer {api_key}"}
            data = {
                "model": get_transcription_model(),
                "response_format": "json",
                "language": "zh",
                "prompt": "這是一段家長用粵語或繁體中文詢問澳門家長學堂課程、孩子年齡、親子溝通、學習或情緒需要的語音。",
            }
            try:
                with open(prepared["path"], "rb") as f:
                    files = {
                        "file": (
                            prepared["filename"],
                            f,
                            prepared["mime_type"],
                        )
                    }
                    resp = requests.post(
                        f"{get_openai_base_url()}/audio/transcriptions",
                        headers=headers,
                        data=data,
                        files=files,
                        timeout=get_transcription_timeout(),
                    )
                if resp.status_code != 200:
                    error_type = ""
                    error_code = ""
                    try:
                        error = (resp.json().get("error") or {})
                        error_type = str(error.get("type", "") or "")
                        error_code = str(error.get("code", "") or "")
                    except Exception:
                        pass
                    self._last_transcription_error = {
                        "provider": "openai",
                        "status_code": resp.status_code,
                        "error_type": error_type,
                        "error_code": error_code,
                    }
                    logger.warning(
                        "OpenAI 語音轉文字失敗: status=%s type=%s code=%s",
                        resp.status_code,
                        error_type,
                        error_code,
                    )
                    return None
                result = resp.json()
                transcript = str(result.get("text", "") or "").strip()
                if not transcript:
                    logger.warning("OpenAI 語音轉文字回傳空白")
                    self._last_transcription_error = {
                        "provider": "openai",
                        "error_code": "empty_transcript",
                    }
                    return None
                return transcript
            except Exception as exc:
                logger.warning("OpenAI 語音轉文字異常: %s", exc)
                self._last_transcription_error = {
                    "provider": "openai",
                    "error_code": "transcription_exception",
                    "error_type": exc.__class__.__name__,
                }
                return None

    def _transcribe_audio_bytes(self, content: bytes, mime_type: str) -> Optional[str]:
        self._last_transcription_error = {}
        provider = get_transcription_provider()
        if provider == "stepfun":
            return self._transcribe_audio_bytes_stepfun(content, mime_type)
        if provider == "openai":
            return self._transcribe_audio_bytes_openai(content, mime_type)

        if get_stepfun_api_key():
            transcript = self._transcribe_audio_bytes_stepfun(content, mime_type)
            if transcript:
                return transcript
        return self._transcribe_audio_bytes_openai(content, mime_type)

    def _transcribe_audio_message(self, media_id: str, mime_type: str = "") -> Optional[str]:
        media = self._download_whatsapp_media(media_id)
        if not media:
            return None
        return self._transcribe_audio_bytes(
            bytes(media.get("content") or b""),
            str(media.get("mime_type") or mime_type or ""),
        )

    def _handle_non_text_message(self, from_number: str, msg: Dict[str, Any]) -> None:
        msg_type = str(msg.get("type", "") or "unknown")
        media = msg.get(msg_type, {}) if isinstance(msg.get(msg_type, {}), dict) else {}
        media_id = str(media.get("id", "") or "")
        mime_type = str(media.get("mime_type", "") or "")
        is_voice_note = msg_type == "audio" and bool(media.get("voice"))
        label = "語音訊息" if is_voice_note or msg_type == "audio" else f"非文字訊息：{msg_type}"
        meta = {
            "message_type": msg_type,
            "media_id": media_id,
            "voice": bool(media.get("voice")),
            "mime_type": mime_type,
        }
        self._memory.record_message(
            from_number,
            "inbound",
            "parent",
            f"[{label}]",
            meta=meta,
        )

        if is_voice_note or msg_type == "audio":
            if self._memory.is_human_takeover(from_number):
                logger.info("AI 已暫停，自動略過 WhatsApp 語音 from=%s", from_number)
                return

            transcript = self._transcribe_audio_message(media_id, mime_type)
            if transcript:
                self._memory.record_message(
                    from_number,
                    "inbound",
                    "parent",
                    transcript,
                    meta={
                        "message_type": "audio_transcription",
                        "media_id": media_id,
                        "original_mime_type": mime_type,
                    },
                )
                self._handle_text_message(from_number, transcript, record_inbound=False)
                return

            transcription_error = dict(self._last_transcription_error or {})
            flag_meta = dict(meta)
            if transcription_error:
                flag_meta["transcription_error"] = transcription_error
            if transcription_error.get("error_code") == "insufficient_quota":
                flag_summary = "家長傳來語音訊息；OpenAI 語音轉文字 quota 不足，需要補 API 額度或人工跟進。"
            else:
                flag_summary = "家長傳來語音訊息；語音轉文字未能完成，需要請家長改用文字或人工跟進。"
            self._memory.add_agent_flag(
                from_number,
                "handoff_needed",
                flag_summary,
                flag_meta,
            )
            self._reply(
                from_number,
                "我收到你的語音訊息，但我暫時未能直接聽錄音。\n\n"
                "你可以用手機鍵盤的咪高峰 *語音輸入成文字* 再傳送；"
                "或直接打一句，例如：\n"
                "• *小朋友13歲，想找情緒壓力課*\n"
                "• *小朋友4歲，想親子活動*\n\n"
                "如果你已經是語音轉文字發送，我就會正常理解。",
            )
            return

        self._memory.add_agent_flag(
            from_number,
            "uncertain",
            "家長傳來非文字訊息，目前只能處理文字查詢。",
            meta,
        )
        self._reply(
            from_number,
            "我目前主要支援文字查詢課程。\n"
            "請直接傳：*小朋友幾歲，想找哪類課程*，我會幫你配對。",
        )

    def send_admin_message(self, to: str, text: str) -> bool:
        """Send an operator-authored WhatsApp message and keep the transcript."""
        message = text.strip()
        if not message:
            return False
        return self._reply(to, message, source="admin")

    def send_template_message(
        self,
        to: str,
        template_name: str,
        language_code: str,
        body_parameters: Optional[List[str]] = None,
        transcript_body: str = "",
    ) -> bool:
        """Send an approved WhatsApp template and record the operator transcript."""
        template = {
            "name": template_name.strip(),
            "language": {"code": (language_code or "zh_HK").strip() or "zh_HK"},
        }
        parameters = [
            {"type": "text", "text": str(value)}
            for value in (body_parameters or [])
            if str(value)
        ]
        if parameters:
            template["components"] = [
                {
                    "type": "body",
                    "parameters": parameters,
                }
            ]

        payload = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "template",
            "template": template,
        }
        sent = self._post_message_payload(payload)
        if sent:
            self._memory.record_message(
                to,
                "outbound",
                "admin",
                transcript_body or f"[Template] {template_name}",
                {
                    "message_type": "template",
                    "template_name": template_name,
                    "template_language": language_code,
                    "body_parameters": body_parameters or [],
                },
            )
        return sent

    def is_within_customer_service_window(self, phone: str, hours: int = 24) -> bool:
        messages = self._memory.get_messages(phone, limit=300)
        for message in reversed(messages):
            if message.get("direction") != "inbound":
                continue
            try:
                created_at = datetime.fromisoformat(str(message.get("created_at", "")))
            except ValueError:
                continue
            return (datetime.now() - created_at).total_seconds() <= hours * 3600
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

    def _course_reply_url(self, course: Any) -> str:
        registration_url = self._course_value(course, "registration_url")
        if registration_url:
            return registration_url
        return normalize_course_detail_url(self._course_value(course, "detail_url"))

    def _course_matching_text(self, course: Any) -> str:
        parts = [
            self._course_value(course, "name"),
            self._course_value(course, "topic"),
            self._course_value(course, "target"),
            self._course_value(course, "summary"),
        ]
        return " ".join(part for part in parts if part).lower()

    @staticmethod
    def _pain_rule(tag: str) -> Dict[str, Any]:
        for rule in PAIN_POINT_RULES:
            if str(rule["tag"]) == str(tag):
                return rule
        return {}

    def _course_pain_reasons(self, course: Any, pain_points: List[str]) -> List[str]:
        if not pain_points:
            return []

        course_topic = self._course_value(course, "topic")
        haystack = self._course_matching_text(course)
        reasons: List[str] = []
        for tag in pain_points:
            rule = self._pain_rule(str(tag))
            if not rule:
                continue
            topic = str(rule.get("topic", ""))
            keywords = [str(k) for k in rule.get("keywords", [])]
            matched_by_outline = any(_contains_pain_keyword(haystack, keyword) for keyword in keywords)
            if matched_by_outline:
                reasons.append(f"大綱回應「{tag}」")
            elif topic and course_topic == topic:
                reasons.append(f"主題回應「{tag}」")
        return reasons

    @staticmethod
    def _method_accepts(method: Any, parameter: str) -> bool:
        try:
            signature = inspect.signature(method)
        except (TypeError, ValueError):
            return False
        return (
            parameter in signature.parameters
            or any(p.kind == inspect.Parameter.VAR_KEYWORD for p in signature.parameters.values())
        )

    def _call_course_method(
        self,
        method: Any,
        include_details: bool = False,
        **kwargs: Any,
    ) -> List[Any]:
        call_kwargs = dict(kwargs)
        if include_details and self._method_accepts(method, "include_details"):
            call_kwargs["include_details"] = True
        return method(**call_kwargs)

    @staticmethod
    def _parse_page_request(text: str) -> Optional[int]:
        normalized = WhatsAppHandler._normalize_command(text)
        if normalized in NEXT_PAGE_KEYWORDS:
            return -1
        match = re.search(r"(?:第)?(\d+)(?:頁|页|page)?", normalized)
        if match and ("頁" in normalized or "页" in normalized or "page" in normalized):
            return max(int(match.group(1)), 1)
        return None

    @staticmethod
    def _normalize_command(text: str) -> str:
        return re.sub(r"[\s\?？!！。,.、，；;:：]+", "", text.strip().lower())

    @staticmethod
    def _detect_proactive_consent_status(text: str) -> str:
        normalized = WhatsAppHandler._normalize_command(text)
        if not normalized:
            return ""
        if any(keyword in normalized for keyword in PROACTIVE_DENY_KEYWORDS):
            return "paused"
        if any(keyword in normalized for keyword in PROACTIVE_PAUSE_KEYWORDS):
            return "paused"
        if any(keyword in normalized for keyword in PROACTIVE_ALLOW_KEYWORDS):
            return "allowed"
        return ""

    @staticmethod
    def _proactive_consent_text(status: str) -> str:
        if status == "allowed":
            return (
                "已記住：你同意接收 *主動課程提醒*。\n\n"
                "之後如果有貼近孩子年齡和你關心痛點的家長學堂課程，"
                "我可以幫你留意。\n"
                "任何時候回覆 *暫停推送*，就會停止主動提醒。"
            )
        return (
            "已記住：暫停主動課程提醒。\n\n"
            "我仍然可以即時幫你查課程；之後想恢復，可以回覆 *同意推送*。"
        )

    @staticmethod
    def _parse_detail_request(text: str) -> Optional[int]:
        normalized = text.strip().lower().replace(" ", "")
        match = re.fullmatch(r"(?:詳情|詳細|detail|link|連結)(\d+)", normalized)
        if match:
            return int(match.group(1))
        return None

    @staticmethod
    def _is_course_intent(text: str) -> bool:
        normalized = WhatsAppHandler._normalize_command(text)
        if normalized in BARE_RECOMMENDATION_COMMANDS:
            return True

        text_lower = text.strip().lower()
        has_domain = any(k in text_lower for k in COURSE_DOMAIN_KEYWORDS)
        has_intent = any(k in text_lower for k in COURSE_INTENT_KEYWORDS)
        return has_domain and has_intent

    @staticmethod
    def _is_off_topic_request(text: str) -> bool:
        text_lower = text.strip().lower()
        return any(k in text_lower for k in OFF_TOPIC_KEYWORDS)

    @staticmethod
    def _has_course_signal(text: str) -> bool:
        normalized = WhatsAppHandler._normalize_command(text)
        if not normalized:
            return False
        if (
            normalized in RESET_KEYWORDS
            or normalized in PROFILE_KEYWORDS
            or normalized in NEXT_PAGE_KEYWORDS
            or normalized in ALL_COURSE_KEYWORDS
            or normalized in BARE_RECOMMENDATION_COMMANDS
            or WhatsAppHandler._parse_detail_request(text) is not None
        ):
            return True
        if detect_age_groups(text):
            return True
        if any(target in text for target in TARGETS):
            return True
        if any(topic in text for topic in TOPICS):
            return True
        if any(k in text.strip().lower() for k in ["報名", "报名", "課程", "课程", "家長學堂", "家长学堂"]):
            return True
        if WhatsAppHandler._has_parent_pain_signal(text):
            return True
        return WhatsAppHandler._is_course_intent(text)

    @staticmethod
    def _has_parent_pain_signal(text: str) -> bool:
        text_stripped = text.strip()
        if not detect_pain_points(text_stripped):
            return False
        text_lower = text_stripped.lower()
        has_parent_context = any(k in text_lower for k in PARENT_CONTEXT_KEYWORDS)
        return has_parent_context or len(text_stripped) <= 80

    @staticmethod
    def _is_out_of_scope_request(text: str) -> bool:
        text_stripped = text.strip()
        if not text_stripped or WhatsAppHandler._has_course_signal(text_stripped):
            return False

        text_lower = text_stripped.lower()
        if any(k in text_lower for k in OUT_OF_SCOPE_AI_KEYWORDS):
            return True
        return len(text_stripped) >= LONG_MESSAGE_OFF_TOPIC_CHARS

    @staticmethod
    def _off_topic_text() -> str:
        return (
            "我目前只協助查詢和推薦 *澳門家長學堂課程*，"
            "不會回答餐廳、天氣、投資、功課或其他無關問題。\n\n"
            "你可以回覆：*小朋友13歲，想家長課*、*青少年課程*、*更多*。"
        )

    @staticmethod
    def _unknown_text() -> str:
        return (
            "🤔 這句我未能轉成課程條件。\n\n"
            "我只處理 *澳門家長學堂課程* 查詢。你可以直接發：\n"
            "• *小朋友1歲，想親子活動*\n"
            "• *青少年家長課*\n"
            "• *全部課程*\n"
            "• *更多*"
        )

    @staticmethod
    def _repair_reply_links(text: str) -> str:
        """Repair DSEDJ links that an LLM or renderer may have entity-decoded."""
        if not text:
            return ""

        trailing_punctuation = "。．，、；;：:！!？?）)]"

        def repair(match: re.Match) -> str:
            raw_url = match.group(0)
            suffix = ""
            while raw_url and raw_url[-1] in trailing_punctuation:
                suffix = raw_url[-1] + suffix
                raw_url = raw_url[:-1]
            return normalize_course_detail_url(raw_url) + suffix

        return re.sub(r"https://portal\.dsedj\.gov\.mo[^\s<>]*", repair, text)

    @staticmethod
    def _strip_url_trailing_punctuation(url: str) -> str:
        trailing_punctuation = "。．，、；;：:！!？?）)]"
        cleaned = str(url or "").strip()
        while cleaned and cleaned[-1] in trailing_punctuation:
            cleaned = cleaned[:-1]
        return cleaned

    @classmethod
    def _extract_urls(cls, text: str) -> List[str]:
        return [
            cls._strip_url_trailing_punctuation(match.group(0))
            for match in re.finditer(r"https?://[^\s<>]+", text or "")
        ]

    @classmethod
    def _canonical_reply_url(cls, url: str) -> str:
        cleaned = cls._strip_url_trailing_punctuation(url)
        if "portal.dsedj.gov.mo" in cleaned:
            return normalize_course_detail_url(cleaned)
        return cleaned

    @classmethod
    def _candidate_url_allowlist(cls, candidate_payload: List[Dict[str, Any]]) -> set[str]:
        allowed: set[str] = set()
        for candidate in candidate_payload:
            for key in ("reply_url", "registration_url", "detail_url"):
                url = str(candidate.get(key, "") or "")
                if url:
                    allowed.add(cls._canonical_reply_url(url))
        return allowed

    @classmethod
    def _llm_reply_uses_only_candidate_urls(
        cls,
        reply: str,
        candidate_payload: List[Dict[str, Any]],
    ) -> bool:
        urls = cls._extract_urls(reply)
        if not urls:
            return True
        allowed = cls._candidate_url_allowlist(candidate_payload)
        return all(cls._canonical_reply_url(url) in allowed for url in urls)

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

    def _fetch_courses(
        self,
        course_source: Any,
        age_group: Any = "",
        include_details: bool = False,
    ) -> List[Any]:
        age_groups = self._normalize_age_groups(age_group)
        if hasattr(course_source, "fetch_courses"):
            if age_groups:
                courses = []
                for age in age_groups:
                    courses.extend(self._call_course_method(
                        course_source.fetch_courses,
                        include_details=include_details,
                        age_group=age,
                        status="",
                        max_retries=2,
                        delay=1.0,
                    ))
                return self._dedupe_courses(courses)

            courses = []
            for age in AGE_GROUP_LABELS:
                try:
                    courses.extend(self._call_course_method(
                        course_source.fetch_courses,
                        include_details=include_details,
                        age_group=age,
                        status="",
                        max_retries=2,
                        delay=1.0,
                    ))
                except Exception as e:
                    logger.warning("抓取 %s 課程失敗: %s", age, e)
            if courses:
                return self._dedupe_courses(courses)

        return self._call_course_method(
            course_source.fetch_all_open_courses,
            include_details=include_details,
            max_retries=2,
            delay=1.0,
        )

    def _load_profile(self, from_number: str) -> Dict[str, Any]:
        if from_number not in self._profiles:
            self._profiles[from_number] = self._memory.get_profile(from_number)
        return dict(self._profiles.get(from_number, {}))

    def _save_profile(self, from_number: str, profile: Dict[str, Any]) -> None:
        self._profiles[from_number] = dict(profile)
        self._memory.save_profile(from_number, profile)

    @staticmethod
    def admin_profile_options() -> Dict[str, List[str]]:
        return {
            "age_groups": list(AGE_GROUP_LABELS.keys()),
            "pain_points": [str(rule["tag"]) for rule in PAIN_POINT_RULES],
            "topics": list(TOPICS),
            "targets": list(TARGETS),
        }

    def normalize_admin_profile(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        options = self.admin_profile_options()
        age_groups = [
            str(age).strip()
            for age in profile.get("age_groups", [])
            if str(age).strip()
        ]
        pain_points = [
            str(pain).strip()
            for pain in profile.get("pain_points", [])
            if str(pain).strip()
        ]
        target = str(profile.get("target", "") or "").strip()
        topic = str(profile.get("topic", "") or "").strip()
        pain_summary = str(profile.get("pain_summary", "") or "").strip()

        invalid_age_groups = [age for age in age_groups if age not in options["age_groups"]]
        invalid_pain_points = [pain for pain in pain_points if pain not in options["pain_points"]]
        if invalid_age_groups:
            raise ValueError(f"Unsupported age group: {', '.join(invalid_age_groups)}")
        if invalid_pain_points:
            raise ValueError(f"Unsupported pain point: {', '.join(invalid_pain_points)}")
        if target and target not in options["targets"]:
            raise ValueError(f"Unsupported target: {target}")
        if topic and topic not in options["topics"]:
            raise ValueError(f"Unsupported topic: {topic}")

        normalized: Dict[str, Any] = {}
        if age_groups:
            normalized["age_groups"] = age_groups[:4]
            normalized["age_group"] = age_groups[0]
        if pain_points:
            normalized["pain_points"] = pain_points[:8]
        if pain_summary:
            normalized["pain_summary"] = pain_summary[:180]
        if target:
            normalized["target"] = target
        if topic:
            normalized["topic"] = topic
            normalized["topic_source"] = "admin"
        return normalized

    def update_profile_from_admin(
        self,
        from_number: str,
        raw_profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        profile = self.normalize_admin_profile(raw_profile)
        self._save_profile(from_number, profile)
        self._sync_onboarding_conversation_meta(from_number, profile)
        return profile

    def _update_profile_from_text(self, from_number: str, text: str) -> Dict[str, Any]:
        profile = self._load_profile(from_number)
        age_groups = detect_age_groups(text)
        target = self._detect_positive_option(text, TARGETS)
        topic = self._detect_positive_option(text, TOPICS)
        pain_points = detect_pain_points(text)
        negative_targets = [t for t in TARGETS if self._mentions_negative(text, t)]
        negative_topics = [t for t in TOPICS if self._mentions_negative(text, t)]

        if age_groups:
            profile["age_groups"] = age_groups
            profile["age_group"] = age_groups[0]
        if target:
            profile["target"] = target
        elif profile.get("target") in negative_targets:
            profile.pop("target", None)
        if topic:
            profile["topic"] = topic
            profile["topic_source"] = "explicit"
        elif profile.get("topic") in negative_topics:
            profile.pop("topic", None)
            profile.pop("topic_source", None)
        if pain_points:
            existing = [str(p) for p in profile.get("pain_points", []) if p]
            for pain in pain_points:
                if pain["tag"] not in existing:
                    existing.append(pain["tag"])
            profile["pain_points"] = existing[:8]
            profile["pain_summary"] = text.strip()[:180]
            if not profile.get("topic"):
                profile["topic"] = pain_points[0]["topic"]
                profile["topic_source"] = "pain"
            self._memory.add_conversation_tags(from_number, [p["tag"] for p in pain_points])
        self._profiles[from_number] = profile
        self._memory.save_profile(from_number, profile)
        return profile

    @staticmethod
    def _profile_has_signal(profile: Dict[str, Any]) -> bool:
        return any(profile.get(k) for k in ["age_group", "age_groups", "target", "topic", "pain_points"])

    def _profile_ready_for_recommendation(self, profile: Dict[str, Any]) -> bool:
        if not self._profile_age_groups(profile):
            return False
        return bool(
            profile.get("pain_points")
            or profile.get("target")
            or profile.get("topic")
        )

    @staticmethod
    def _topic_for_exact_filter(profile: Dict[str, Any]) -> str:
        if profile.get("topic_source") == "pain":
            return ""
        return str(profile.get("topic", ""))

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
        if profile.get("pain_points"):
            parts.append("痛點：" + "、".join(profile["pain_points"][:3]))
        return "我會先按「" + " / ".join(parts) + "」幫你縮窄。"

    def _profile_tag_labels(self, profile: Dict[str, Any]) -> List[str]:
        tags: List[str] = []
        age_tag_labels = {
            "0-2歲": "嬰幼",
            "3-6歲": "幼兒",
            "7-12歲": "兒童",
            "13-18歲": "青少年",
        }
        for age_group in self._profile_age_groups(profile):
            label = age_tag_labels.get(age_group, AGE_GROUP_LABELS.get(age_group, age_group))
            if label and label not in tags:
                tags.append(label)
        for pain in [str(p) for p in profile.get("pain_points", []) if p]:
            if pain not in tags:
                tags.append(pain)
        if profile.get("target") and str(profile["target"]) not in tags:
            tags.append(str(profile["target"]))
        if profile.get("topic") and str(profile["topic"]) not in tags:
            tags.append(str(profile["topic"]))
        return tags[:8]

    def _onboarding_note_text(self, profile: Dict[str, Any]) -> str:
        parts: List[str] = []
        age_groups = self._profile_age_groups(profile)
        if age_groups:
            parts.append("、".join(age_groups))
        if profile.get("pain_points"):
            parts.append("、".join([str(p) for p in profile.get("pain_points", []) if p][:3]))
        elif profile.get("topic"):
            parts.append(str(profile["topic"]))
        if profile.get("target"):
            parts.append(str(profile["target"]))
        return "onboarding: " + " / ".join([p for p in parts if p])

    def _merge_onboarding_note(self, existing_notes: str, onboarding_note: str) -> str:
        if not onboarding_note or onboarding_note == "onboarding: ":
            return existing_notes

        machine_note = f"{ONBOARDING_NOTE_MARKER} {onboarding_note}"
        existing = str(existing_notes or "")
        if not existing:
            return machine_note

        lines = existing.splitlines(keepends=True)
        for index, line in enumerate(lines):
            if line.lstrip().startswith(ONBOARDING_NOTE_MARKER):
                newline = "\n" if line.endswith("\n") else ""
                lines[index] = machine_note + newline
                return "".join(lines)

        separator = "" if existing.endswith("\n") else "\n"
        return existing + separator + machine_note

    def _sync_onboarding_conversation_meta(
        self,
        from_number: str,
        profile: Dict[str, Any],
    ) -> None:
        if not self._profile_has_signal(profile):
            return

        conversation = self._memory.get_conversation(from_number)
        existing_tags = [str(tag) for tag in conversation.get("tags", []) if tag]
        tags = existing_tags[:]
        for tag in self._profile_tag_labels(profile):
            if tag not in tags:
                tags.append(tag)

        note = self._onboarding_note_text(profile)
        notes = self._merge_onboarding_note(str(conversation.get("notes", "")), note)
        kwargs: Dict[str, Any] = {}
        if tags:
            kwargs["tags"] = tags
        if notes:
            kwargs["notes"] = notes
        if kwargs:
            self._memory.update_conversation(from_number, **kwargs)

    def _onboarding_text(self, profile: Dict[str, Any]) -> str:
        if not self._profile_has_signal(profile):
            return ONBOARDING_QUESTION

        has_age = bool(self._profile_age_groups(profile))
        has_concern = bool(
            profile.get("pain_points")
            or profile.get("topic")
            or profile.get("target")
        )
        if has_age and not has_concern:
            return ONBOARDING_CONCERN_QUESTION
        if has_concern and not has_age:
            return ONBOARDING_AGE_QUESTION
        return ONBOARDING_QUESTION

    def _get_courses_text(
        self,
        from_number: str,
        age_group: Any = "",
        target: str = "",
        topic: str = "",
        page: int = 1,
        agentic: bool = False,
        profile: Optional[Dict[str, Any]] = None,
    ) -> str:
        """獲取課程列表文字"""
        course_source = self._get_course_source()
        if not course_source:
            return "課程資料暫時無法取得，請稍後再試。"

        try:
            available_courses = self._fetch_courses(
                course_source,
                age_group=age_group,
                include_details=agentic,
            )
            topic_filter = topic
            if profile and profile.get("topic_source") == "pain":
                topic_filter = ""
            courses = self._filter_courses(available_courses, age_group, target, topic_filter)
            if agentic and profile:
                ranked = self._rank_courses_for_profile(
                    courses,
                    profile,
                    require_pain_match=bool(profile.get("pain_points")),
                )
                if ranked or profile.get("pain_points"):
                    courses = ranked

            if not courses:
                self._memory.add_agent_flag(
                    from_number,
                    "no_match",
                    "課程查詢沒有找到符合條件的報名中課程",
                    {"age_group": age_group, "target": target, "topic": topic},
                )
                filter_profile = profile or {
                    "age_groups": self._normalize_age_groups(age_group),
                    "target": target,
                    "topic": topic,
                }
                return self._no_match_text(filter_profile, available_courses)

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
                link = self._course_reply_url(c)
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
                    if profile and profile.get("pain_points"):
                        reason_bits.extend(self._course_pain_reasons(
                            c,
                            [str(p) for p in profile.get("pain_points", []) if p],
                        )[:2])
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

    def _course_summary_for_llm(self, course: Any, number: int) -> Dict[str, Any]:
        return {
            "number": str(number),
            "name": self._course_value(course, "name"),
            "date": self._course_value(course, "date"),
            "age_group": self._course_value(course, "age_group"),
            "age_groups": self._course_values(course, "age_groups"),
            "topic": self._course_value(course, "topic"),
            "target": self._course_value(course, "target"),
            "status": self._course_value(course, "status"),
            "summary": self._course_value(course, "summary"),
            "registration_url": self._course_value(course, "registration_url"),
            "reply_url": self._course_reply_url(course),
            "detail_url": normalize_course_detail_url(self._course_value(course, "detail_url")),
        }

    @staticmethod
    def _pain_topics_from_profile(profile: Dict[str, Any]) -> List[str]:
        pain_points = {str(p) for p in profile.get("pain_points", []) if p}
        topics: List[str] = []
        for rule in PAIN_POINT_RULES:
            if str(rule["tag"]) in pain_points and str(rule["topic"]) not in topics:
                topics.append(str(rule["topic"]))
        return topics

    def _score_course_for_profile(
        self,
        course: Any,
        profile: Dict[str, Any],
    ) -> tuple[int, List[str]]:
        score = 0
        reasons: List[str] = []
        age_groups = self._profile_age_groups(profile)
        course_age_groups = self._course_values(course, "age_groups") or [self._course_value(course, "age_group")]
        if age_groups and any(age in course_age_groups for age in age_groups):
            score += 4
            reasons.append("孩子年齡吻合")

        course_topic = self._course_value(course, "topic")
        profile_topic = str(profile.get("topic", ""))
        pain_reasons = self._course_pain_reasons(course, [
            str(p) for p in profile.get("pain_points", []) if p
        ])
        if pain_reasons:
            score += 4
            reasons.extend(pain_reasons[:2])
        elif profile_topic and course_topic == profile_topic:
            score += 3
            reasons.append(f"主題符合「{profile_topic}」")

        profile_target = str(profile.get("target", ""))
        if profile_target and self._course_value(course, "target") == profile_target:
            score += 2
            reasons.append(f"對象是{profile_target}")

        return score, reasons

    def _rank_courses_for_profile(
        self,
        courses: List[Any],
        profile: Dict[str, Any],
        require_pain_match: bool = False,
    ) -> List[Any]:
        scored = []
        pain_points = [str(p) for p in profile.get("pain_points", []) if p]
        for course in courses:
            pain_reasons = self._course_pain_reasons(course, pain_points)
            if require_pain_match and not pain_reasons:
                continue
            score, reasons = self._score_course_for_profile(course, profile)
            if score <= 0:
                continue
            scored.append((score, len(pain_reasons), reasons, course))
        scored.sort(key=lambda item: (item[0], item[1]), reverse=True)
        return [course for _, _, _, course in scored]

    def get_proactive_matches(
        self,
        parent_limit: int = 100,
        courses_per_parent: int = 3,
        allowed_only: bool = False,
    ) -> List[Dict[str, Any]]:
        """Draft proactive course matches from stored parent memories."""
        course_source = self._get_course_source()
        if not course_source:
            return []

        courses = self._fetch_courses(course_source, include_details=True)
        parents = self._memory.iter_parent_profiles(limit=parent_limit)
        results: List[Dict[str, Any]] = []
        for parent in parents:
            conversation = parent.get("conversation", {})
            if allowed_only and conversation.get("consent_status") != "allowed":
                continue
            profile = parent.get("profile", {})
            if not self._profile_has_signal(profile):
                continue
            scored = []
            for course in courses:
                if profile.get("pain_points") and not self._course_pain_reasons(
                    course,
                    [str(p) for p in profile.get("pain_points", []) if p],
                ):
                    continue
                score, reasons = self._score_course_for_profile(course, profile)
                if score <= 0:
                    continue
                scored.append((score, reasons, course))
            scored.sort(key=lambda item: item[0], reverse=True)
            matches = [
                {
                    "score": score,
                    "reasons": reasons,
                    "course": self._course_summary_for_llm(course, index),
                }
                for index, (score, reasons, course)
                in enumerate(scored[:courses_per_parent], 1)
            ]
            if matches:
                results.append({
                    "phone": parent["phone"],
                    "conversation": conversation,
                    "profile": profile,
                    "matches": matches,
                    "draft_text": self._draft_proactive_match_text(profile, matches),
                })
        return results

    def _draft_proactive_match_text(
        self,
        profile: Dict[str, Any],
        matches: List[Dict[str, Any]],
    ) -> str:
        """Build an operator-editable proactive WhatsApp draft."""
        if not matches:
            return ""

        intro = "我看到有幾個可能貼近你情況的家長學堂課程，先幫你挑少量重點："
        pain_points = "、".join([str(p) for p in profile.get("pain_points", []) if p][:2])
        if pain_points:
            intro = f"你之前提到「{pain_points}」，我看到有幾個可能貼近的家長學堂課程："

        lines = [intro]
        for index, match in enumerate(matches[:2], 1):
            course = match.get("course", {})
            reasons = "、".join(match.get("reasons", [])[:2])
            title = str(course.get("name", "未命名課程"))
            date_text = str(course.get("date", ""))
            link = str(course.get("reply_url") or course.get("registration_url") or course.get("detail_url") or "")
            lines.append(f"\n{index}. {title}")
            if date_text:
                lines.append(f"日期：{date_text}")
            if reasons:
                lines.append(f"原因：{reasons}")
            if link:
                lines.append(f"報名連結：{link}")
        lines.append("\n如果不想再收到這類主動提醒，可以直接回覆：暫停推送。")
        return "\n".join(lines)

    def _llm_cache_key(
        self,
        user_text: str,
        profile: Dict[str, Any],
        candidate_payload: List[Dict[str, Any]],
    ) -> str:
        payload = {
            "v": 1,
            "model": get_deepseek_model(),
            "message": self._normalize_command(user_text),
            "profile": {
                "age_groups": self._profile_age_groups(profile),
                "target": profile.get("target", ""),
                "topic": profile.get("topic", ""),
                "pain_points": profile.get("pain_points", []),
            },
            "candidates": candidate_payload,
        }
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

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

    @staticmethod
    def _extract_json_object(text: str) -> Dict[str, Any]:
        raw = str(text or "").strip()
        if not raw:
            return {}
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
        try:
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError:
            pass
        match = re.search(r"\{.*\}", raw, re.S)
        if not match:
            return {}
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _consume_llm_quota(self, from_number: str) -> bool:
        daily_limit = get_deepseek_daily_limit_per_user()
        global_limit = get_deepseek_daily_limit_global()
        consumed = self._memory.try_consume_llm_quotas(
            from_number,
            per_user_daily_limit=daily_limit,
            global_daily_limit=global_limit,
        )
        if not consumed:
            logger.info(
                "DeepSeek 每日限額已達 from=%s usage=%s limit=%s global_usage=%s global_limit=%s",
                from_number,
                self._memory.get_llm_usage_count(from_number),
                daily_limit,
                self._memory.get_llm_usage_count("__global__"),
                global_limit,
            )
        return consumed

    @staticmethod
    def _looks_like_onboarding_reply(text: str, profile: Dict[str, Any]) -> bool:
        normalized = WhatsAppHandler._normalize_command(text)
        if not normalized:
            return False
        if len(normalized) > LLM_PROFILE_EXTRACTION_MAX_CHARS:
            return False
        if normalized in {"ok", "okay", "好", "好的", "收到", "謝謝", "谢谢", "thanks", "thank you"}:
            return False
        if any(ch.isdigit() for ch in normalized):
            return True
        if re.search(r"[零〇一二兩两三四五六七八九十]{1,3}\s*歲", normalized):
            return True
        if any(k in normalized for k in ["歲", "岁", "小朋友", "孩子", "仔", "女", "情緒", "學習", "升學", "親子", "溝通", "壓力", "搵", "找"]):
            return True
        return bool(profile and WhatsAppHandler._profile_has_signal(profile))

    def _should_attempt_llm_profile_extraction(self, text: str, profile: Dict[str, Any]) -> bool:
        if not get_deepseek_api_key():
            return False
        if self._is_off_topic_request(text) or self._is_out_of_scope_request(text):
            return False
        if not self._looks_like_onboarding_reply(text, profile):
            return False
        return True

    def _llm_extract_profile_update(
        self,
        from_number: str,
        text: str,
        profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        if not self._should_attempt_llm_profile_extraction(text, profile):
            return {}
        if not self._consume_llm_quota(from_number):
            return {}

        options = self.admin_profile_options()
        messages = [
            {
                "role": "system",
                "content": (
                    "你是澳門家長學堂 WhatsApp bot 的語意抽取器，只能輸出 JSON。"
                    "你的任務不是聊天，而是把家長自然語句轉成可用 profile。"
                    "只抽取與孩子年齡、家長痛點、課程主題、對象有關的資訊。"
                    "如果是餐廳、天氣、投資、功課、翻譯、寫程式等無關問題，"
                    "回傳 is_course_related=false。"
                    "可接受粵語、國語、英文混合，例如 '8 and 6' 代表孩子 8 歲和 6 歲。"
                    "age_groups 只能用：0-2歲, 3-6歲, 7-12歲, 13-18歲。"
                    "pain_points、topic、target 只能使用用戶提供選項。"
                    "不要創造其他值。不要輸出 markdown。"
                ),
            },
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "家長訊息": text,
                        "目前已知profile": profile,
                        "可選age_groups": options["age_groups"],
                        "可選pain_points": options["pain_points"],
                        "可選topics": options["topics"],
                        "可選targets": options["targets"],
                        "輸出JSON格式": {
                            "is_course_related": True,
                            "age_groups": [],
                            "pain_points": [],
                            "topic": "",
                            "target": "",
                            "pain_summary": "",
                            "confidence": 0.0,
                        },
                    },
                    ensure_ascii=False,
                ),
            },
        ]
        raw = self._call_deepseek(messages, max_tokens=320)
        payload = self._extract_json_object(raw or "")
        if not payload or payload.get("is_course_related") is False:
            return {}

        valid_age_groups = set(options["age_groups"])
        valid_pain_points = set(options["pain_points"])
        valid_topics = set(options["topics"])
        valid_targets = set(options["targets"])
        extracted: Dict[str, Any] = {}
        age_groups = [
            str(age).strip()
            for age in payload.get("age_groups", [])
            if str(age).strip() in valid_age_groups
        ]
        pain_points = [
            str(pain).strip()
            for pain in payload.get("pain_points", [])
            if str(pain).strip() in valid_pain_points
        ]
        topic = str(payload.get("topic", "") or "").strip()
        target = str(payload.get("target", "") or "").strip()
        pain_summary = str(payload.get("pain_summary", "") or "").strip()

        if age_groups:
            extracted["age_groups"] = age_groups[:4]
            extracted["age_group"] = age_groups[0]
        if pain_points:
            extracted["pain_points"] = pain_points[:8]
        if topic in valid_topics:
            extracted["topic"] = topic
            extracted["topic_source"] = "llm"
        if target in valid_targets:
            extracted["target"] = target
        if pain_summary:
            extracted["pain_summary"] = pain_summary[:180]
        return extracted

    def _update_profile_from_llm_text(
        self,
        from_number: str,
        text: str,
        profile: Dict[str, Any],
    ) -> Dict[str, Any]:
        extracted = self._llm_extract_profile_update(from_number, text, profile)
        if not extracted:
            return profile

        updated = dict(profile)
        if extracted.get("age_groups"):
            updated["age_groups"] = extracted["age_groups"]
            updated["age_group"] = extracted["age_groups"][0]
        if extracted.get("target"):
            updated["target"] = extracted["target"]
        if extracted.get("topic"):
            updated["topic"] = extracted["topic"]
            updated["topic_source"] = extracted.get("topic_source", "llm")
        if extracted.get("pain_points"):
            existing = [str(p) for p in updated.get("pain_points", []) if p]
            for pain in extracted["pain_points"]:
                if pain not in existing:
                    existing.append(pain)
            updated["pain_points"] = existing[:8]
            self._memory.add_conversation_tags(from_number, extracted["pain_points"])
        if extracted.get("pain_summary"):
            updated["pain_summary"] = extracted["pain_summary"]

        self._save_profile(from_number, updated)
        self._sync_onboarding_conversation_meta(from_number, updated)
        return updated

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
            courses = self._fetch_courses(
                course_source,
                age_group=requested_ages,
                include_details=True,
            )
            filtered = self._filter_courses(
                courses,
                requested_ages,
                profile.get("target", ""),
                self._topic_for_exact_filter(profile),
            )
            if profile.get("pain_points"):
                ranked = self._rank_courses_for_profile(
                    filtered,
                    profile,
                    require_pain_match=True,
                )
                if ranked:
                    filtered = ranked
                else:
                    filtered = []
            if not filtered:
                age_group = requested_ages
                has_secondary_filter = bool(profile.get("target") or profile.get("topic"))
                if age_group and has_secondary_filter and not profile.get("pain_points"):
                    filtered = self._filter_courses(courses, age_group=age_group)
                if not filtered:
                    self._memory.add_agent_flag(
                        from_number,
                        "no_match",
                        "LLM 推薦前沒有符合家長記憶的候選課程",
                        {"profile": profile},
                    )
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
            cache_key = self._llm_cache_key(user_text, profile, candidate_payload)
            cached_reply = self._memory.get_llm_cached_response(cache_key)
            if cached_reply:
                logger.info("DeepSeek 快取命中 from=%s", from_number)
                repaired_reply = self._repair_reply_links(cached_reply)
                if repaired_reply != cached_reply:
                    self._memory.save_llm_cached_response(cache_key, repaired_reply)
                return repaired_reply

            if not self._consume_llm_quota(from_number):
                return None

            messages = [
                {
                    "role": "system",
                    "content": (
                        "你是澳門家長學堂 WhatsApp agentic 課程助手。"
                        "你要先理解家長情境，再從候選課程中挑少量選項。"
                        "只回答澳門家長學堂課程查詢；如果訊息要求餐廳、天氣、投資、"
                        "功課、翻譯或任何無關內容，只能請對方改問家長學堂課程，"
                        "不可順便回答無關問題。"
                        "只能根據候選課程回答，不可創造課程、日期、名額或連結。"
                        "推薦理由要優先看候選課程的 summary 是否回應家長痛點，"
                        "不要只看課程名稱。"
                        "最多推薦 3 個課程。每個推薦要有一句人話理由。"
                        "每個推薦都要直接貼上候選課程提供的 reply_url；"
                        "如果沒有 reply_url 才用 detail_url。"
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
            reply = self._call_deepseek(messages)
            if reply:
                reply = self._repair_reply_links(reply)
                if not self._llm_reply_uses_only_candidate_urls(reply, candidate_payload):
                    logger.warning("DeepSeek 回覆包含非候選課程連結，改用規則式推薦")
                    self._memory.add_agent_flag(
                        from_number,
                        "uncertain",
                        "DeepSeek 回覆包含非候選課程連結，已改用規則式推薦",
                        {"candidate_count": len(candidate_payload)},
                    )
                    return None
                self._memory.save_llm_cached_response(cache_key, reply)
            return reply
        except Exception as e:
            logger.warning("LLM 推薦建立失敗: %s", e)
            return None

    def _get_agentic_recommendation_text(
        self,
        from_number: str,
        profile: Dict[str, Any],
        user_text: str = "",
    ) -> str:
        if not self._profile_has_signal(profile) or not self._profile_ready_for_recommendation(profile):
            return self._onboarding_text(profile)

        llm_reply = self._get_llm_recommendation_text(from_number, user_text, profile)
        if llm_reply:
            return self._with_proactive_consent_prompt(from_number, llm_reply)

        reply = self._get_courses_text(
            from_number=from_number,
            age_group=self._profile_age_groups(profile),
            target=profile.get("target", ""),
            topic=profile.get("topic", ""),
            page=1,
            agentic=True,
            profile=profile,
        )
        return self._with_proactive_consent_prompt(from_number, reply)

    def _should_append_proactive_consent_prompt(self, from_number: str) -> bool:
        conversation = self._memory.get_conversation(from_number)
        return conversation.get("consent_status", "unknown") == "unknown"

    @staticmethod
    def _is_non_recommendation_reply(reply: str) -> bool:
        return any(
            marker in reply
            for marker in [
                "暫時沒有",
                "課程資料暫時無法取得",
                "課程資料獲取失敗",
            ]
        )

    def _with_proactive_consent_prompt(self, from_number: str, reply: str) -> str:
        if not reply or not self._should_append_proactive_consent_prompt(from_number):
            return reply
        if self._is_non_recommendation_reply(reply):
            return reply
        if "同意推送" in reply:
            return reply
        return reply + PROACTIVE_CONSENT_PROMPT

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
        link = self._course_reply_url(c)
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

    def _handle_text_message(self, from_number: str, text: str, record_inbound: bool = True) -> None:
        """處理家長發送的文字消息"""
        text_lower = text.strip().lower()
        normalized = self._normalize_command(text)
        logger.info(f"收到消息 from={from_number}: {text}")
        if record_inbound:
            self._memory.record_message(from_number, "inbound", "parent", text)

        if self._memory.is_human_takeover(from_number):
            logger.info("AI 已暫停，自動略過 from=%s", from_number)
            return

        current_profile = self._load_profile(from_number)
        harness_decision = decide_message_route(text, current_profile)
        self._memory.record_harness_trace(
            from_number,
            route=harness_decision.get("route", ""),
            intent=harness_decision.get("intent", ""),
            recommended_action=harness_decision.get("recommended_action", ""),
            allow_llm=bool(harness_decision.get("allow_llm")),
            llm_purpose=harness_decision.get("llm_purpose", ""),
        )

        # 關鍵詞匹配
        if normalized in RESET_KEYWORDS:
            self._profiles.pop(from_number, None)
            self._last_queries.pop(from_number, None)
            self._memory.clear_user(from_number)
            self._reply(from_number, "已重設。你可以回覆：*小朋友幾歲，想找哪類課程*。")
            return

        if normalized in PROFILE_KEYWORDS:
            profile = self._load_profile(from_number)
            reply = (
                "📌 *我目前記得的偏好*\n\n"
                f"{self._profile_text(profile)}\n\n"
                "你可以直接補充，例如：*不要親子，要家長課*、*只要青少年*、*想身心健康*。"
            )
            self._reply(from_number, reply)
            return

        consent_status = self._detect_proactive_consent_status(text)
        if consent_status:
            self._memory.update_conversation(
                from_number,
                consent_status=consent_status,
                proactive_notes=text.strip()[:180],
            )
            self._reply(from_number, self._proactive_consent_text(consent_status))
            return

        if (
            (self._is_off_topic_request(text) and not self._has_parent_pain_signal(text))
            or self._is_out_of_scope_request(text)
        ):
            self._reply(from_number, self._off_topic_text())
            return

        profile = self._update_profile_from_text(from_number, text)
        self._sync_onboarding_conversation_meta(from_number, profile)
        age_groups = detect_age_groups(text)
        target = self._detect_positive_option(text, TARGETS)
        topic = self._detect_positive_option(text, TOPICS)
        pain_points = detect_pain_points(text)
        page_request = self._parse_page_request(text)
        detail_request = self._parse_detail_request(text)
        if detail_request is not None:
            reply = self._get_course_detail_text(from_number, detail_request)
        elif age_groups or target or topic or pain_points:
            reply = self._get_agentic_recommendation_text(from_number, profile, text)
        elif page_request is not None:
            if from_number not in self._last_queries:
                persisted_query = self._memory.get_last_query(from_number)
                if persisted_query:
                    self._last_queries[from_number] = persisted_query
                else:
                    reply = self._get_agentic_recommendation_text(from_number, profile, text)
                    self._reply(from_number, reply)
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
                profile=profile,
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
            llm_profile = self._update_profile_from_llm_text(from_number, text, profile)
            if llm_profile != profile:
                profile = llm_profile
                reply = self._get_agentic_recommendation_text(from_number, profile, text)
            else:
                self._memory.add_agent_flag(
                    from_number,
                    "uncertain",
                    "AI 未能理解家長訊息，需要人工檢視或補充提示",
                    {"message": text.strip()[:300]},
                )
                reply = self._unknown_text()

        self._reply(from_number, reply)

    @staticmethod
    def _iter_webhook_messages(data: dict):
        entries = data.get("entry", [])
        for entry in entries:
            changes = entry.get("changes", [])
            for change in changes:
                value = change.get("value", {})
                messages = value.get("messages", [])
                for msg in messages:
                    yield msg

    def claim_webhook_messages(self, data: dict) -> bool:
        """Claim incoming message ids before background processing.

        Returns True when there is at least one new message worth processing.
        """
        has_new_message = False
        for msg in self._iter_webhook_messages(data):
            msg_id = msg.get("id", "")
            from_number = msg.get("from")
            if msg_id and not self._memory.claim_message(msg_id, from_number or ""):
                logger.info("略過已處理 WhatsApp 訊息: %s", msg_id)
                continue
            has_new_message = True
        return has_new_message

    def handle_webhook(self, data: dict, messages_preclaimed: bool = False) -> None:
        """處理 Meta 發來的 webhook 事件"""
        logger.info(f"收到 WhatsApp webhook: {data}")

        for msg in self._iter_webhook_messages(data):
            msg_id = msg.get("id", "")
            msg_type = msg.get("type")
            from_number = msg.get("from")
            if (
                not messages_preclaimed
                and msg_id
                and not self._memory.claim_message(msg_id, from_number or "")
            ):
                logger.info("略過已處理 WhatsApp 訊息: %s", msg_id)
                continue

            if msg_type == "text":
                text_body = msg.get("text", {}).get("body", "")
                if from_number and text_body:
                    self._handle_text_message(from_number, text_body)
            else:
                if from_number:
                    self._handle_non_text_message(from_number, msg)

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
