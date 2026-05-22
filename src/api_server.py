"""FastAPI HTTP 服務器 — Zeabur 適配

接收企業微信客服回調，提供管理接口
"""

import logging
import os
import re
import sys
import json
import hashlib
import hmac
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

logger = logging.getLogger("api_server")

from fastapi import FastAPI, Request, BackgroundTasks, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel
from bot_webhook import ZeaburBot
from wecom_cs_handler import CSMessageHandler
from wecom_crypto import WeComCrypto
from wecom_poller import WeComPoller
from whatsapp_handler import WhatsAppHandler, is_configured as wa_is_configured, is_valid_meta_signature
from whatsapp_memory import WhatsAppMemoryStore

# 全局實例
bot: Optional[ZeaburBot] = None
cs_handler: Optional[CSMessageHandler] = None
cs_crypto: Optional[WeComCrypto] = None
poller: Optional[WeComPoller] = None
wa_handler: Optional[WhatsAppHandler] = None
wa_memory: Optional[WhatsAppMemoryStore] = None

ADMIN_SESSION_COOKIE = "parent_school_admin"
ADMIN_SESSION_SALT = b"parent-school-admin-session-v1"


def get_bot() -> ZeaburBot:
    global bot
    if bot is None:
        bot = ZeaburBot()
    return bot


def get_cs_handler() -> CSMessageHandler:
    global cs_handler
    if cs_handler is None:
        cs_handler = CSMessageHandler()
    return cs_handler


def get_cs_crypto() -> Optional[WeComCrypto]:
    global cs_crypto
    if cs_crypto is None:
        aes_key = os.environ.get("WECOM_ENCODING_AES_KEY", "")
        token = os.environ.get("WECOM_TOKEN", "")
        corp_id = os.environ.get("WECOM_CORP_ID", "")
        if aes_key and token and corp_id:
            try:
                cs_crypto = WeComCrypto(aes_key, token, corp_id)
                logger.info("WeCom crypto 初始化成功")
            except Exception as e:
                logger.warning(f"WeCom crypto 初始化失敗: {e}")
    return cs_crypto


def get_wa_handler() -> Optional[WhatsAppHandler]:
    """獲取 WhatsApp 處理器"""
    global wa_handler
    if wa_handler is None and wa_is_configured():
        try:
            wa_handler = WhatsAppHandler()
            logger.info("WhatsApp handler 初始化成功")
        except Exception as e:
            logger.warning(f"WhatsApp handler 初始化失敗: {e}")
    return wa_handler


def get_wa_memory_store() -> WhatsAppMemoryStore:
    """Shared WhatsApp memory store for admin views."""
    global wa_memory
    if wa_handler is not None:
        return wa_handler._memory
    if wa_memory is None:
        wa_memory = WhatsAppMemoryStore()
    return wa_memory


def require_secret(provided: str, env_keys: tuple[str, ...], label: str) -> None:
    """檢查管理/排程接口密鑰。"""
    expected = _first_configured_secret(env_keys)

    if not expected:
        logger.error("%s secret 未配置", label)
        raise HTTPException(status_code=500, detail=f"{label} secret not configured")

    if not hmac.compare_digest(str(provided or ""), expected):
        raise HTTPException(status_code=401, detail="Unauthorized")


def _first_configured_secret(env_keys: tuple[str, ...]) -> str:
    for key in env_keys:
        expected = os.environ.get(key, "")
        if expected:
            return expected
    return ""


def make_admin_session_token(secret: str) -> str:
    """Derive a stable cookie token without storing the raw admin secret."""
    return hmac.new(
        str(secret or "").encode("utf-8"),
        ADMIN_SESSION_SALT,
        hashlib.sha256,
    ).hexdigest()


def _authorization_bearer(request: Request) -> str:
    auth = request.headers.get("authorization") or request.headers.get("Authorization") or ""
    prefix = "Bearer "
    if auth.startswith(prefix):
        return auth[len(prefix):].strip()
    return ""


def require_admin_request(request: Request) -> None:
    """Authorize admin APIs via HttpOnly session cookie or Authorization header."""
    expected = _first_configured_secret(("ADMIN_SECRET",))
    if not expected:
        logger.error("Admin secret 未配置")
        raise HTTPException(status_code=500, detail="Admin secret not configured")

    bearer = _authorization_bearer(request)
    if bearer and hmac.compare_digest(bearer, expected):
        return

    cookie = request.cookies.get(ADMIN_SESSION_COOKIE, "")
    expected_cookie = make_admin_session_token(expected)
    if cookie and hmac.compare_digest(cookie, expected_cookie):
        return

    raise HTTPException(status_code=401, detail="Unauthorized")


def _admin_cookie_secure() -> bool:
    raw = os.environ.get("ADMIN_COOKIE_SECURE", "true").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _env_truthy(name: str) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# ============== Pydantic 模型 ==============

class WeComCallback(BaseModel):
    """企業微信群機器人回調數據"""
    msgtype: str = ""
    text: dict = {}
    sender: str = ""
    sender_id: str = ""


class PushResponse(BaseModel):
    success: bool
    courses: int = 0
    users: int = 0
    error: str = ""
    timestamp: str = ""


class StatusResponse(BaseModel):
    status: str
    users: int
    configured: int
    uptime: str


class AdminMessageRequest(BaseModel):
    body: str


class AdminLoginRequest(BaseModel):
    secret: str = ""


class ConversationUpdateRequest(BaseModel):
    display_name: str = ""
    tags: list[str] = []
    notes: str = ""
    consent_status: str = ""
    proactive_notes: str = ""


class ProfileUpdateRequest(BaseModel):
    age_groups: list[str] = []
    pain_points: list[str] = []
    target: str = ""
    topic: str = ""
    pain_summary: str = ""


class ProactiveSendRequest(BaseModel):
    body: str = ""
    use_template: bool = False
    template_name: str = ""
    template_language: str = ""
    template_params: list[str] = []


class ProactiveDraftGenerateRequest(BaseModel):
    parent_limit: int = 100
    courses_per_parent: int = 3
    allowed_only: bool = False


class ProactiveDraftUpdateRequest(BaseModel):
    body: str = ""


class PrivacyPruneRequest(BaseModel):
    older_than_days: int = 90
    dry_run: bool = True


class QaFeedbackRequest(BaseModel):
    message_id: int = 0
    rating: str = "bad"
    issue_type: str = "other"
    summary: str = ""
    expected_behavior: str = ""


class QaFeedbackStatusRequest(BaseModel):
    status: str = "closed"


def _profile_options() -> Dict[str, list[str]]:
    return WhatsAppHandler.admin_profile_options()


