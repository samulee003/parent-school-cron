# 家長學堂 WhatsApp Bot

澳門家長學堂課程查詢 Bot。第一版主線是 WhatsApp：家長掃 QR code 開對話，發送「課程」「0-2歲」「報名」等關鍵詞，即時收到最新報名中課程。

企業微信相關程式保留為 legacy fallback，但不是目前主線。

## 家長流程

```text
掃 WhatsApp QR / wa.me 連結
  -> 發送「課程」
  -> Bot 回覆最新家長學堂課程
  -> 發送「0-2歲」「3-6歲」「7-12歲」「13-18歲」可按年齡查詢
```

## 支援指令

| 家長輸入 | 回覆 |
| --- | --- |
| `課程` / `最新` / `course` | 最新 5 個報名中課程 |
| `0-2歲` / `3-6歲` / `7-12歲` / `13-18歲` | 指定年齡層課程 |
| `報名` / `報名連結` | 報名方式 |
| `你好` / `help` / `幫助` | 使用說明 |

## 本機啟動

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
python src/api_server.py
```

健康檢查：

```bash
curl http://127.0.0.1:8000/health
```

## WhatsApp Webhook

Meta 後台設定：

| 欄位 | 值 |
| --- | --- |
| Callback URL | `https://你的域名/api/whatsapp/webhook` |
| Verify Token | `WHATSAPP_VERIFY_TOKEN` 的值 |
| Webhook fields | `messages` |

必填環境變數：

| Key | 說明 |
| --- | --- |
| `WHATSAPP_PHONE_NUMBER_ID` | WhatsApp Cloud API phone number id |
| `WHATSAPP_ACCESS_TOKEN` | Permanent token |
| `WHATSAPP_VERIFY_TOKEN` | Meta webhook 驗證 token |
| `WHATSAPP_APP_SECRET` | 必填，用於驗證 Meta 簽名；缺少時 webhook 會拒絕處理 |
| `WHATSAPP_PROACTIVE_TEMPLATE_NAME` | 可選，窗口外主動推送使用的已核准 WhatsApp template 名稱 |
| `WHATSAPP_PROACTIVE_TEMPLATE_LANGUAGE` | 可選，template 語言，預設 `zh_HK` |
| `CRON_SECRET` | 保護 `/api/cron` |
| `ADMIN_SECRET` | 管理台登入密鑰 |
| `ADMIN_COOKIE_SECURE` | 可選，管理台 cookie 是否只允許 HTTPS，正式環境保持 `true` |

## API

| Method | Path | 用途 |
| --- | --- | --- |
| `GET` | `/` | 基本健康檢查 |
| `GET` | `/health` | 詳細狀態 |
| `GET` | `/api/whatsapp/webhook` | Meta webhook 驗證 |
| `POST` | `/api/whatsapp/webhook` | 接收 WhatsApp 消息 |
| `GET` | `/admin` | WhatsApp 管理台，使用登入 cookie，不再把 secret 放 URL |
| `POST` | `/admin/login` | 建立 HttpOnly 管理台登入 cookie |
| `POST` | `/api/cron?secret=...` | Cron 觸發課程抓取 |
| `POST` | `/api/push?secret=...` | 管理員手動推送 |
| `GET` | `/api/users?secret=...` | 管理員查看用戶 |

## 目前限制

- WhatsApp 測試號碼只能給 Meta 白名單測試用戶互動。
- 正式公開使用需要正式 WhatsApp 號碼、Permanent token、Meta webhook 設定完成。
- 每週主動推送若超出 24 小時對話窗口，需要 WhatsApp message template；未設定
  `WHATSAPP_PROACTIVE_TEMPLATE_NAME` 時，系統會阻止窗口外主動發送。

## 驗證

```bash
python -m unittest
python -B -m compileall src
```
