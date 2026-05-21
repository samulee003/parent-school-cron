# 家長學堂 WhatsApp Bot — 系統規格

最後更新：2026-05-20
版本：v3.0.0 WhatsApp-first

## 1. 目標

讓家長透過 WhatsApp 查詢澳門家長學堂最新報名中課程。第一版先做穩定查詢 Bot，不處理超出 24 小時窗口的主動廣播。

## 2. 主流程

```text
家長 WhatsApp
  -> Meta Cloud API webhook
  -> FastAPI /api/whatsapp/webhook
  -> CourseScraper 抓 DSEDJ 家長學堂課程
  -> WhatsApp Cloud API 回覆文字列表
```

## 3. 服務

| 項目 | 值 |
| --- | --- |
| Runtime | Python + FastAPI + Uvicorn |
| Default port | `8000` |
| Main entry | `src/api_server.py` |
| Data dir | `WXAGENT_DATA_DIR`，部署建議 `/app/data` |
| Repo | `samulee003/parent-school-cron` |

## 4. 必填環境變數

| Key | 用途 |
| --- | --- |
| `WHATSAPP_PHONE_NUMBER_ID` | WhatsApp Cloud API 發送消息 |
| `WHATSAPP_ACCESS_TOKEN` | WhatsApp Cloud API token，正式環境用 Permanent token |
| `WHATSAPP_VERIFY_TOKEN` | Meta webhook GET 驗證 |
| `WHATSAPP_APP_SECRET` | Meta POST webhook 簽名驗證，必填 |
| `WHATSAPP_ALLOW_UNSIGNED_WEBHOOK` | 臨時兼容未簽名 webhook；正式環境應為 `false` |
| `CRON_SECRET` | `/api/cron` |
| `ADMIN_SECRET` | `/admin` 管理台登入 |

## 5. API

| Method | Path | 說明 | 保護 |
| --- | --- | --- | --- |
| `GET` | `/` | 基本狀態 | 無 |
| `GET` | `/health` | 詳細狀態 | 無 |
| `GET` | `/api/whatsapp/webhook` | Meta webhook 驗證 | `WHATSAPP_VERIFY_TOKEN` |
| `POST` | `/api/whatsapp/webhook` | 接收 WhatsApp 消息 | 必須通過 `WHATSAPP_APP_SECRET` 簽名 |
| `GET` | `/admin` | WhatsApp 管理台 | HttpOnly cookie 或 Authorization Bearer |
| `POST` | `/admin/login` | 管理台登入 | `ADMIN_SECRET` |
| `POST` | `/api/cron?secret=...` | Cron 抓課程 | `CRON_SECRET` |
| `POST` | `/api/push?secret=...` | 管理員手動推送 | `ADMIN_SECRET` 或 `CRON_SECRET` |
| `GET` | `/api/users?secret=...` | 管理員查看用戶 | `ADMIN_SECRET` 或 `CRON_SECRET` |

## 6. WhatsApp 指令

| 輸入 | 行為 |
| --- | --- |
| `課程`、`最新`、`course` | 回覆最新 5 個報名中課程 |
| `0-2歲`、`3-6歲`、`7-12歲`、`13-18歲` | 回覆指定年齡層課程 |
| `報名`、`報名連結` | 回覆報名指引 |
| `你好`、`help`、`幫助` | 回覆說明 |

## 7. Legacy

以下模組保留但非主線：

- `wecom_cs_handler.py`
- `wecom_poller.py`
- `wecom_cs_api.py`
- `bot_webhook.py` 中的企業微信群 webhook 推送

之後若確定不再使用企業微信，可以另開任務刪除 legacy 模組。

## 8. 已知限制

- WhatsApp 測試號碼只支援白名單用戶。
- 主動每週推送需要 approved message template。
- DSEDJ 網站結構若改版，`scraper.py` 需要同步修正。
- 舊 git history 曾包含敏感值時，需要在平台輪替密鑰。
