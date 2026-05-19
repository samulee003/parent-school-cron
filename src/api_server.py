"""FastAPI HTTP 服務器 — Zeabur 適配

接收企業微信群機器人回調，提供管理接口
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

from fastapi import FastAPI, Request, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse
from pydantic import BaseModel
from bot_webhook import ZeaburBot

# 全局 Bot 實例
bot: Optional[ZeaburBot] = None


def get_bot() -> ZeaburBot:
    global bot
    if bot is None:
        bot = ZeaburBot()
    return bot


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
    description="Zeabur 部署的企業微信群機器人課程推送服務",
    version="2.0.0",
)


@app.on_event("startup")
async def startup():
    """啟動時初始化"""
    logger.info("API Server 啟動...")
    get_bot()


@app.get("/")
async def root():
    """健康檢查"""
    return {
        "status": "ok",
        "service": "家長學堂課程推送 Bot",
        "version": "2.0.0",
        "time": datetime.now().isoformat(),
    }


@app.get("/health")
async def health():
    """健康檢查端點"""
    b = get_bot()
    stats = b.store.get_stats()
    return {
        "status": "healthy",
        "webhook_configured": bool(b.webhook_url),
        "users": stats,
    }


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