def _scrub_private_text(text: str) -> str:
    scrubbed = str(text or "")
    scrubbed = re.sub(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", "[email]", scrubbed)
    scrubbed = re.sub(r"\+?\d[\d\s().-]{5,}\d", "[phone]", scrubbed)
    return scrubbed[:1200]


def _build_qa_learning_sample(
    store: WhatsAppMemoryStore,
    phone: str,
    message_id: int,
    rating: str,
    issue_type: str,
    summary: str,
    expected_behavior: str,
) -> Dict:
    messages = store.get_messages(phone, limit=80)
    selected_index = -1
    for index, message in enumerate(messages):
        if int(message.get("id", 0) or 0) == int(message_id or 0):
            selected_index = index
            break
    if selected_index < 0:
        for index in range(len(messages) - 1, -1, -1):
            message = messages[index]
            if message.get("direction") == "outbound" and message.get("source") == "ai":
                selected_index = index
                break
    if selected_index < 0 and messages:
        selected_index = len(messages) - 1

    selected = messages[selected_index] if selected_index >= 0 else {}
    parent_message = ""
    ai_response = ""
    if selected.get("source") == "parent":
        parent_message = str(selected.get("body", ""))
    elif selected.get("source") == "ai":
        ai_response = str(selected.get("body", ""))

    for index in range(selected_index - 1, -1, -1):
        message = messages[index]
        if not parent_message and message.get("source") == "parent":
            parent_message = str(message.get("body", ""))
        if not ai_response and message.get("source") == "ai":
            ai_response = str(message.get("body", ""))
        if parent_message and ai_response:
            break
    for index in range(selected_index + 1, len(messages)):
        message = messages[index]
        if not ai_response and message.get("source") == "ai":
            ai_response = str(message.get("body", ""))
        if not parent_message and message.get("source") == "parent":
            parent_message = str(message.get("body", ""))
        if parent_message and ai_response:
            break

    profile = store.get_profile(phone)
    return {
        "source": "admin_qa_feedback",
        "message_id": int(selected.get("id", 0) or 0),
        "rating": str(rating or "bad"),
        "issue_type": str(issue_type or "other"),
        "parent_message": _scrub_private_text(parent_message),
        "ai_response": _scrub_private_text(ai_response),
        "operator_summary": _scrub_private_text(summary),
        "expected_behavior": _scrub_private_text(expected_behavior),
        "profile": {
            "age_groups": profile.get("age_groups", []),
            "pain_points": profile.get("pain_points", []),
            "target": profile.get("target", ""),
            "topic": profile.get("topic", ""),
            "pain_summary": _scrub_private_text(profile.get("pain_summary", "")),
        },
        "privacy": "phone/email-like strings scrubbed; original transcript stays admin-only",
    }


def _scrub_private_value(value: Any) -> Any:
    if isinstance(value, str):
        return _scrub_private_text(value)
    if isinstance(value, list):
        return [_scrub_private_value(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _scrub_private_value(item)
            for key, item in value.items()
            if str(key).lower() != "phone"
        }
    return value


def _build_qa_eval_case(feedback: Dict[str, Any]) -> Dict[str, Any]:
    sample = feedback.get("anonymized_sample") or {}
    profile = _scrub_private_value(sample.get("profile") or {})
    return {
        "source": "admin_qa_feedback",
        "feedback_id": feedback.get("id", 0),
        "issue_type": feedback.get("issue_type", ""),
        "rating": feedback.get("rating", ""),
        "summary": _scrub_private_text(feedback.get("summary", "")),
        "expected_behavior": _scrub_private_text(feedback.get("expected_behavior", "")),
        "parent_message": _scrub_private_text(sample.get("parent_message", "")),
        "ai_response": _scrub_private_text(sample.get("ai_response", "")),
        "profile": profile,
        "privacy": "scrubbed",
        "created_at": feedback.get("created_at", ""),
    }


def _build_agent_state(
    store: WhatsAppMemoryStore,
    phone: str,
    profile: Dict,
    conversation: Optional[Dict] = None,
) -> Dict:
    age_groups = WhatsAppHandler._normalize_age_groups(profile.get("age_groups") or profile.get("age_group", ""))
    has_concern = bool(
        profile.get("pain_points")
        or profile.get("target")
        or profile.get("topic")
    )
    missing_fields = []
    if not age_groups:
        missing_fields.append("age_group")
    if not has_concern:
        missing_fields.append("concern")

    open_flags_count = store.count_agent_flags(phone=phone, unresolved_only=True)
    draft_count = store.count_proactive_drafts(phone=phone, status="draft")
    qa_feedback_count = store.count_qa_feedback(status="open", phone=phone)
    conversation = conversation or store.get_conversation(phone)
    consent_status = conversation.get("consent_status", "unknown")

    if qa_feedback_count:
        recommended_action = "檢視 QA 回饋"
    elif open_flags_count:
        recommended_action = "需要人工判斷"
    elif "age_group" in missing_fields:
        recommended_action = "追問年齡"
    elif "concern" in missing_fields:
        recommended_action = "追問痛點"
    elif consent_status == "allowed":
        recommended_action = "可產生主動草稿"
    else:
        recommended_action = "可推薦課程"

    return {
        "profile_ready": not missing_fields,
        "missing_fields": missing_fields,
        "recommended_action": recommended_action,
        "open_flags_count": open_flags_count,
        "draft_count": draft_count,
        "qa_feedback_count": qa_feedback_count,
        "last_harness_route": conversation.get("last_harness_route", ""),
        "last_harness_intent": conversation.get("last_harness_intent", ""),
        "last_harness_action": conversation.get("last_harness_action", ""),
        "last_harness_allow_llm": bool(conversation.get("last_harness_allow_llm", 0)),
        "last_harness_llm_purpose": conversation.get("last_harness_llm_purpose", ""),
        "last_harness_at": conversation.get("last_harness_at", ""),
    }


def _enrich_conversation_for_inbox(
    store: WhatsAppMemoryStore,
    conversation: Dict,
) -> Dict:
    phone = conversation.get("phone", "")
    enriched = dict(conversation)
    profile = store.get_profile(phone)
    agent_state = _build_agent_state(store, phone, profile, conversation)
    enriched["open_flags_count"] = agent_state["open_flags_count"]
    enriched["draft_count"] = agent_state["draft_count"]
    enriched["qa_feedback_count"] = agent_state["qa_feedback_count"]
    enriched["profile_ready"] = agent_state["profile_ready"]
    enriched["recommended_action"] = agent_state["recommended_action"]
    return enriched


def _filter_conversations(
    store: WhatsAppMemoryStore,
    conversations: list[Dict],
    status: str = "",
    consent_status: str = "",
    filter: str = "",
    search: str = "",
) -> list[Dict]:
    status = str(status or "").strip().lower()
    consent_status = str(consent_status or "").strip().lower()
    filter_name = str(filter or "").strip().lower()
    search_text = str(search or "").strip().lower()

    filtered = [_enrich_conversation_for_inbox(store, c) for c in conversations]
    if status in {"ai", "human"}:
        filtered = [c for c in filtered if c.get("status") == status]
    if consent_status in {"allowed", "paused", "unknown"}:
        filtered = [c for c in filtered if c.get("consent_status") == consent_status]
    if filter_name == "flagged":
        filtered = [c for c in filtered if int(c.get("open_flags_count", 0)) > 0]
    elif filter_name == "pushable":
        filtered = [c for c in filtered if c.get("consent_status") == "allowed"]
    elif filter_name == "draft":
        filtered = [c for c in filtered if int(c.get("draft_count", 0)) > 0]
    if search_text:
        def matches(conversation: Dict) -> bool:
            haystack = " ".join([
                str(conversation.get("phone", "")),
                str(conversation.get("display_name", "")),
                str(conversation.get("latest_message", "")),
                " ".join([str(tag) for tag in conversation.get("tags", [])]),
                str(conversation.get("notes", "")),
            ]).lower()
            if search_text in haystack:
                return True
            profile = store.get_profile(str(conversation.get("phone", "")))
            return search_text in json.dumps(profile, ensure_ascii=False).lower()

        filtered = [c for c in filtered if matches(c)]
    return filtered


def _agent_task(
    task_type: str,
    phone: str,
    priority: int,
    title: str,
    summary: str,
    action: str,
    conversation: Dict,
    agent_state: Dict,
    profile: Dict,
) -> Dict:
    return {
        "type": task_type,
        "phone": phone,
        "priority": priority,
        "title": title,
        "summary": summary,
        "action": action,
        "conversation": conversation,
        "agent_state": agent_state,
        "profile": {
            "age_groups": profile.get("age_groups", []),
            "pain_points": profile.get("pain_points", []),
            "target": profile.get("target", ""),
            "topic": profile.get("topic", ""),
            "pain_summary": profile.get("pain_summary", ""),
        },
    }


def _build_agent_tasks_for_conversation(
    store: WhatsAppMemoryStore,
    conversation: Dict,
) -> list[Dict]:
    phone = str(conversation.get("phone", ""))
    if not phone:
        return []

    profile = store.get_profile(phone)
    state = _build_agent_state(store, phone, profile, conversation)
    tasks: list[Dict] = []
    open_flags = int(state.get("open_flags_count", 0) or 0)
    draft_count = int(state.get("draft_count", 0) or 0)
    qa_feedback_count = int(state.get("qa_feedback_count", 0) or 0)
    consent_status = str(conversation.get("consent_status", "unknown") or "unknown")
    missing_fields = set(state.get("missing_fields", []))

    if qa_feedback_count:
        tasks.append(_agent_task(
            "review_qa_feedback",
            phone,
            105,
            "檢視朋友測試回饋",
            f"這位家長有 {qa_feedback_count} 個開放 QA 標記，可整理成 prompt rule 或回歸測試。",
            "打開對話，查看 QA 學習樣本",
            conversation,
            state,
            profile,
        ))
    if open_flags:
        tasks.append(_agent_task(
            "review_flag",
            phone,
            100,
            "處理 AI 不確定項目",
            f"這位家長有 {open_flags} 個待處理 flag，需要人工判斷或修正記憶。",
            "打開對話，查看右欄不確定隊列",
            conversation,
            state,
            profile,
        ))
    if str(conversation.get("status", "ai")) == "human":
        tasks.append(_agent_task(
            "human_takeover",
            phone,
            90,
            "人工接手中",
            "AI 已暫停自動回覆，家長新訊息只會進 transcript。",
            "完成人工回覆後恢復 AI",
            conversation,
            state,
            profile,
        ))
    if "age_group" in missing_fields:
        tasks.append(_agent_task(
            "ask_age",
            phone,
            80,
            "追問孩子年齡",
            "Profile 還缺孩子年齡，暫時不適合主動推薦課程。",
            "問：小朋友幾多歲？",
            conversation,
            state,
            profile,
        ))
    if "concern" in missing_fields:
        tasks.append(_agent_task(
            "ask_concern",
            phone,
            75,
            "追問家長痛點",
            "Profile 還缺痛點或方向，推薦容易變成泛泛列表。",
            "問：最近比較想處理情緒、學習、親子溝通，還是升學壓力？",
            conversation,
            state,
            profile,
        ))
    if draft_count:
        tasks.append(_agent_task(
            "approve_draft",
            phone,
            70,
            "審批主動推送草稿",
            f"已有 {draft_count} 份待發草稿，等管理員確認內容。",
            "打開草稿，修改後發送或略過",
            conversation,
            state,
            profile,
        ))
    if state.get("profile_ready") and consent_status == "unknown":
        tasks.append(_agent_task(
            "ask_consent",
            phone,
            55,
            "確認主動推送同意",
            "家長記憶已足夠，但未確認是否願意之後收到提醒。",
            "推薦後請家長回覆「同意推送」",
            conversation,
            state,
            profile,
        ))
    if state.get("profile_ready") and consent_status == "allowed" and not draft_count:
        tasks.append(_agent_task(
            "generate_draft",
            phone,
            45,
            "可產生主動匹配草稿",
            "家長已同意推送，而且 Profile 足夠，可檢查是否有新課程可匹配。",
            "按「主動匹配」產生待審草稿",
            conversation,
            state,
            profile,
        ))

    return tasks


def _build_agent_task_queue(
    store: WhatsAppMemoryStore,
    limit: int = 100,
    search: str = "",
) -> Dict:
    conversations = _filter_conversations(
        store,
        store.list_conversations(limit=max(limit, 200)),
        search=search,
    )
    tasks: list[Dict] = []
    for conversation in conversations:
        tasks.extend(_build_agent_tasks_for_conversation(store, conversation))
    tasks.sort(key=lambda task: (
        int(task.get("priority", 0)),
        str(task.get("conversation", {}).get("last_message_at", "")),
    ), reverse=True)
    tasks = tasks[:max(1, min(int(limit or 100), 200))]
    by_type: Dict[str, int] = {}
    for task in tasks:
        task_type = str(task.get("type", ""))
        by_type[task_type] = by_type.get(task_type, 0) + 1
    briefing = {
        "total_tasks": len(tasks),
        "parents": len({task.get("phone") for task in tasks if task.get("phone")}),
        "by_type": by_type,
    }
    return {"total": len(tasks), "tasks": tasks, "briefing": briefing}


# ============== FastAPI 應用 ==============

app = FastAPI(
    title="家長學堂課程推送 Bot",
    description="Zeabur 部署的企業微信 + WhatsApp 課程推送服務",
    version="3.0.0",
)


@app.on_event("startup")
async def startup():
    """啟動時初始化"""
    global poller
    logger.info("API Server 啟動...")
    get_bot()
    handler = get_cs_handler()
    get_cs_crypto()

    # 如果有 WeCom CS API 配置，啟動輪詢
    if handler.api:
        poll_interval = int(os.environ.get("WECOM_POLL_INTERVAL", "5"))
        poller = WeComPoller(handler, poll_interval=poll_interval)
        poller.start()
        logger.info("WeCom CS 輪詢模式已啟動（無需回調 URL）")
    else:
        logger.warning("WeCom CS API 未配置，輪詢未啟動")


@app.on_event("shutdown")
async def shutdown():
    """關閉時清理"""
    global poller
    if poller:
        poller.stop()
        logger.info("輪詢器已停止")


def _public_landing_html() -> str:
    whatsapp_phone = "8614714949607"
    share_text = "%E8%AA%B2%E7%A8%8B"
    web_link = f"https://wa.me/{whatsapp_phone}?text={share_text}"
    app_link = f"whatsapp://send?phone={whatsapp_phone}&text={share_text}"
    android_intent = (
        f"intent://send?phone={whatsapp_phone}&text={share_text}"
        "#Intent;scheme=whatsapp;package=com.whatsapp;end"
    )
    share_url = "https://parent-school-bot.zeabur.app/whatsapp"
    html = """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>悅昕心理小助手阿Sa｜家長學堂 WhatsApp 課程配對</title>
  <meta name="description" content="悅昕心理小助手阿Sa 透過 WhatsApp 協助家長查詢澳門家長學堂課程，按孩子年齡和關心方向取得少量官方課程連結。">
  <meta property="og:title" content="悅昕心理小助手阿Sa">
  <meta property="og:description" content="家長學堂 WhatsApp 課程小助手，打開 WhatsApp，輸入「課程」即可查詢澳門家長學堂課程。">
  <meta property="og:image" content="https://parent-school-bot.zeabur.app/whatsapp-qr.png">
  <style>
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #1f2933; background: #f7f7f2; }
    main { max-width: 760px; margin: 0 auto; padding: 56px 24px; }
    h1 { font-size: 34px; line-height: 1.2; margin: 0 0 16px; }
    p { font-size: 18px; line-height: 1.7; margin: 0 0 16px; }
    .panel { background: #fff; border: 1px solid #d9ded7; border-radius: 8px; padding: 24px; margin-top: 28px; }
    .button { display: inline-block; background: #0f8f5f; color: #fff; text-decoration: none; padding: 12px 18px; border-radius: 6px; font-weight: 700; }
    .secondary { display: inline-block; color: #0f6f4c; margin-left: 10px; }
    .qr { width: 220px; max-width: 100%; border-radius: 8px; border: 1px solid #d9ded7; margin-top: 12px; }
    .small { font-size: 14px; color: #52616b; }
    .wechat-tip { display: none; border-left: 4px solid #0f8f5f; padding: 12px 14px; background: #edf8f1; margin: 16px 0; }
    .copybox { user-select: all; font-size: 16px; background: #f4f5f2; padding: 10px 12px; border-radius: 6px; word-break: break-all; }
  </style>
</head>
<body>
  <main>
    <h1>悅昕心理小助手阿Sa</h1>
    <p>家長學堂 WhatsApp 課程小助手。</p>
    <p>這是一個協助家長查詢澳門家長學堂課程的 WhatsApp 測試版小助手。你可以用一句自然語句描述孩子年齡和最近關心的方向，我會盡量只挑少量相關課程，並附官方報名連結。</p>
    <p>課程資料來自澳門教育及青年發展局家長學堂公開課程頁面，實際名額、時間和報名狀態以官方網站為準。</p>
    <div class="panel">
      <p>WhatsApp 使用方式：</p>
      <p>輸入「課程」、「13歲有什麼課程」、「小朋友4歲，想親子課」、「更多」即可查詢。</p>
      <div id="wechatTip" class="wechat-tip">
        如果你正在 WeChat 裡打開，請點右上角「...」選擇用瀏覽器打開；或直接掃下面 QR code。WeChat 內建瀏覽器有時會攔截 WhatsApp app。
      </div>
      <p>
        <a id="openWhatsApp" class="button" href="__WEB_LINK__">開啟 WhatsApp 查詢</a>
        <a class="secondary" href="__SHARE_URL__">分享入口</a>
      </p>
      <p class="small">也可以手動加入 WhatsApp：+86 147 1494 9607，然後傳送「課程」。</p>
      <div class="copybox">__SHARE_URL__</div>
      <p><img class="qr" src="/whatsapp-qr.png" alt="悅昕心理小助手阿Sa WhatsApp QR code"></p>
    </div>
    <div class="panel">
      <p>私隱說明</p>
      <p class="small">本服務只會使用你在 WhatsApp 對話中提供的孩子大概年齡、關心方向和課程查詢內容，以便記住偏好和回覆課程建議。請不要輸入小朋友姓名、學校、證件、住址或其他敏感資料。</p>
      <p class="small">不會出售個人資料，也不提供與家長學堂課程無關的通用 AI 問答。你可以在 WhatsApp 回覆「私隱」查看說明，回覆「暫停推送」停止主動課程提醒，或回覆「刪除資料」清除保存的對話記錄和偏好。</p>
    </div>
  </main>
  <script>
    (function () {
      var ua = navigator.userAgent || "";
      var isWechat = /MicroMessenger/i.test(ua);
      var isAndroid = /Android/i.test(ua);
      var appLink = "__APP_LINK__";
      var webLink = "__WEB_LINK__";
      var androidIntent = "__ANDROID_INTENT__";
      var button = document.getElementById("openWhatsApp");
      if (isWechat) {
        document.getElementById("wechatTip").style.display = "block";
        button.href = webLink;
        return;
      }
      button.href = isAndroid ? androidIntent : appLink;
      setTimeout(function () {
        window.location.href = isAndroid ? androidIntent : appLink;
      }, 350);
      setTimeout(function () {
        if (!document.hidden) window.location.href = webLink;
      }, 1600);
    })();
  </script>
</body>
</html>"""
    return (
        html
        .replace("__APP_LINK__", app_link)
        .replace("__ANDROID_INTENT__", android_intent)
        .replace("__SHARE_URL__", share_url)
        .replace("__WEB_LINK__", web_link)
    )


@app.get("/", response_class=HTMLResponse)
async def root():
    """Public landing page for business verification and parents."""
    return _public_landing_html()


@app.get("/whatsapp", response_class=HTMLResponse)
async def whatsapp_share_page():
    """WeChat-friendly WhatsApp app handoff page."""
    return _public_landing_html()


@app.get("/whatsapp-qr.png")
async def whatsapp_qr():
    """Shareable WhatsApp QR card."""
    qr_path = PROJECT_ROOT / "whatsapp_parent_school_qr.png"
    if not qr_path.exists():
        raise HTTPException(status_code=404, detail="QR code not found")
    return FileResponse(str(qr_path), media_type="image/png")


@app.get("/whatsapp-qr-clean.png")
async def whatsapp_qr_clean():
    """Clean QR image for printing or scanners that dislike styled cards."""
    qr_path = PROJECT_ROOT / "whatsapp_parent_school_qr_clean.png"
    if not qr_path.exists():
        raise HTTPException(status_code=404, detail="QR code not found")
    return FileResponse(str(qr_path), media_type="image/png")


@app.head("/", response_class=HTMLResponse)
async def root_head():
    """Allow website validators to probe the landing page with HEAD."""
    return ""


@app.get("/health")
async def health():
    """健康檢查端點"""
    b = get_bot()
    stats = b.store.get_stats()
    result = {
        "status": "healthy",
        "webhook_configured": bool(b.webhook_url),
        "users": stats,
    }
    if poller:
        result["poller"] = poller.get_status()
    result["whatsapp"] = wa_is_configured()
    return result


@app.get("/api/status")
async def api_status():
    """狀態查詢"""
    b = get_bot()
    stats = b.store.get_stats()
    return StatusResponse(
        status="running",
        users=stats.get("total", 0),
        configured=stats.get("configured", 0),
        uptime=datetime.now().isoformat(),
    )


@app.post("/api/push", response_model=PushResponse)
async def api_push(background_tasks: BackgroundTasks, secret: str = ""):
    """手動觸發推送"""
    require_secret(secret, ("ADMIN_SECRET", "CRON_SECRET"), "Admin")
    b = get_bot()
    result = b.run_push()
    return PushResponse(
        success=result.get("success", False),
        courses=result.get("courses", 0),
        users=result.get("users", 0),
        error=result.get("error", ""),
        timestamp=datetime.now().isoformat(),
    )


@app.post("/api/webhook")
async def api_webhook(request: Request):
    """
    接收企業微信群機器人回調

    企業微信群機器人發送的消息會打到這個接口
    """
    try:
        data = await request.json()
        logger.info(f"收到 Webhook 回調: {data}")

        b = get_bot()
        result = b.handle_group_message(data)

        # 如果有回覆，發送到群
        if result.get("reply") and b.webhook:
            b.webhook.send_markdown(result["reply"])

        return {"success": True, "action": result.get("action", "unknown")}

    except Exception as e:
        logger.exception(f"Webhook 處理失敗: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/cron")
async def api_cron(secret: str = ""):
    """
    Cron 觸發端點（Zeabur Cron Job 調用）

    需在 URL 中帶 secret 參數防止未授權調用
    如: /api/cron?secret=your_secret_key
    """
    require_secret(secret, ("CRON_SECRET",), "Cron")

    b = get_bot()
    result = b.run_push()
    return result


@app.get("/api/users")
async def api_users(secret: str = ""):
    """獲取用戶列表（管理員接口）"""
    require_secret(secret, ("ADMIN_SECRET", "CRON_SECRET"), "Admin")
    b = get_bot()
    users = b.store.get_active_users()
    return {
        "total": len(users),
        "users": [
            {
                "wx_id": u.wx_id,
                "wx_name": u.wx_name,
                "age_groups": u.child_age_groups,
                "is_active": u.is_active,
                "created_at": u.created_at,
            }
            for u in users
        ],
    }


# ============== WhatsApp Agentic Admin ==============

def _admin_login_html() -> str:
    return """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WhatsApp 家長學堂接手台登入</title>
  <style>
    :root { --bg: #f5f6f2; --panel: #ffffff; --line: #d8ddd2; --ink: #17212b; --muted: #66737d; --brand: #0d7a56; --brand-dark: #075f43; --danger: #9b2c2c; }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: var(--bg); color: var(--ink); letter-spacing: 0; }
    main { min-height: 100vh; display: grid; place-items: center; padding: 24px; }
    form { width: min(380px, 100%); background: var(--panel); border: 1px solid var(--line); border-radius: 8px; padding: 28px; display: grid; gap: 14px; box-shadow: 0 18px 48px rgba(23, 33, 43, .08); }
    h1 { font-size: 20px; margin: 0; line-height: 1.25; }
    .hint { margin: -2px 0 6px; color: var(--muted); font-size: 13px; line-height: 1.5; }
    input, button { font: inherit; border-radius: 6px; padding: 11px 12px; }
    input { border: 1px solid var(--line); background: #fbfcfa; color: var(--ink); outline: none; }
    input:focus { border-color: var(--brand); box-shadow: 0 0 0 3px rgba(13, 122, 86, .12); background: #fff; }
    button { border: 1px solid var(--brand); background: var(--brand); color: #fff; cursor: pointer; font-weight: 700; }
    button:hover { background: var(--brand-dark); }
    .msg { min-height: 20px; color: var(--danger); font-size: 13px; }
  </style>
</head>
<body>
  <main>
    <form id="login">
      <h1>WhatsApp 家長學堂接手台</h1>
      <p class="hint">輸入管理密鑰後進入家長 inbox。</p>
      <input id="secret" type="password" autocomplete="current-password" placeholder="管理密鑰" autofocus>
      <button type="submit">登入</button>
      <div id="msg" class="msg"></div>
    </form>
  </main>
  <script>
    document.getElementById("login").addEventListener("submit", async (event) => {
      event.preventDefault();
      const secret = document.getElementById("secret").value;
      const res = await fetch("/admin/login", {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({secret})
      });
      if (res.ok) {
        location.href = "/admin";
      } else {
        document.getElementById("msg").textContent = "登入失敗";
      }
    });
  </script>
</body>
</html>"""


@app.post("/admin/login")
async def admin_login(payload: AdminLoginRequest):
    """Create an HttpOnly admin session cookie without putting secrets in URLs."""
    expected = _first_configured_secret(("ADMIN_SECRET",))
    if not expected:
        logger.error("Admin secret 未配置")
        raise HTTPException(status_code=500, detail="Admin secret not configured")
    if not hmac.compare_digest(payload.secret or "", expected):
        raise HTTPException(status_code=401, detail="Unauthorized")

    response = JSONResponse({"success": True})
    response.set_cookie(
        ADMIN_SESSION_COOKIE,
        make_admin_session_token(expected),
        max_age=24 * 60 * 60,
        httponly=True,
        secure=_admin_cookie_secure(),
        samesite="lax",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


@app.post("/admin/logout")
async def admin_logout():
    response = JSONResponse({"success": True})
    response.delete_cookie(ADMIN_SESSION_COOKIE)
    response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(request: Request):
    """Minimal WhatsApp operator console."""
    try:
        require_admin_request(request)
    except HTTPException as exc:
        if exc.status_code == 401:
            return HTMLResponse(
                _admin_login_html(),
                headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
            )
        raise
    return HTMLResponse("""<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>WhatsApp 家長學堂接手台</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #eef2f5;
      --panel: #ffffff;
      --panel-soft: #f7f9fb;
      --line: #d8dee6;
      --line-soft: #e8edf2;
      --ink: #17212b;
      --muted: #66737d;
      --faint: #8b969f;
      --brand: #0d7a56;
      --brand-dark: #075f43;
      --brand-soft: #e7f4ed;
      --accent: #2563eb;
      --accent-soft: #eaf1ff;
      --warn: #95621b;
      --warn-bg: #fff7e8;
      --danger: #a13b3b;
      --danger-soft: #fff4f4;
      --shadow: 0 10px 30px rgba(23, 33, 43, .06);
    }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--ink); background: var(--bg); letter-spacing: 0; }
    header { height: 60px; display: flex; align-items: center; justify-content: space-between; padding: 0 18px 0 22px; border-bottom: 1px solid var(--line); background: rgba(255,255,255,.92); backdrop-filter: blur(10px); position: sticky; top: 0; z-index: 2; }
    h1 { font-size: 18px; margin: 0; font-weight: 750; line-height: 1.2; }
    .subtle { color: var(--muted); font-size: 12px; margin-top: 2px; }
    main { display: grid; grid-template-columns: 350px minmax(480px, 1fr) 400px; height: calc(100vh - 60px); min-height: 560px; }
    aside, section { background: var(--panel); min-width: 0; }
    aside:first-child { border-right: 1px solid var(--line); }
    aside:last-child { border-left: 1px solid var(--line); }
    section { background: linear-gradient(180deg, #fbfcfa 0%, #f4f6f2 100%); }
    .toolbar { padding: 12px; border-bottom: 1px solid var(--line-soft); display: flex; gap: 8px; align-items: center; }
    .toolbar.stack { display: grid; grid-template-columns: 1fr; gap: 8px; }
    .tools { display: grid; grid-template-columns: 1fr 1fr; gap: 8px; }
    .inbox-head { padding: 14px; border-bottom: 1px solid var(--line-soft); background: #fff; display: grid; gap: 10px; }
    .inbox-title { display: flex; align-items: center; justify-content: space-between; gap: 10px; }
    .inbox-title strong { font-size: 15px; }
    .queue-stats { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px; }
    .stat { border: 1px solid var(--line-soft); border-radius: 8px; padding: 9px; background: var(--panel-soft); }
    .stat b { display: block; font-size: 17px; line-height: 1; }
    .stat span { display: block; color: var(--muted); font-size: 11px; margin-top: 5px; }
    .segments { display: grid; grid-template-columns: repeat(5, minmax(0, 1fr)); gap: 4px; padding: 4px; background: #f2f5f7; border: 1px solid var(--line-soft); border-radius: 8px; }
    .segment { min-height: 30px; padding: 4px 6px; border: 0; border-radius: 6px; color: var(--muted); background: transparent; font-size: 12px; }
    .segment.active { background: #fff; color: var(--ink); box-shadow: 0 1px 3px rgba(23, 33, 43, .08); }
    .panel-title { display: flex; justify-content: space-between; align-items: center; gap: 10px; min-height: 64px; background: rgba(255,255,255,.94); }
    .chat-profile { display: flex; align-items: center; gap: 11px; min-width: 0; }
    .avatar { width: 38px; height: 38px; border-radius: 999px; display: grid; place-items: center; background: linear-gradient(135deg, var(--brand-soft), var(--accent-soft)); color: var(--brand); font-weight: 800; border: 1px solid #c9d9e7; flex: 0 0 auto; }
    .chat-title { display: grid; gap: 2px; }
    .chat-actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; justify-content: flex-end; }
    input, textarea, button, select { font: inherit; }
    input, textarea, select { width: 100%; border: 1px solid var(--line); border-radius: 6px; padding: 9px 10px; background: #fff; color: var(--ink); outline: none; }
    input:focus, textarea:focus, select:focus { border-color: var(--brand); box-shadow: 0 0 0 3px rgba(13, 122, 86, .11); }
    textarea { min-height: 88px; resize: vertical; line-height: 1.45; }
    button { border: 1px solid var(--line); background: #fff; color: var(--ink); border-radius: 6px; padding: 8px 10px; cursor: pointer; white-space: nowrap; font-weight: 650; }
    button:hover { border-color: #b8c1b4; background: #fbfcfa; }
    button.primary { background: var(--brand); color: #fff; border-color: var(--brand); }
    button.primary:hover { background: var(--brand-dark); border-color: var(--brand-dark); }
    button.warn { border-color: #d8a948; color: var(--warn); background: var(--warn-bg); }
    button.ghost { color: var(--muted); background: transparent; }
    button.compact { padding: 7px 9px; font-size: 13px; }
    .list { overflow: auto; height: calc(100vh - 370px); background: #fbfcfd; }
    .row { padding: 12px 14px; border-bottom: 1px solid var(--line-soft); cursor: pointer; transition: background .12s ease, border-color .12s ease; background: #fff; }
    .row:hover { background: #f7fafc; }
    .row.active { background: #eef8f3; box-shadow: inset 3px 0 0 var(--brand); }
    .row-main { display: grid; grid-template-columns: 34px 1fr; gap: 10px; align-items: start; }
    .row .avatar { width: 34px; height: 34px; font-size: 12px; }
    .row-head { display: flex; align-items: center; justify-content: space-between; gap: 8px; min-width: 0; }
    .phone { font-weight: 750; font-size: 14px; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .time { color: var(--faint); font-size: 11px; flex: 0 0 auto; }
    .latest { color: var(--muted); font-size: 13px; margin-top: 5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .row-meta { display: flex; align-items: center; gap: 6px; flex-wrap: wrap; margin-top: 8px; }
    .pill { display: inline-flex; align-items: center; min-height: 22px; font-size: 12px; border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; color: var(--muted); background: #fff; }
    .pill.human { color: var(--warn); border-color: #e0be75; background: var(--warn-bg); }
    .pill.ok { color: var(--brand); border-color: #a7d6bd; background: #eff9f3; }
    .pill.alert { color: var(--danger); border-color: #e3b7b7; background: var(--danger-soft); }
    .pill.info { color: var(--accent); border-color: #bdd1fb; background: var(--accent-soft); }
    .empty { height: 100%; display: grid; place-items: center; text-align: center; color: var(--muted); padding: 24px; }
    .empty strong { display: block; color: var(--ink); font-size: 16px; margin-bottom: 6px; }
    .messages { padding: 18px 18px 20px; overflow: auto; height: calc(100vh - 240px); display: flex; flex-direction: column; gap: 12px; }
    .bubble { max-width: min(78%, 720px); padding: 11px 13px; border-radius: 8px; line-height: 1.48; white-space: pre-wrap; word-break: break-word; border: 1px solid var(--line); box-shadow: 0 2px 8px rgba(23, 33, 43, .03); }
    .inbound { align-self: flex-start; background: #fff; }
    .outbound { align-self: flex-end; background: #e6f4ec; border-color: #b8dbc5; }
    .meta { color: var(--faint); font-size: 12px; margin-bottom: 5px; }
    .composer { padding: 12px; border-top: 1px solid var(--line); background: rgba(255,255,255,.95); display: grid; grid-template-columns: 1fr auto; gap: 8px; }
    .composer textarea { min-height: 74px; }
    .sidebody { height: calc(100vh - 60px); overflow: auto; padding: 14px; display: grid; gap: 12px; align-content: start; }
    .profile-card { border: 1px solid var(--line-soft); border-radius: 8px; background: #fff; padding: 13px; display: grid; gap: 10px; }
    .profile-top { display: flex; gap: 10px; align-items: center; min-width: 0; }
    .profile-top .avatar { width: 42px; height: 42px; }
    .profile-phone { font-weight: 800; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .profile-notes { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 8px; }
    .metric { border-radius: 8px; background: var(--panel-soft); border: 1px solid var(--line-soft); padding: 9px; }
    .metric strong { display: block; font-size: 13px; margin-bottom: 3px; }
    .kv { border: 1px solid var(--line-soft); border-radius: 8px; padding: 11px; background: #fff; }
    .kv.flat { border: 0; border-radius: 0; border-bottom: 1px solid var(--line-soft); padding: 0 0 10px; }
    .label { color: var(--muted); font-size: 12px; margin-bottom: 6px; font-weight: 700; }
    .mini { color: var(--muted); font-size: 12px; line-height: 1.45; }
    .flag, .match { border: 1px solid var(--line-soft); border-radius: 8px; padding: 10px; margin-top: 8px; background: #fff; }
    .match strong, .flag strong { display: block; margin-bottom: 5px; }
    .match textarea { min-height: 118px; margin-top: 8px; }
    .stack { display: grid; gap: 8px; }
    .chips { display: flex; gap: 6px; flex-wrap: wrap; }
    .chip { display: inline-flex; gap: 5px; align-items: center; border: 1px solid var(--line); border-radius: 999px; padding: 5px 9px; font-size: 12px; background: #fff; color: var(--ink); }
    .chip input { width: auto; margin: 0; accent-color: var(--brand); }
    .state { display: grid; gap: 5px; font-size: 13px; background: var(--panel-soft); border: 1px solid var(--line-soft); border-radius: 8px; padding: 10px; }
    .state .ready { color: var(--brand); font-weight: 750; }
    .state .missing { color: var(--warn); font-weight: 750; }
    details summary { cursor: pointer; }
    pre { margin: 0; white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; background: #f4f5f2; padding: 10px; border-radius: 6px; border: 1px solid var(--line-soft); }
    @media (max-width: 1080px) {
      main { grid-template-columns: 300px minmax(360px, 1fr); }
      aside:last-child { grid-column: 1 / -1; border-left: 0; border-top: 1px solid var(--line); }
      .sidebody { height: auto; grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
    @media (max-width: 760px) {
      header { height: auto; min-height: 58px; align-items: flex-start; padding: 12px; gap: 10px; }
      main { grid-template-columns: 1fr; height: auto; }
      aside:first-child, aside:last-child { border: 0; border-bottom: 1px solid var(--line); }
      .list { height: 320px; }
      .messages { height: 420px; }
      .sidebody { grid-template-columns: 1fr; height: auto; }
      .composer { grid-template-columns: 1fr; }
      .segments { grid-template-columns: repeat(2, minmax(0, 1fr)); }
    }
  </style>
</head>
<body>
  <header>
    <div>
      <h1>家長學堂 Agent Inbox</h1>
      <div class="subtle">WhatsApp 對話、家長記憶、主動推送草稿</div>
    </div>
    <button class="ghost" onclick="loadConversations()">刷新</button>
  </header>
  <main>
    <aside>
      <div class="inbox-head">
        <div class="inbox-title">
          <strong>Inbox</strong>
          <span class="pill" id="conversationCount">0 對話</span>
        </div>
        <div class="queue-stats" id="queueStats">
          <div class="stat"><b>0</b><span>待處理</span></div>
          <div class="stat"><b>0</b><span>人工中</span></div>
          <div class="stat"><b>0</b><span>可推送</span></div>
        </div>
      </div>
      <div class="toolbar stack">
        <input id="search" placeholder="搜尋電話、訊息、Profile" oninput="loadConversations()">
        <div class="segments" id="filterSegments">
          <button class="segment active" data-filter="" onclick="setConversationFilter('')">全部</button>
          <button class="segment" data-filter="human" onclick="setConversationFilter('human')">接手</button>
          <button class="segment" data-filter="ai" onclick="setConversationFilter('ai')">AI</button>
          <button class="segment" data-filter="flagged" onclick="setConversationFilter('flagged')">待處理</button>
          <button class="segment" data-filter="pushable" onclick="setConversationFilter('pushable')">可推送</button>
        </div>
        <select id="filterStatus" onchange="loadConversations()">
          <option value="">全部對話</option>
          <option value="human">人工接手</option>
          <option value="ai">AI 自動</option>
          <option value="flagged">有待處理</option>
          <option value="pushable">可推送</option>
          <option value="draft">有草稿</option>
        </select>
      </div>
      <div class="toolbar tools">
        <button onclick="loadFlags()">不確定隊列</button>
        <button onclick="loadAgentTasks()">Agent 任務</button>
        <button onclick="loadQaFeedback()">QA 回饋</button>
        <button onclick="loadMatches()">主動匹配</button>
        <button onclick="loadDrafts('draft')">待發草稿</button>
        <button onclick="loadDrafts('all')">推送紀錄</button>
      </div>
      <div class="toolbar tools">
        <select id="draftStatus">
          <option value="draft">待發</option>
          <option value="sent">已發送</option>
          <option value="skipped">已略過</option>
          <option value="failed">失敗</option>
          <option value="all">全部紀錄</option>
        </select>
        <select id="draftConsent">
          <option value="">全部同意狀態</option>
          <option value="allowed">已同意</option>
          <option value="unknown">未確認</option>
          <option value="paused">已暫停</option>
        </select>
        <input id="draftSearch" placeholder="搜尋草稿、課程、電話">
        <button onclick="loadDraftsFromControls()">查草稿</button>
      </div>
      <div id="list" class="list"></div>
    </aside>
    <section>
      <div class="toolbar panel-title">
        <div class="chat-profile">
          <div class="avatar" id="chatAvatar">--</div>
          <div class="chat-title">
            <strong id="chatTitle">未選擇對話</strong>
            <span class="subtle" id="chatSubline">選擇左側家長後可查看完整上下文</span>
          </div>
        </div>
        <div class="chat-actions">
          <button class="warn compact" onclick="takeover()">接手</button>
          <button class="compact" onclick="resumeAi()">恢復 AI</button>
        </div>
      </div>
      <div id="messages" class="messages"><div class="empty"><div><strong>選擇一位家長</strong><span>對話、AI 記憶和推送草稿會在這裡接上。</span></div></div></div>
      <div class="composer">
        <textarea id="reply" placeholder="人工回覆"></textarea>
        <button class="primary" onclick="sendReply()">傳送</button>
      </div>
    </section>
    <aside>
      <div class="sidebody">
        <div class="profile-card">
          <div class="profile-top">
            <div class="avatar" id="profileAvatar">--</div>
            <div style="min-width:0">
              <div class="profile-phone" id="profilePhone">未選擇家長</div>
              <div class="subtle" id="profileSummary">等待對話資料</div>
            </div>
          </div>
          <div class="profile-notes">
            <div><div class="label">狀態</div><div id="status">-</div></div>
            <div><div class="label">同意</div><div id="consentBadge">-</div></div>
          </div>
        </div>
        <div class="kv"><div class="label">Agent State</div><div id="agentState" class="state">未選擇</div></div>
        <div class="kv"><div class="label">Harness Trace</div><div id="harnessRoute" class="state">Harness: -</div></div>
        <div style="display:flex; gap:8px; flex-wrap:wrap">
          <button class="warn" onclick="takeover()">人工接手</button>
          <button onclick="resumeAi()">恢復 AI</button>
        </div>
        <div class="kv stack">
          <div class="label">結構化 Profile</div>
          <div class="mini">孩子年齡</div>
          <div id="profileAgeGroups" class="chips"></div>
          <div class="mini">家長痛點</div>
          <div id="profilePainPoints" class="chips"></div>
          <div class="mini">對象</div>
          <select id="profileTarget"></select>
          <div class="mini">主題</div>
          <select id="profileTopic"></select>
          <textarea id="profilePainSummary" placeholder="痛點摘要，例如：青春期壓力和親子衝突"></textarea>
          <button onclick="saveProfile()">儲存 Profile</button>
        </div>
        <div class="kv">
          <div class="label">家長標籤</div>
          <input id="tags" placeholder="例：情緒壓力, 青少年, 高關注">
        </div>
        <div class="kv">
          <div class="label">備註</div>
          <textarea id="notes" placeholder="只給管理員看的備註"></textarea>
        </div>
        <div class="kv">
          <div class="label">主動推送同意</div>
          <select id="consentStatus">
            <option value="unknown">未確認</option>
            <option value="allowed">同意主動推送</option>
            <option value="paused">暫停主動推送</option>
          </select>
          <textarea id="proactiveNotes" placeholder="推送偏好或同意來源"></textarea>
          <button onclick="saveMeta()">儲存標籤/備註/同意</button>
        </div>
        <div class="kv"><details><summary class="label">原始 Profile JSON</summary><pre id="profile">{}</pre></details></div>
        <div class="kv"><div class="label">上次查詢</div><pre id="lastQuery">{}</pre></div>
        <div class="kv stack">
          <div class="label">朋友測試 QA</div>
          <select id="qaIssueType">
            <option value="classification_error">分類錯</option>
            <option value="missed_course">漏課程</option>
            <option value="off_topic_should_block">不應回答</option>
            <option value="unclear_reply">回覆不清楚</option>
            <option value="onboarding_gap">訪談沒問好</option>
            <option value="link_error">連結錯</option>
            <option value="handoff_needed">應人工接手</option>
            <option value="good_reply">回答好</option>
            <option value="other">其他</option>
          </select>
          <textarea id="qaSummary" placeholder="發生什麼問題，例如：朋友問青少年情緒，AI 回了親子活動"></textarea>
          <textarea id="qaExpected" placeholder="正確應該怎樣回，例如：保留 13-18歲 + 家長課 + 情緒壓力"></textarea>
          <button onclick="submitQaFeedback()">記錄 QA 標記</button>
          <div id="qaFeedback" class="mini">尚未載入</div>
        </div>
        <div class="kv"><div class="label">Agent 任務隊列</div><div id="agentTasks" class="mini">尚未載入</div></div>
        <div class="kv"><div class="label">不確定隊列</div><div id="flags" class="mini">尚未載入</div></div>
        <div class="kv"><div class="label">主動匹配草稿</div><div id="matches" class="mini">尚未產生</div></div>
        <div class="kv stack">
          <div class="label">隱私清理</div>
          <input id="retentionDays" type="number" min="1" max="3650" value="90">
          <div class="mini" id="privacyResult">先預覽，再執行。Profile 和未發草稿不會被清掉。</div>
          <div style="display:flex; gap:8px; flex-wrap:wrap">
            <button onclick="prunePrivacy(true)">預覽</button>
            <button class="warn" onclick="prunePrivacy(false)">清理舊紀錄</button>
          </div>
        </div>
      </div>
    </aside>
  </main>
  <script>
    let conversations = [];
    let currentPhone = "";

    async function api(path, options = {}) {
      const res = await fetch(path, {
        headers: {"Content-Type": "application/json"},
        ...options
      });
      if (!res.ok) {
        const text = await res.text();
        let message = text;
        try { message = JSON.parse(text).detail || text; } catch {}
        throw new Error(message);
      }
      return res.json();
    }
    function esc(text) {
      const map = {"&":"&amp;","<":"&lt;",">":"&gt;"};
      map['"'] = "&quot;";
      map["'"] = "&#39;";
      return String(text || "").replace(/[&<>"']/g, c => map[c]);
    }
    function avatarText(phone) {
      const digits = String(phone || "").replace(/\\D/g, "");
      return digits.slice(-2) || "--";
    }
    function formatTime(value) {
      if (!value) return "";
      const parsed = new Date(value);
      if (Number.isNaN(parsed.getTime())) return String(value).slice(0, 10);
      return parsed.toLocaleString("zh-Hant", {month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit"});
    }
    function consentLabel(status) {
      if (status === "allowed") return ["可推送", "ok"];
      if (status === "paused") return ["暫停", "human"];
      return ["未同意", ""];
    }
    function compactProfileSummary(profile) {
      const parts = [];
      if ((profile.age_groups || []).length) parts.push((profile.age_groups || []).join("、"));
      if ((profile.pain_points || []).length) parts.push((profile.pain_points || []).slice(0, 2).join("、"));
      if (profile.topic) parts.push(profile.topic);
      if (profile.target) parts.push(profile.target);
      return parts.join(" · ") || "未建立完整 Profile";
    }
    function setConversationFilter(filter) {
      document.getElementById("filterStatus").value = filter;
      document.querySelectorAll("#filterSegments .segment").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.filter === filter);
      });
      loadConversations();
    }
    function syncFilterSegments() {
      const filter = document.getElementById("filterStatus").value;
      document.querySelectorAll("#filterSegments .segment").forEach(btn => {
        btn.classList.toggle("active", btn.dataset.filter === filter);
      });
    }
    function updateQueueStats() {
      const total = conversations.length;
      const flagged = conversations.reduce((sum, c) => sum + (Number(c.open_flags_count || 0) > 0 || Number(c.qa_feedback_count || 0) > 0 ? 1 : 0), 0);
      const human = conversations.filter(c => c.status === "human").length;
      const pushable = conversations.filter(c => c.consent_status === "allowed").length;
      document.getElementById("conversationCount").textContent = `${total} 對話`;
      document.getElementById("queueStats").innerHTML = `
        <div class="stat"><b>${flagged}</b><span>待處理</span></div>
        <div class="stat"><b>${human}</b><span>人工中</span></div>
        <div class="stat"><b>${pushable}</b><span>可推送</span></div>
      `;
    }
    async function loadConversations() {
      const params = new URLSearchParams();
      params.set("limit", "100");
      const q = document.getElementById("search").value.trim();
      const filter = document.getElementById("filterStatus").value;
      if (q) params.set("search", q);
      if (filter === "ai" || filter === "human") {
        params.set("status", filter);
      } else if (filter) {
        params.set("filter", filter);
      }
      const data = await api("/api/whatsapp/conversations?" + params.toString());
      conversations = data.conversations || [];
      syncFilterSegments();
      renderList();
      loadAgentTasks().catch(() => {});
      if (currentPhone) await openChat(currentPhone);
    }
    function renderList() {
      updateQueueStats();
      if (!conversations.length) {
        document.getElementById("list").innerHTML = `<div class="empty"><div><strong>沒有符合條件的對話</strong><span>換個搜尋或篩選看看。</span></div></div>`;
        return;
      }
      document.getElementById("list").innerHTML = conversations.map(c => {
        const [consentText, consentClass] = consentLabel(c.consent_status);
        return `<div class="row ${c.phone === currentPhone ? "active" : ""}" onclick="openChat('${esc(c.phone)}')">
          <div class="row-main">
            <div class="avatar">${esc(avatarText(c.phone))}</div>
            <div style="min-width:0">
              <div class="row-head">
                <div class="phone">${esc(c.display_name || c.phone)}</div>
                <div class="time">${esc(formatTime(c.last_message_at || c.updated_at))}</div>
              </div>
              <div class="latest">${esc(c.latest_message || c.recommended_action || "")}</div>
              <div class="row-meta">
                <span class="pill ${c.status === "human" ? "human" : "ok"}">${c.status === "human" ? "人工接手" : "AI 自動"}</span>
                <span class="pill ${consentClass}">${esc(consentText)}</span>
                ${c.profile_ready ? `<span class="pill info">Profile OK</span>` : ""}
                ${Number(c.open_flags_count || 0) ? `<span class="pill alert">待處理 ${Number(c.open_flags_count || 0)}</span>` : ""}
                ${Number(c.qa_feedback_count || 0) ? `<span class="pill alert">QA ${Number(c.qa_feedback_count || 0)}</span>` : ""}
                ${Number(c.draft_count || 0) ? `<span class="pill">草稿 ${Number(c.draft_count || 0)}</span>` : ""}
              </div>
            </div>
          </div>
        </div>`;
      }).join("");
    }
    function renderAgentState(state) {
      const readyClass = state.profile_ready ? "ready" : "missing";
      const route = state.last_harness_route || "-";
      const action = state.last_harness_action || "-";
      const intent = state.last_harness_intent ? ` · intent: ${esc(state.last_harness_intent)}` : "";
      const purpose = state.last_harness_llm_purpose ? ` · ${esc(state.last_harness_llm_purpose)}` : "";
      document.getElementById("harnessRoute").innerHTML = `
        <div>Harness: ${esc(route)} / ${esc(action)}</div>
        <div>LLM: ${state.last_harness_allow_llm ? "yes" : "no"}${intent}${purpose}</div>
      `;
      document.getElementById("agentState").innerHTML = `
        <div>狀態：<span class="${readyClass}">${state.profile_ready ? "資料足夠" : "需要補資料"}</span></div>
        <div>下一步：${esc(state.recommended_action || "-")}</div>
        <div>缺少：${esc((state.missing_fields || []).join(", ") || "無")}</div>
        <div>待處理：${Number(state.open_flags_count || 0)} · QA：${Number(state.qa_feedback_count || 0)} · 草稿：${Number(state.draft_count || 0)}</div>
      `;
    }
    function renderCheckboxGroup(containerId, name, options, selected) {
      const chosen = new Set(selected || []);
      document.getElementById(containerId).innerHTML = (options || []).map(value => `
        <label class="chip">
          <input type="checkbox" name="${name}" value="${esc(value)}" ${chosen.has(value) ? "checked" : ""}>
          ${esc(value)}
        </label>
      `).join("");
    }
    function checkedValues(name) {
      return Array.from(document.querySelectorAll(`input[name="${name}"]:checked`)).map(el => el.value);
    }
    function setSelectOptions(id, options, selected, emptyLabel) {
      document.getElementById(id).innerHTML = [`<option value="">${esc(emptyLabel)}</option>`]
        .concat((options || []).map(value => `<option value="${esc(value)}" ${value === selected ? "selected" : ""}>${esc(value)}</option>`))
        .join("");
    }
    function renderProfileControls(profile, options) {
      renderCheckboxGroup("profileAgeGroups", "profileAgeGroup", options.age_groups || [], profile.age_groups || []);
      renderCheckboxGroup("profilePainPoints", "profilePainPoint", options.pain_points || [], profile.pain_points || []);
      setSelectOptions("profileTarget", options.targets || [], profile.target || "", "未設定");
      setSelectOptions("profileTopic", options.topics || [], profile.topic || "", "未設定");
      document.getElementById("profilePainSummary").value = profile.pain_summary || "";
    }
    async function openChat(phone) {
      currentPhone = phone;
      const data = await api("/api/whatsapp/conversations/" + encodeURIComponent(phone));
      const profile = data.profile || {};
      const conversation = data.conversation || {};
      const [consentText, consentClass] = consentLabel(conversation.consent_status);
      document.getElementById("chatAvatar").textContent = avatarText(phone);
      document.getElementById("chatTitle").textContent = conversation.display_name || phone;
      document.getElementById("chatSubline").textContent = compactProfileSummary(profile);
      document.getElementById("profileAvatar").textContent = avatarText(phone);
      document.getElementById("profilePhone").textContent = conversation.display_name || phone;
      document.getElementById("profileSummary").textContent = compactProfileSummary(profile);
      document.getElementById("status").innerHTML = data.conversation.status === "human"
        ? `<span class="pill human">人工接手中</span>`
        : `<span class="pill ok">AI 自動回覆</span>`;
      document.getElementById("consentBadge").innerHTML = `<span class="pill ${consentClass}">${esc(consentText)}</span>`;
      document.getElementById("tags").value = (data.conversation.tags || []).join(", ");
      document.getElementById("notes").value = data.conversation.notes || "";
      document.getElementById("consentStatus").value = data.conversation.consent_status || "unknown";
      document.getElementById("proactiveNotes").value = data.conversation.proactive_notes || "";
      document.getElementById("profile").textContent = JSON.stringify(data.profile || {}, null, 2);
      document.getElementById("lastQuery").textContent = JSON.stringify(data.last_query || {}, null, 2);
      renderAgentState(data.agent_state || {});
      renderProfileControls(data.profile || {}, data.profile_options || {});
      document.getElementById("flags").innerHTML = (data.flags || []).length ? (data.flags || []).map(f => `
        <div class="flag">
          <strong>${esc(f.flag_type)} · ${esc(f.phone)}</strong>
          <div>${esc(f.summary)}</div>
          <button onclick="resolveFlag(${Number(f.id)})">標記已處理</button>
        </div>`).join("") : "目前沒有待處理項目";
      renderQaFeedback(data.qa_feedback || []);
      document.getElementById("matches").innerHTML = (data.drafts || []).length ? (data.drafts || []).map(d => `
        <div class="match">
          <strong>草稿 #${Number(d.id)}</strong>
          <div class="mini">${esc(d.updated_at || "")}</div>
          <textarea id="draft-${Number(d.id)}">${esc(d.draft_text || "")}</textarea>
          <button onclick="saveDraft(${Number(d.id)})">儲存修改</button>
          <button onclick="sendQueuedDraft(${Number(d.id)})">發送</button>
          <button onclick="skipDraft(${Number(d.id)})">略過</button>
        </div>`).join("") : "目前沒有待發草稿";
      document.getElementById("messages").innerHTML = (data.messages || []).length ? (data.messages || []).map(m => `
        <div class="bubble ${m.direction}">
          <div class="meta">${esc(m.source)} · ${esc(m.created_at)}</div>${esc(m.body)}
          ${m.source === "ai" ? `<div style="margin-top:8px; display:flex; gap:6px; flex-wrap:wrap">
            <button class="compact" onclick="submitQaQuick(${Number(m.id)}, 'good', 'good_reply')">回答好</button>
            <button class="compact" onclick="submitQaQuick(${Number(m.id)}, 'bad', 'unclear_reply')">回答差</button>
            <button class="compact" onclick="submitQaQuick(${Number(m.id)}, 'bad', 'missed_course')">漏課</button>
            <button class="compact" onclick="submitQaQuick(${Number(m.id)}, 'bad', 'off_topic_should_block')">不應答</button>
          </div>` : ""}
        </div>`).join("") : `<div class="empty"><div><strong>還沒有訊息</strong><span>可以先人工傳一則開場訊息。</span></div></div>`;
      renderList();
    }
    async function saveMeta() {
      if (!currentPhone) return;
      const tags = document.getElementById("tags").value.split(",").map(t => t.trim()).filter(Boolean);
      const notes = document.getElementById("notes").value;
      const consent_status = document.getElementById("consentStatus").value;
      const proactive_notes = document.getElementById("proactiveNotes").value;
      await api("/api/whatsapp/conversations/" + encodeURIComponent(currentPhone), {
        method: "POST",
        body: JSON.stringify({tags, notes, consent_status, proactive_notes})
      });
      await loadConversations();
    }
    async function saveProfile() {
      if (!currentPhone) return;
      await api("/api/whatsapp/conversations/" + encodeURIComponent(currentPhone) + "/profile", {
        method: "POST",
        body: JSON.stringify({
          age_groups: checkedValues("profileAgeGroup"),
          pain_points: checkedValues("profilePainPoint"),
          target: document.getElementById("profileTarget").value,
          topic: document.getElementById("profileTopic").value,
          pain_summary: document.getElementById("profilePainSummary").value
        })
      });
      await loadConversations();
    }
    async function takeover() {
      if (!currentPhone) return;
      await api("/api/whatsapp/conversations/" + encodeURIComponent(currentPhone) + "/takeover", {method: "POST"});
      await loadConversations();
    }
    async function resumeAi() {
      if (!currentPhone) return;
      await api("/api/whatsapp/conversations/" + encodeURIComponent(currentPhone) + "/resume-ai", {method: "POST"});
      await loadConversations();
    }
    async function sendReply() {
      const body = document.getElementById("reply").value.trim();
      if (!currentPhone || !body) return;
      await api("/api/whatsapp/conversations/" + encodeURIComponent(currentPhone) + "/messages", {
        method: "POST",
        body: JSON.stringify({body})
      });
      document.getElementById("reply").value = "";
      await loadConversations();
    }
    async function loadFlags() {
      const data = await api("/api/whatsapp/flags");
      const flags = data.flags || [];
      document.getElementById("flags").innerHTML = flags.length ? flags.map(f => `
        <div class="flag">
          <strong>${esc(f.flag_type)} · ${esc(f.phone)}</strong>
          <div>${esc(f.summary)}</div>
          <button onclick="resolveFlag(${Number(f.id)})">標記已處理</button>
        </div>`).join("") : "目前沒有待處理項目";
    }
    async function resolveFlag(id) {
      await api("/api/whatsapp/flags/" + id + "/resolve", {method: "POST"});
      await loadFlags();
    }
    function taskTypeLabel(type) {
      const labels = {
        review_qa_feedback: "QA 回饋",
        review_flag: "待人工判斷",
        human_takeover: "人工接手",
        ask_age: "追問年齡",
        ask_concern: "追問痛點",
        approve_draft: "審批草稿",
        ask_consent: "確認同意",
        generate_draft: "可產生草稿"
      };
      return labels[type] || type;
    }
    function issueTypeLabel(type) {
      const labels = {
        good_reply: "回答好",
        classification_error: "分類錯",
        missed_course: "漏課程",
        off_topic_should_block: "不應回答",
        unclear_reply: "回覆不清楚",
        handoff_needed: "應人工接手",
        link_error: "連結錯",
        onboarding_gap: "訪談沒問好",
        other: "其他"
      };
      return labels[type] || type;
    }
    function renderQaFeedback(items) {
      document.getElementById("qaFeedback").innerHTML = items.length ? items.map(item => `
        <div class="flag">
          <strong>${esc(issueTypeLabel(item.issue_type))} · ${esc(item.rating)}</strong>
          <div>${esc(item.summary || "沒有補充摘要")}</div>
          ${item.expected_behavior ? `<div class="mini">期望：${esc(item.expected_behavior)}</div>` : ""}
          <details><summary>匿名樣本</summary><pre>${esc(JSON.stringify(item.anonymized_sample || {}, null, 2))}</pre></details>
          <button onclick="markQaFeedback(${Number(item.id)}, 'converted')">已轉測試/規則</button>
          <button onclick="markQaFeedback(${Number(item.id)}, 'closed')">關閉</button>
        </div>
      `).join("") : "目前沒有開放 QA 標記";
    }
    async function submitQaQuick(messageId, rating, issueType) {
      if (!currentPhone) return;
      await api("/api/whatsapp/conversations/" + encodeURIComponent(currentPhone) + "/qa-feedback", {
        method: "POST",
        body: JSON.stringify({
          message_id: messageId,
          rating,
          issue_type: issueType,
          summary: issueType === "good_reply" ? "朋友測試標記：回答好" : "朋友測試標記：" + issueTypeLabel(issueType),
          expected_behavior: ""
        })
      });
      await loadConversations();
      await openChat(currentPhone);
    }
    async function submitQaFeedback() {
      if (!currentPhone) return;
      await api("/api/whatsapp/conversations/" + encodeURIComponent(currentPhone) + "/qa-feedback", {
        method: "POST",
        body: JSON.stringify({
          rating: document.getElementById("qaIssueType").value === "good_reply" ? "good" : "bad",
          issue_type: document.getElementById("qaIssueType").value,
          summary: document.getElementById("qaSummary").value,
          expected_behavior: document.getElementById("qaExpected").value
        })
      });
      document.getElementById("qaSummary").value = "";
      document.getElementById("qaExpected").value = "";
      await loadConversations();
      await openChat(currentPhone);
    }
    async function loadQaFeedback(status = "open") {
      const data = await api("/api/whatsapp/qa-feedback?status=" + encodeURIComponent(status));
      renderQaFeedback(data.feedback || []);
    }
    async function markQaFeedback(id, status) {
      await api("/api/whatsapp/qa-feedback/" + id + "/status", {
        method: "POST",
        body: JSON.stringify({status})
      });
      if (currentPhone) await openChat(currentPhone);
      else await loadQaFeedback();
      await loadAgentTasks().catch(() => {});
    }
    async function loadAgentTasks() {
      const q = document.getElementById("search").value.trim();
      const params = new URLSearchParams();
      params.set("limit", "100");
      if (q) params.set("search", q);
      const data = await api("/api/whatsapp/agent-tasks?" + params.toString());
      const tasks = data.tasks || [];
      const counts = (data.briefing || {}).by_type || {};
      const countLine = Object.entries(counts).map(([key, value]) => `${taskTypeLabel(key)}: ${value}`).join(" · ");
      document.getElementById("agentTasks").innerHTML = tasks.length ? `
        <div class="state" style="margin-bottom:8px">
          <div>任務：${Number(data.total || 0)} · 家長：${Number((data.briefing || {}).parents || 0)}</div>
          <div>${esc(countLine || "無分類")}</div>
        </div>
        ${tasks.map(t => `
          <div class="flag">
            <strong>${esc(t.title)} · ${esc(t.phone)}</strong>
            <div>${esc(t.summary)}</div>
            <div class="mini">下一步：${esc(t.action)}</div>
            <button onclick="openChat('${esc(t.phone)}')">打開對話</button>
          </div>
        `).join("")}
      ` : "目前沒有需要處理的 agent 任務";
    }
    async function loadMatches() {
      const data = await api("/api/whatsapp/proactive-drafts/generate", {
        method: "POST",
        body: JSON.stringify({allowed_only: true})
      });
      const drafts = data.drafts || [];
      document.getElementById("matches").innerHTML = drafts.length ? drafts.map(d => `
        <div class="match">
          <strong>${esc(d.phone)}</strong>
          <div class="mini">${d.conversation.consent_status === "allowed" ? "已同意主動推送" : "未同意或已暫停"} · 已保存到待發隊列</div>
          <div class="mini">${esc((d.profile.pain_points || []).join("、"))}</div>
          ${(d.matches || []).map(m => `<div style="margin-top:8px">
            <strong>${esc(m.course.name)}</strong>
            <div>${esc((m.reasons || []).join("、"))}</div>
            <div class="mini">${esc(m.course.date || "")}</div>
          </div>`).join("")}
          <textarea id="draft-${Number(d.id)}">${esc(d.draft_text || "")}</textarea>
          <button onclick="saveDraft(${Number(d.id)})">儲存修改</button>
          <button onclick="sendQueuedDraft(${Number(d.id)})">發送</button>
          <button onclick="skipDraft(${Number(d.id)})">略過</button>
          <button onclick="openChat('${esc(d.phone)}')">打開對話</button>
        </div>`).join("") : "目前沒有足夠記憶可主動匹配";
    }
    async function loadDraftsFromControls() {
      await loadDrafts(
        document.getElementById("draftStatus").value || "draft",
        document.getElementById("draftSearch").value.trim(),
        document.getElementById("draftConsent").value
      );
    }
    async function loadDrafts(status = "draft", search = "", consent = "") {
      document.getElementById("draftStatus").value = status;
      const params = new URLSearchParams();
      params.set("status", status);
      if (search) params.set("search", search);
      if (consent) params.set("consent_status", consent);
      const data = await api("/api/whatsapp/proactive-drafts?" + params.toString());
      const drafts = data.drafts || [];
      document.getElementById("matches").innerHTML = drafts.length ? drafts.map(d => `
        <div class="match">
          <strong>${esc(d.phone)}</strong>
          <span class="pill">${esc(d.status)}</span>
          <div class="mini">${d.conversation.consent_status === "allowed" ? "已同意主動推送" : "未同意或已暫停"} · ${esc(d.sent_message_type || "")}</div>
          <div class="mini">${esc(d.updated_at || "")}</div>
          ${d.error_text ? `<div class="mini">錯誤：${esc(d.error_text)}</div>` : ""}
          ${d.original_text ? `<details><summary>AI 原始草稿</summary><pre>${esc(d.original_text)}</pre></details>` : ""}
          ${d.sent_text ? `<details><summary>最後發送內容</summary><pre>${esc(d.sent_text)}</pre></details>` : ""}
          ${(d.matches || []).length ? `<details><summary>匹配原因</summary>${(d.matches || []).map(m => `
            <div style="margin-top:8px">
              <strong>${esc((m.course || {}).name || "")}</strong>
              <div>${esc((m.reasons || []).join("、"))}</div>
            </div>`).join("")}</details>` : ""}
          <textarea id="draft-${Number(d.id)}">${esc(d.draft_text || "")}</textarea>
          ${d.status === "draft" ? `
            <button onclick="saveDraft(${Number(d.id)})">儲存修改</button>
            <button onclick="sendQueuedDraft(${Number(d.id)})">發送</button>
            <button onclick="skipDraft(${Number(d.id)})">略過</button>
          ` : ""}
          <button onclick="openChat('${esc(d.phone)}')">打開對話</button>
        </div>`).join("") : "目前沒有草稿";
    }
    async function prunePrivacy(dryRun = true) {
      const days = Number(document.getElementById("retentionDays").value || 90);
      if (!dryRun && !confirm("確認清理 " + days + " 天前的舊訊息、LLM cache、已處理紀錄？")) return;
      const data = await api("/api/whatsapp/privacy/prune", {
        method: "POST",
        body: JSON.stringify({older_than_days: days, dry_run: dryRun})
      });
      const counts = data.counts || {};
      document.getElementById("privacyResult").textContent =
        (dryRun ? "預覽：" : "已清理：") +
        `訊息 ${counts.messages || 0}、LLM cache ${counts.llm_cache || 0}、去重紀錄 ${counts.processed_message_ids || 0}、已解決 flags ${counts.resolved_flags || 0}、舊推送紀錄 ${counts.closed_proactive_drafts || 0}、已關閉 QA ${counts.closed_qa_feedback || 0}`;
    }
    async function saveDraft(id) {
      const body = document.getElementById("draft-" + id).value.trim();
      if (!body) return;
      try {
        await api("/api/whatsapp/proactive-drafts/" + id, {
          method: "POST",
          body: JSON.stringify({body})
        });
        await loadDrafts("draft");
      } catch (err) {
        alert(err.message);
      }
    }
    async function sendQueuedDraft(id) {
      const body = document.getElementById("draft-" + id).value.trim();
      if (!body) return;
      try {
        await api("/api/whatsapp/proactive-drafts/" + id + "/send", {
          method: "POST",
          body: JSON.stringify({body})
        });
        await loadConversations();
        await loadDrafts("draft");
      } catch (err) {
        alert(err.message);
        await loadDrafts("draft");
      }
    }
    async function skipDraft(id) {
      try {
        await api("/api/whatsapp/proactive-drafts/" + id + "/skip", {method: "POST"});
        await loadDrafts("draft");
      } catch (err) {
        alert(err.message);
      }
    }
    loadConversations().catch(err => alert(err.message));
  </script>
</body>
</html>""", headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"})


@app.get("/api/whatsapp/conversations")
async def api_whatsapp_conversations(
    request: Request,
    limit: int = 50,
    status: str = "",
    filter: str = "",
    search: str = "",
    consent_status: str = "",
):
    """List WhatsApp parent conversations for the admin console."""
    require_admin_request(request)
    store = get_wa_memory_store()
    conversations = _filter_conversations(
        store,
        store.list_conversations(limit=max(limit, 200)),
        status=status,
        consent_status=consent_status,
        filter=filter,
        search=search,
    )[:max(1, min(int(limit or 50), 200))]
    return {"total": len(conversations), "conversations": conversations}


@app.get("/api/whatsapp/conversations/{phone}")
async def api_whatsapp_conversation(phone: str, request: Request, limit: int = 100):
    """Return one conversation with transcript and agent memory."""
    require_admin_request(request)
    store = get_wa_memory_store()
    conversation = store.get_conversation(phone)
    profile = store.get_profile(phone)
    return {
        "conversation": conversation,
        "profile": profile,
        "last_query": store.get_last_query(phone),
        "messages": store.get_messages(phone, limit=limit),
        "flags": store.list_agent_flags(unresolved_only=True, phone=phone, limit=50),
        "drafts": store.list_proactive_drafts(status="draft", phone=phone, limit=20),
        "qa_feedback": store.list_qa_feedback(status="open", phone=phone, limit=20),
        "agent_state": _build_agent_state(store, phone, profile, conversation),
        "profile_options": _profile_options(),
    }


@app.post("/api/whatsapp/conversations/{phone}")
async def api_whatsapp_update_conversation(
    phone: str,
    payload: ConversationUpdateRequest,
    request: Request,
):
    """Update operator-only labels and notes for a WhatsApp parent."""
    require_admin_request(request)
    conversation = get_wa_memory_store().update_conversation(
        phone,
        display_name=payload.display_name or None,
        tags=payload.tags,
        notes=payload.notes,
        consent_status=payload.consent_status or None,
        proactive_notes=payload.proactive_notes or None,
    )
    return {"success": True, "conversation": conversation}


@app.post("/api/whatsapp/conversations/{phone}/profile")
async def api_whatsapp_update_profile(
    phone: str,
    payload: ProfileUpdateRequest,
    request: Request,
):
    """Update the structured AI profile that future recommendations use."""
    require_admin_request(request)
    handler = get_wa_handler()
    if not handler:
        handler = WhatsAppHandler(memory_store=get_wa_memory_store())
    try:
        payload_data = payload.model_dump() if hasattr(payload, "model_dump") else payload.dict()
        profile = handler.update_profile_from_admin(phone, payload_data)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    store = handler._memory
    conversation = store.get_conversation(phone)
    return {
        "success": True,
        "profile": profile,
        "conversation": conversation,
        "agent_state": _build_agent_state(store, phone, profile, conversation),
        "profile_options": _profile_options(),
    }


@app.post("/api/whatsapp/conversations/{phone}/takeover")
async def api_whatsapp_takeover(phone: str, request: Request):
    """Pause AI auto-replies for a parent while an operator handles them."""
    require_admin_request(request)
    conversation = get_wa_memory_store().set_conversation_status(phone, "human")
    return {"success": True, "conversation": conversation}


@app.post("/api/whatsapp/conversations/{phone}/resume-ai")
async def api_whatsapp_resume_ai(phone: str, request: Request):
    """Resume AI auto-replies for a parent."""
    require_admin_request(request)
    conversation = get_wa_memory_store().set_conversation_status(phone, "ai")
    return {"success": True, "conversation": conversation}


@app.post("/api/whatsapp/conversations/{phone}/messages")
async def api_whatsapp_admin_message(
    phone: str,
    payload: AdminMessageRequest,
    request: Request,
):
    """Send a manual WhatsApp reply from the operator console."""
    require_admin_request(request)
    handler = get_wa_handler()
    if not handler:
        raise HTTPException(status_code=500, detail="WhatsApp is not configured")
    if not handler.send_admin_message(phone, payload.body):
        raise HTTPException(status_code=502, detail="WhatsApp message failed")
    return {"success": True}


@app.get("/api/whatsapp/flags")
async def api_whatsapp_flags(request: Request, unresolved_only: bool = True, limit: int = 100):
    """List AI uncertainty/no-match items for operator review."""
    require_admin_request(request)
    flags = get_wa_memory_store().list_agent_flags(
        unresolved_only=unresolved_only,
        limit=limit,
    )
    return {"total": len(flags), "flags": flags}


@app.get("/api/whatsapp/agent-tasks")
async def api_whatsapp_agent_tasks(
    request: Request,
    limit: int = 100,
    search: str = "",
):
    """Return an operator action queue inferred from memory, flags, and drafts."""
    require_admin_request(request)
    return _build_agent_task_queue(
        get_wa_memory_store(),
        limit=limit,
        search=search,
    )


@app.get("/api/whatsapp/qa-feedback")
async def api_whatsapp_qa_feedback(
    request: Request,
    status: str = "open",
    phone: str = "",
    issue_type: str = "",
    limit: int = 100,
):
    """List operator QA marks and anonymized learning samples."""
    require_admin_request(request)
    feedback = get_wa_memory_store().list_qa_feedback(
        status=status,
        phone=phone,
        issue_type=issue_type,
        limit=limit,
    )
    return {"total": len(feedback), "feedback": feedback}


@app.get("/api/whatsapp/qa-feedback/eval-cases")
async def api_whatsapp_qa_feedback_eval_cases(
    request: Request,
    limit: int = 100,
):
    """Export privacy-scrubbed QA feedback candidates for eval-case review."""
    require_admin_request(request)
    feedback = get_wa_memory_store().list_qa_feedback_for_eval(limit=limit)
    cases: List[Dict[str, Any]] = [
        _build_qa_eval_case(item)
        for item in feedback
    ]
    return {"total": len(cases), "cases": cases}


@app.post("/api/whatsapp/conversations/{phone}/qa-feedback")
async def api_whatsapp_add_qa_feedback(
    phone: str,
    payload: QaFeedbackRequest,
    request: Request,
):
    """Save an admin QA mark without sending raw private data to any LLM."""
    require_admin_request(request)
    store = get_wa_memory_store()
    sample = _build_qa_learning_sample(
        store=store,
        phone=phone,
        message_id=payload.message_id,
        rating=payload.rating,
        issue_type=payload.issue_type,
        summary=payload.summary,
        expected_behavior=payload.expected_behavior,
    )
    feedback = store.add_qa_feedback(
        phone=phone,
        message_id=payload.message_id,
        rating=payload.rating,
        issue_type=payload.issue_type,
        summary=payload.summary,
        expected_behavior=payload.expected_behavior,
        anonymized_sample=sample,
    )
    conversation = store.get_conversation(phone)
    return {
        "success": True,
        "feedback": feedback,
        "agent_state": _build_agent_state(store, phone, store.get_profile(phone), conversation),
    }


@app.post("/api/whatsapp/qa-feedback/{feedback_id}/status")
async def api_whatsapp_update_qa_feedback_status(
    feedback_id: int,
    payload: QaFeedbackStatusRequest,
    request: Request,
):
    """Close or mark a QA sample as converted into a test/prompt rule."""
    require_admin_request(request)
    feedback = get_wa_memory_store().mark_qa_feedback(
        feedback_id,
        status=payload.status,
    )
    if not feedback:
        raise HTTPException(status_code=404, detail="QA feedback not found")
    return {"success": True, "feedback": feedback}


@app.post("/api/whatsapp/flags/{flag_id}/resolve")
async def api_whatsapp_resolve_flag(flag_id: int, request: Request):
    """Resolve an AI uncertainty/no-match queue item."""
    require_admin_request(request)
    return {
        "success": get_wa_memory_store().resolve_agent_flag(flag_id),
    }


def _send_operator_proactive_message(
    phone: str,
    message: str,
    payload: ProactiveSendRequest,
) -> str:
    """Send a proactive operator-approved message and return the message type."""
    store = get_wa_memory_store()
    conversation = store.get_conversation(phone)
    if conversation.get("consent_status") != "allowed":
        raise HTTPException(
            status_code=409,
            detail="Parent has not consented to proactive messages",
        )
    handler = get_wa_handler()
    if not handler:
        raise HTTPException(status_code=500, detail="WhatsApp is not configured")
    if not message:
        raise HTTPException(status_code=400, detail="Message body is required")

    needs_template = (
        payload.use_template
        or not handler.is_within_customer_service_window(phone)
    )
    if needs_template:
        template_name = (
            payload.template_name.strip()
            or os.environ.get("WHATSAPP_PROACTIVE_TEMPLATE_NAME", "").strip()
        )
        template_language = (
            payload.template_language.strip()
            or os.environ.get("WHATSAPP_PROACTIVE_TEMPLATE_LANGUAGE", "zh_HK").strip()
            or "zh_HK"
        )
        if not template_name:
            raise HTTPException(
                status_code=409,
                detail="WhatsApp template is required outside the 24-hour customer service window",
            )
        parameters = payload.template_params or [message]
        if not handler.send_template_message(
            phone,
            template_name,
            template_language,
            parameters,
            transcript_body=message,
        ):
            raise HTTPException(status_code=502, detail="WhatsApp template message failed")
        return "template"

    if not handler.send_admin_message(phone, message):
        raise HTTPException(status_code=502, detail="WhatsApp message failed")
    return "text"


@app.get("/api/whatsapp/proactive-matches")
async def api_whatsapp_proactive_matches(
    request: Request,
    parent_limit: int = 100,
    courses_per_parent: int = 3,
    allowed_only: bool = False,
):
    """Draft proactive course matches from stored family memories."""
    require_admin_request(request)
    handler = get_wa_handler()
    if not handler:
        raise HTTPException(status_code=500, detail="WhatsApp is not configured")
    matches = handler.get_proactive_matches(
        parent_limit=parent_limit,
        courses_per_parent=courses_per_parent,
        allowed_only=allowed_only,
    )
    return {"total": len(matches), "matches": matches}


@app.post("/api/whatsapp/proactive-drafts/generate")
async def api_whatsapp_generate_proactive_drafts(
    payload: ProactiveDraftGenerateRequest,
    request: Request,
):
    """Persist proactive match drafts into the operator review queue."""
    require_admin_request(request)
    handler = get_wa_handler()
    if not handler:
        raise HTTPException(status_code=500, detail="WhatsApp is not configured")
    matches = handler.get_proactive_matches(
        parent_limit=payload.parent_limit,
        courses_per_parent=payload.courses_per_parent,
        allowed_only=payload.allowed_only,
    )
    store = get_wa_memory_store()
    drafts = [
        store.save_proactive_draft(
            phone=match["phone"],
            draft_text=match.get("draft_text", ""),
            matches=match.get("matches", []),
            profile=match.get("profile", {}),
            meta={"source": "proactive_match"},
        )
        for match in matches
        if match.get("draft_text")
    ]
    drafts = [draft for draft in drafts if draft]
    return {"total": len(drafts), "drafts": drafts}


@app.get("/api/whatsapp/proactive-drafts")
async def api_whatsapp_proactive_drafts(
    request: Request,
    status: str = "draft",
    phone: str = "",
    search: str = "",
    consent_status: str = "",
    limit: int = 100,
):
    """List persisted proactive drafts and send history."""
    require_admin_request(request)
    drafts = get_wa_memory_store().list_proactive_drafts(
        status=status,
        phone=phone,
        search=search,
        consent_status=consent_status,
        limit=limit,
    )
    return {"total": len(drafts), "drafts": drafts}


@app.post("/api/whatsapp/privacy/prune")
async def api_whatsapp_privacy_prune(
    payload: PrivacyPruneRequest,
    request: Request,
):
    """Preview or prune old private operational history without deleting profiles."""
    require_admin_request(request)
    result = get_wa_memory_store().prune_private_history(
        older_than_days=payload.older_than_days,
        dry_run=payload.dry_run,
    )
    return {"success": True, **result}


@app.post("/api/whatsapp/proactive-drafts/{draft_id}")
async def api_whatsapp_update_proactive_draft(
    draft_id: int,
    payload: ProactiveDraftUpdateRequest,
    request: Request,
):
    """Update an operator-edited proactive draft before sending."""
    require_admin_request(request)
    draft = get_wa_memory_store().update_proactive_draft_body(
        draft_id,
        payload.body,
    )
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found or not editable")
    return {"success": True, "draft": draft}


@app.post("/api/whatsapp/proactive-drafts/{draft_id}/skip")
async def api_whatsapp_skip_proactive_draft(draft_id: int, request: Request):
    """Mark a proactive draft as intentionally skipped."""
    require_admin_request(request)
    draft = get_wa_memory_store().mark_proactive_draft(
        draft_id,
        "skipped",
        only_status="draft",
    )
    if not draft:
        raise HTTPException(status_code=409, detail="Draft is not pending")
    return {"success": True, "draft": draft}


@app.post("/api/whatsapp/proactive-drafts/{draft_id}/send")
async def api_whatsapp_send_proactive_draft(
    draft_id: int,
    payload: ProactiveSendRequest,
    request: Request,
):
    """Send a persisted proactive draft and record the final status."""
    require_admin_request(request)
    store = get_wa_memory_store()
    draft = store.get_proactive_draft(draft_id)
    if not draft:
        raise HTTPException(status_code=404, detail="Draft not found")
    if draft.get("status") != "draft":
        raise HTTPException(status_code=409, detail="Draft is not pending")
    message = payload.body.strip() or str(draft.get("draft_text", "")).strip()
    draft = store.claim_proactive_draft_for_send(draft_id, message)
    if not draft:
        raise HTTPException(status_code=409, detail="Draft is already being processed")
    try:
        message_type = _send_operator_proactive_message(
            draft["phone"],
            message,
            payload,
        )
    except HTTPException as exc:
        if exc.status_code >= 500:
            store.mark_proactive_draft(
                draft_id,
                "failed",
                error_text=str(exc.detail),
                only_status="sending",
            )
        else:
            store.mark_proactive_draft(
                draft_id,
                "draft",
                error_text=str(exc.detail),
                only_status="sending",
            )
        raise
    updated = store.mark_proactive_draft(
        draft_id,
        "sent",
        sent_message_type=message_type,
        sent_text=message,
        only_status="sending",
    )
    if not updated:
        raise HTTPException(status_code=409, detail="Draft status changed during send")
    return {"success": True, "message_type": message_type, "draft": updated}


@app.post("/api/whatsapp/proactive-matches/{phone}/send")
async def api_whatsapp_send_proactive_match(
    phone: str,
    payload: ProactiveSendRequest,
    request: Request,
):
    """Send an operator-approved proactive draft to a consented parent."""
    require_admin_request(request)
    message = payload.body.strip()
    message_type = _send_operator_proactive_message(phone, message, payload)
    return {"success": True, "message_type": message_type}


# ============== 企業微信客服回調 ==============

@app.get("/api/wecom-cs/callback")
async def wecom_cs_callback_verify(
    msg_signature: str = Query(..., alias="msg_signature"),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    """
    企業微信客服回調 URL 驗證（GET）

    企業微信後台配置回調 URL 時，會發送 GET 請求驗證
    需要解密 echostr 並返回明文
    """
    crypto = get_cs_crypto()
    if not crypto:
        logger.warning("WeCom crypto 未初始化，無法驗證回調")
        raise HTTPException(status_code=500, detail="Crypto not configured")

    try:
        # 驗證簽名
        if not crypto.verify_signature(msg_signature, timestamp, nonce, echostr):
            logger.warning("回調簽名驗證失敗")
            raise HTTPException(status_code=403, detail="Invalid signature")

        # 解密 echostr
        plaintext, corp_id = crypto.decrypt(echostr)
        logger.info(f"回調驗證成功，corp_id={corp_id}")

        # 返回明文
        return PlainTextResponse(content=plaintext)

    except Exception as e:
        logger.exception(f"回調驗證失敗: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/wecom-cs/callback")
async def wecom_cs_callback(
    request: Request,
    msg_signature: str = Query(..., alias="msg_signature"),
    timestamp: str = Query(...),
    nonce: str = Query(...),
):
    """
    企業微信客服事件回調（POST）

    接收加密的事件推送，解密後處理
    """
    crypto = get_cs_crypto()
    if not crypto:
        logger.warning("WeCom crypto 未初始化，無法處理事件")
        raise HTTPException(status_code=500, detail="Crypto not configured")

    try:
        # 讀取 POST body
        post_data = await request.body()
        post_data_str = post_data.decode("utf-8")
        logger.info(f"收到加密事件: {post_data_str[:200]}...")

        # 解密
        event_data = crypto.decrypt_event_msg(msg_signature, timestamp, nonce, post_data_str)
        logger.info(f"解密後事件: {event_data}")

        # 處理事件
        handler = get_cs_handler()
        result = handler.handle_event(event_data)

        # 如果有需要回覆的內容（被動回覆）
        if result:
            # 加密回覆
            encrypt_xml = crypto.encrypt_msg(result, timestamp, nonce)
            return PlainTextResponse(content=encrypt_xml, media_type="application/xml")

        return PlainTextResponse(content="success")

    except Exception as e:
        logger.exception(f"事件處理失敗: {e}")
        # 即使處理失敗，也返回 success，避免企業微信重試
        return PlainTextResponse(content="success")


# ============== WhatsApp Webhook ==============

@app.get("/api/whatsapp/webhook")
async def whatsapp_webhook_verify(
    hub_mode: str = Query(..., alias="hub.mode"),
    hub_verify_token: str = Query(..., alias="hub.verify_token"),
    hub_challenge: str = Query(..., alias="hub.challenge"),
):
    """
    WhatsApp webhook 驗證（GET）

    Meta 配置 webhook callback URL 時發送 GET 請求驗證
    """
    from whatsapp_handler import WhatsAppHandler
    result = WhatsAppHandler.verify_challenge(hub_mode, hub_verify_token, hub_challenge)
    if result is not None:
        logger.info("WhatsApp webhook 驗證成功")
        return PlainTextResponse(content=result)
    else:
        logger.warning("WhatsApp webhook 驗證失敗")
        raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/api/whatsapp/webhook")
async def whatsapp_webhook(request: Request, background_tasks: BackgroundTasks):
    """
    WhatsApp 消息接收（POST）

    接收家長發來的消息，處理後回覆課程資訊
    """
    try:
        body = await request.body()
        app_secret = os.environ.get("WHATSAPP_APP_SECRET", "")
        if app_secret:
            if not is_valid_meta_signature(
                body,
                request.headers.get("x-hub-signature-256", ""),
                app_secret,
            ):
                logger.warning("WhatsApp webhook 簽名驗證失敗")
                raise HTTPException(status_code=403, detail="Invalid signature")
        elif _env_truthy("WHATSAPP_ALLOW_UNSIGNED_WEBHOOK"):
            logger.warning("WHATSAPP_APP_SECRET 未配置，暫時允許未簽名 WhatsApp webhook")
        else:
            logger.error("WHATSAPP_APP_SECRET 未配置，拒絕 WhatsApp webhook")
            raise HTTPException(status_code=500, detail="WhatsApp app secret not configured")

        handler = get_wa_handler()
        if not handler:
            logger.warning("WhatsApp 未配置，忽略 webhook")
            return PlainTextResponse(content="ok")

        data = json.loads(body.decode("utf-8") or "{}")
        if handler.claim_webhook_messages(data):
            background_tasks.add_task(handler.handle_webhook, data, True)
        return PlainTextResponse(content="ok")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"WhatsApp webhook 處理失敗: {e}")
        return PlainTextResponse(content="ok")


# ============== 啟動 ==============

def main():
    """主入口"""
    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    host = os.environ.get("HOST", "0.0.0.0")

    logger.info(f"啟動 API Server: {host}:{port}")

    uvicorn.run(
        "api_server:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


if __name__ == "__main__":
    main()
