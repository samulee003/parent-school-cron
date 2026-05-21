"""FastAPI HTTP 服務器 — Zeabur 適配

接收企業微信客服回調，提供管理接口
"""

import logging
import os
import sys
import json
import hashlib
import hmac
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

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


@app.get("/", response_class=HTMLResponse)
async def root():
    """Public landing page for business verification and parents."""
    return """<!doctype html>
<html lang="zh-Hant">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>家長學堂 WhatsApp 課程小助手</title>
  <meta name="description" content="透過 WhatsApp 查詢澳門家長學堂課程，按孩子年齡和課程類型篩選並取得官方報名連結。">
  <style>
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: #1f2933; background: #f7f7f2; }
    main { max-width: 760px; margin: 0 auto; padding: 56px 24px; }
    h1 { font-size: 34px; line-height: 1.2; margin: 0 0 16px; }
    p { font-size: 18px; line-height: 1.7; margin: 0 0 16px; }
    .panel { background: #fff; border: 1px solid #d9ded7; border-radius: 8px; padding: 24px; margin-top: 28px; }
    .button { display: inline-block; background: #0f8f5f; color: #fff; text-decoration: none; padding: 12px 18px; border-radius: 6px; font-weight: 700; }
    .qr { width: 220px; max-width: 100%; border-radius: 8px; border: 1px solid #d9ded7; margin-top: 12px; }
    .small { font-size: 14px; color: #52616b; }
  </style>
</head>
<body>
  <main>
    <h1>家長學堂 WhatsApp 課程小助手</h1>
    <p>這是一個協助家長查詢澳門家長學堂課程的 WhatsApp 小助手。家長可以按孩子年齡、親子課、家長講座、青少年課程等條件查詢，並取得官方報名連結。</p>
    <p>課程資料來自澳門教育及青年發展局家長學堂公開課程頁面，實際名額、時間和報名狀態以官方網站為準。</p>
    <div class="panel">
      <p>WhatsApp 使用方式：</p>
      <p>輸入「課程」、「13歲有什麼課程」、「小朋友4歲，想親子課」、「更多」即可查詢。</p>
      <a class="button" href="https://wa.me/8614714949607?text=%E8%AA%B2%E7%A8%8B">開啟 WhatsApp 查詢</a>
      <p><img class="qr" src="/whatsapp-qr.png" alt="家長學堂 WhatsApp QR code"></p>
    </div>
    <div class="panel">
      <p>私隱說明</p>
      <p class="small">本服務只會使用家長在 WhatsApp 對話中提供的查詢內容，以便記住年齡偏好和回覆課程建議。不會出售個人資料，也不提供與家長學堂課程無關的通用 AI 問答。</p>
    </div>
  </main>
</body>
</html>"""


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
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; background: #f6f7f4; color: #1f2933; }
    main { min-height: 100vh; display: grid; place-items: center; padding: 24px; }
    form { width: min(360px, 100%); background: #fff; border: 1px solid #d9ded7; border-radius: 8px; padding: 22px; display: grid; gap: 12px; }
    h1 { font-size: 18px; margin: 0 0 6px; }
    input, button { font: inherit; border-radius: 6px; padding: 10px 12px; }
    input { border: 1px solid #d9ded7; }
    button { border: 1px solid #0f8f5f; background: #0f8f5f; color: #fff; cursor: pointer; }
    .msg { min-height: 20px; color: #9b2c2c; font-size: 13px; }
  </style>
</head>
<body>
  <main>
    <form id="login">
      <h1>WhatsApp 家長學堂接手台</h1>
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
    :root { color-scheme: light; --line: #d9ded7; --ink: #1f2933; --muted: #5f6f79; --brand: #0f8f5f; --bg: #f6f7f4; }
    * { box-sizing: border-box; }
    body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; color: var(--ink); background: var(--bg); }
    header { height: 52px; display: flex; align-items: center; justify-content: space-between; padding: 0 18px; border-bottom: 1px solid var(--line); background: #fff; }
    h1 { font-size: 18px; margin: 0; font-weight: 700; }
    main { display: grid; grid-template-columns: 300px minmax(360px, 1fr) 340px; min-height: calc(100vh - 52px); }
    aside, section { border-right: 1px solid var(--line); background: #fff; min-width: 0; }
    aside:last-child { border-right: 0; }
    .toolbar { padding: 12px; border-bottom: 1px solid var(--line); display: flex; gap: 8px; align-items: center; }
    input, textarea, button { font: inherit; }
    input, textarea { width: 100%; border: 1px solid var(--line); border-radius: 6px; padding: 9px 10px; background: #fff; }
    textarea { min-height: 88px; resize: vertical; }
    button { border: 1px solid var(--line); background: #fff; border-radius: 6px; padding: 8px 10px; cursor: pointer; white-space: nowrap; }
    button.primary { background: var(--brand); color: #fff; border-color: var(--brand); }
    button.warn { border-color: #b7791f; color: #8a5200; }
    .list { overflow: auto; max-height: calc(100vh - 105px); }
    .row { padding: 12px; border-bottom: 1px solid var(--line); cursor: pointer; }
    .row:hover, .row.active { background: #eef7f1; }
    .phone { font-weight: 700; font-size: 14px; }
    .latest { color: var(--muted); font-size: 13px; margin-top: 4px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
    .pill { display: inline-block; font-size: 12px; border: 1px solid var(--line); border-radius: 999px; padding: 2px 8px; color: var(--muted); margin-top: 6px; }
    .pill.human { color: #8a5200; border-color: #d69e2e; background: #fffaf0; }
    .messages { padding: 16px; overflow: auto; height: calc(100vh - 210px); display: flex; flex-direction: column; gap: 10px; }
    .bubble { max-width: 78%; padding: 10px 12px; border-radius: 8px; line-height: 1.45; white-space: pre-wrap; word-break: break-word; border: 1px solid var(--line); }
    .inbound { align-self: flex-start; background: #fff; }
    .outbound { align-self: flex-end; background: #e8f7ee; border-color: #bddfc8; }
    .meta { color: var(--muted); font-size: 12px; margin-bottom: 4px; }
    .composer { padding: 12px; border-top: 1px solid var(--line); background: #fff; display: grid; grid-template-columns: 1fr auto; gap: 8px; }
    .sidebody { padding: 14px; display: grid; gap: 12px; }
    .kv { border-bottom: 1px solid var(--line); padding-bottom: 10px; }
    .label { color: var(--muted); font-size: 12px; margin-bottom: 4px; }
    .mini { color: var(--muted); font-size: 12px; line-height: 1.4; }
    .flag, .match { border: 1px solid var(--line); border-radius: 6px; padding: 8px; margin-top: 8px; background: #fff; }
    .match strong { display: block; margin-bottom: 4px; }
    .match textarea { min-height: 120px; margin-top: 8px; }
    pre { margin: 0; white-space: pre-wrap; word-break: break-word; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 12px; background: #f4f5f2; padding: 10px; border-radius: 6px; }
    @media (max-width: 900px) { main { grid-template-columns: 1fr; } aside, section { min-height: 260px; border-right: 0; border-bottom: 1px solid var(--line); } .messages { height: 360px; } }
  </style>
</head>
<body>
  <header>
    <h1>WhatsApp 家長學堂接手台</h1>
    <button onclick="loadConversations()">刷新</button>
  </header>
  <main>
    <aside>
      <div class="toolbar"><input id="search" placeholder="搜尋電話或訊息" oninput="renderList()"></div>
      <div class="toolbar">
        <button onclick="loadFlags()">不確定隊列</button>
        <button onclick="loadMatches()">主動匹配</button>
        <button onclick="loadDrafts('draft')">待發草稿</button>
        <button onclick="loadDrafts('all')">推送紀錄</button>
      </div>
      <div id="list" class="list"></div>
    </aside>
    <section>
      <div class="toolbar"><strong id="chatTitle">未選擇對話</strong></div>
      <div id="messages" class="messages"></div>
      <div class="composer">
        <textarea id="reply" placeholder="人工回覆"></textarea>
        <button class="primary" onclick="sendReply()">傳送</button>
      </div>
    </section>
    <aside>
      <div class="sidebody">
        <div class="kv"><div class="label">狀態</div><div id="status">-</div></div>
        <div style="display:flex; gap:8px; flex-wrap:wrap">
          <button class="warn" onclick="takeover()">人工接手</button>
          <button onclick="resumeAi()">恢復 AI</button>
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
        <div class="kv"><div class="label">記憶</div><pre id="profile">{}</pre></div>
        <div class="kv"><div class="label">上次查詢</div><pre id="lastQuery">{}</pre></div>
        <div class="kv"><div class="label">不確定隊列</div><div id="flags" class="mini">尚未載入</div></div>
        <div class="kv"><div class="label">主動匹配草稿</div><div id="matches" class="mini">尚未產生</div></div>
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
    async function loadConversations() {
      const data = await api("/api/whatsapp/conversations");
      conversations = data.conversations || [];
      renderList();
      if (currentPhone) await openChat(currentPhone);
    }
    function renderList() {
      const q = document.getElementById("search").value.trim().toLowerCase();
      document.getElementById("list").innerHTML = conversations
        .filter(c => !q || c.phone.includes(q) || String(c.latest_message || "").toLowerCase().includes(q))
        .map(c => `<div class="row ${c.phone === currentPhone ? "active" : ""}" onclick="openChat('${esc(c.phone)}')">
          <div class="phone">${esc(c.phone)}</div>
          <div class="latest">${esc(c.latest_message || "")}</div>
          <span class="pill ${c.status === "human" ? "human" : ""}">${c.status === "human" ? "人工接手" : "AI 自動"}</span>
          <span class="pill">${c.consent_status === "allowed" ? "可推送" : c.consent_status === "paused" ? "暫停推送" : "未同意"}</span>
        </div>`).join("");
    }
    async function openChat(phone) {
      currentPhone = phone;
      const data = await api("/api/whatsapp/conversations/" + encodeURIComponent(phone));
      document.getElementById("chatTitle").textContent = phone;
      document.getElementById("status").textContent = data.conversation.status === "human" ? "人工接手中" : "AI 自動回覆";
      document.getElementById("tags").value = (data.conversation.tags || []).join(", ");
      document.getElementById("notes").value = data.conversation.notes || "";
      document.getElementById("consentStatus").value = data.conversation.consent_status || "unknown";
      document.getElementById("proactiveNotes").value = data.conversation.proactive_notes || "";
      document.getElementById("profile").textContent = JSON.stringify(data.profile || {}, null, 2);
      document.getElementById("lastQuery").textContent = JSON.stringify(data.last_query || {}, null, 2);
      document.getElementById("messages").innerHTML = (data.messages || []).map(m => `
        <div class="bubble ${m.direction}">
          <div class="meta">${esc(m.source)} · ${esc(m.created_at)}</div>${esc(m.body)}
        </div>`).join("");
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
    async function loadDrafts(status = "draft") {
      const data = await api("/api/whatsapp/proactive-drafts?status=" + encodeURIComponent(status));
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
async def api_whatsapp_conversations(request: Request, limit: int = 50):
    """List WhatsApp parent conversations for the admin console."""
    require_admin_request(request)
    store = get_wa_memory_store()
    conversations = store.list_conversations(limit=limit)
    return {"total": len(conversations), "conversations": conversations}


@app.get("/api/whatsapp/conversations/{phone}")
async def api_whatsapp_conversation(phone: str, request: Request, limit: int = 100):
    """Return one conversation with transcript and agent memory."""
    require_admin_request(request)
    store = get_wa_memory_store()
    return {
        "conversation": store.get_conversation(phone),
        "profile": store.get_profile(phone),
        "last_query": store.get_last_query(phone),
        "messages": store.get_messages(phone, limit=limit),
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
    limit: int = 100,
):
    """List persisted proactive drafts and send history."""
    require_admin_request(request)
    drafts = get_wa_memory_store().list_proactive_drafts(
        status=status,
        phone=phone,
        limit=limit,
    )
    return {"total": len(drafts), "drafts": drafts}


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
