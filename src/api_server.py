"""FastAPI HTTP 服務器 — Zeabur 適配

接收企業微信客服回調，提供管理接口
"""

import logging
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

logger = logging.getLogger("api_server")

from fastapi import FastAPI, Request, BackgroundTasks, HTTPException, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from bot_webhook import ZeaburBot
from wecom_cs_handler import CSMessageHandler
from wecom_crypto import WeComCrypto
from wecom_poller import WeComPoller

# 全局實例
bot: Optional[ZeaburBot] = None
cs_handler: Optional[CSMessageHandler] = None
cs_crypto: Optional[WeComCrypto] = None
poller: Optional[WeComPoller] = None


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


# ============== FastAPI 應用 ==============

app = FastAPI(
    title="家長學堂課程推送 Bot",
    description="Zeabur 部署的企業微信客服課程推送服務",
    version="2.2.0",
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


@app.get("/")
async def root():
    """健康檢查"""
    return {
        "status": "ok",
        "service": "家長學堂課程推送 Bot",
        "version": "2.2.0",
        "time": datetime.now().isoformat(),
    }


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
async def api_push(background_tasks: BackgroundTasks):
    """手動觸發推送"""
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
    expected = os.environ.get("CRON_SECRET", "")
    if expected and secret != expected:
        raise HTTPException(status_code=401, detail="Unauthorized")

    b = get_bot()
    result = b.run_push()
    return result


@app.get("/api/users")
async def api_users():
    """獲取用戶列表（管理員接口）"""
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
