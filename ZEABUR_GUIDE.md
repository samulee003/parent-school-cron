# Zeabur 部署指南

目前主線是 WhatsApp Cloud API。企業微信檔案保留，但部署時可全部留空。

## 架構

```text
WhatsApp 家長訊息
  -> Meta Webhook
  -> Zeabur FastAPI /api/whatsapp/webhook
  -> DSEDJ 家長學堂爬蟲
  -> WhatsApp Cloud API 回覆課程
```

## 1. 部署

1. 在 Zeabur 建立 Project。
2. 從 GitHub repo 部署此服務。
3. Zeabur 會使用 Dockerfile 啟動 `python src/api_server.py`。
4. 服務 port 使用環境變數 `PORT`，預設 `8000`。

## 2. 環境變數

必填：

| Key | 說明 |
| --- | --- |
| `WHATSAPP_PHONE_NUMBER_ID` | WhatsApp Phone Number ID |
| `WHATSAPP_ACCESS_TOKEN` | Permanent Access Token |
| `WHATSAPP_VERIFY_TOKEN` | Meta Webhook Verify Token |
| `WHATSAPP_APP_SECRET` | Meta App Secret，驗證 webhook 簽名 |
| `CRON_SECRET` | Cron endpoint secret |
| `ADMIN_SECRET` | Admin endpoint secret |
| `WXAGENT_DATA_DIR` | 建議 `/app/data` |

可選：

| Key | 說明 |
| --- | --- |
| `WXAGENT_PUSH_DAY` | 預設 `mon` |
| `WXAGENT_PUSH_HOUR` | 預設 `9` |
| `WXAGENT_PUSH_MINUTE` | 預設 `0` |
| `WECOM_*` | legacy 企業微信渠道，不用可留空 |

## 3. Meta Webhook

Meta Developers 後台填：

```text
Callback URL: https://你的 Zeabur 域名/api/whatsapp/webhook
Verify Token: WHATSAPP_VERIFY_TOKEN 的值
Webhook fields: messages
```

## 4. Cron

第一版主要是家長主動查詢。若仍想定時刷新/測試，可加 Zeabur Cron：

```text
Schedule: 0 1 * * 1
Command: curl -s -X POST "https://你的域名/api/cron?secret=$CRON_SECRET"
```

`0 1 * * 1` 是 UTC 週一 01:00，等於澳門時間週一 09:00。

## 5. 驗證

```bash
curl https://你的域名/health
curl -X POST "https://你的域名/api/cron?secret=你的CRON_SECRET"
```

端到端測試：

1. 用 Meta 測試白名單手機傳「課程」。
2. 確認收到最新課程。
3. 再測「0-2歲」「報名」「幫助」。

## 6. 上線限制

- 測試號碼不能公開給所有家長。
- Temporary token 會過期。
- 超出 24 小時對話窗口的主動推送，需要 WhatsApp message template。
