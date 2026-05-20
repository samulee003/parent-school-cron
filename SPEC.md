# 家長學堂課程推送 Bot — 系統規格書

> 最後更新：2026-05-20  
> 版本：v2.3.0

---

## 1. 專案概述

澳門家長學堂的自動化課程推送與客服系統，部署於 Zeabur。支援兩個渠道：企業微信客服（WeCom Customer Service）與 WhatsApp Cloud API。

家長可透過微信客服或 WhatsApp 查詢最新課程、報名資訊。

---

## 2. 架構

```
家長 (WeCom/WhatsApp)
    ↓
Zeabur (parent-school-bot)
    ├── api_server.py  (FastAPI, port 8080)
    │   ├── /api/wecom-cs/callback   (企業微信客服)
    │   ├── /api/whatsapp/webhook    (WhatsApp)
    │   ├── /api/cron                (定時推送)
    │   ├── /api/status              (狀態查詢)
    │   └── /health                  (健康檢查)
    ├── wecom_cs_handler.py          (微信客服邏輯)
    ├── wecom_crypto.py              (AES 加解密)
    ├── wecom_cs_api.py              (微信客服 API)
    ├── wecom_poller.py              (輪詢模式，繞過 ICP)
    ├── whatsapp_handler.py          (WhatsApp 消息處理)
    ├── bot_webhook.py               (課程爬取 + 推送)
    ├── scraper.py                   (DSEDJ 課程爬蟲)
    └── subscription.py              (用戶訂閱管理)
```

---

## 3. 部署資訊

| 項目 | 值 |
|------|-----|
| **Platform** | Zeabur |
| **專案名稱** | parent-school-bot |
| **Project ID** | `6a0d3e3433d1a635fa37e4c5` |
| **Service ID** | `6a0d3e4433d1a635fa37e4c9` |
| **Domain** | `https://parent-school-bot.zeabur.app` |
| **GitHub Repo** | `samulee003/parent-school-cron` |
| **Runtime** | Python (Uvicorn + FastAPI) |
| **Port** | 8080 |
| **Region** | 騰訊雲東京 (43.167.10.6) |

---

## 4. 環境變數

### 4.1 企業微信客服（必填）

| Key | 值 | 說明 |
|-----|-----|------|
| `WECOM_CORP_ID` | `ww9f72e51ed2bf6492` | 企業 ID |
| `WECOM_CS_SECRET` | `VJUU3Brn9SYKDjrhbphVXJmdrGPDKKA_dwkL65Sjp9M` | 客服應用 Secret |
| `WECOM_TOKEN` | `ZiCZrY3f7hUaCuYVdpRPtTDuza0301pE` | 回調 Token |
| `WECOM_ENCODING_AES_KEY` | `ST9SbgiljDAUPJHD3bliy8kUbRRYGWyk3WflFk1pvyy` | AES 加密金鑰 |

### 4.2 WhatsApp Cloud API

| Key | 值 | 說明 |
|-----|-----|------|
| `WHATSAPP_PHONE_NUMBER_ID` | `1093490963851787` | 測試號碼 ID |
| `WHATSAPP_ACCESS_TOKEN` | `EAAMVdegRN...` | Temporary Token |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | `4517619721898015` | Business Account ID |
| `WHATSAPP_VERIFY_TOKEN` | `wxagent_wh_verify_2026` | Webhook 驗證 Token |

### 4.3 其他

| Key | 值 | 說明 |
|-----|-----|------|
| `CRON_SECRET` | `your_cron_secret_here` | Cron 觸發密鑰 |
| `API_BASE_URL` | `https://portal.dsedj.gov.mo` | DSEDJ API |
| `WXAGENT_PUSH_DAY` | `mon` | 推送日 |
| `WXAGENT_PUSH_HOUR` | `9` | 推送時 |
| `WXAGENT_DATA_DIR` | `./data` | 數據目錄 |

---

## 5. API 端點

### 5.1 企業微信客服

| Method | Path | 用途 |
|--------|------|------|
| `GET` | `/api/wecom-cs/callback` | 回調 URL 驗證（企業微信配置時調用） |
| `POST` | `/api/wecom-cs/callback` | 接收加密事件推送 |

**輪詢模式**：因 ICP 備案限制（域名需與企業主體一致），改用 `wecom_poller.py` 每 5 秒輪詢 sync_msg 接口，無需回調 URL。

### 5.2 WhatsApp

| Method | Path | 用途 |
|--------|------|------|
| `GET` | `/api/whatsapp/webhook` | Webhook 驗證（Meta 配置時調用） |
| `POST` | `/api/whatsapp/webhook` | 接收家長消息、回覆課程資訊 |

**Webhook 配置（Meta 後台填寫）：**

| 欄位 | 值 |
|------|-----|
| Callback URL | `https://parent-school-bot.zeabur.app/api/whatsapp/webhook` |
| Verify Token | `wxagent_wh_verify_2026` |

### 5.3 通用

| Method | Path | 用途 |
|--------|------|------|
| `GET` | `/` | 根路由健康檢查 |
| `GET` | `/health` | 詳細健康檢查（含渠道狀態） |
| `GET` | `/api/status` | 用戶統計 |
| `POST` | `/api/push` | 手動觸發課程推送 |
| `POST` | `/api/cron?secret=xxx` | Cron 定時推送 |
| `GET` | `/api/users` | 用戶列表 |

---

## 6. WhatsApp Handler 邏輯

文件：`src/whatsapp_handler.py`（201 行）

### 6.1 關鍵詞匹配

| 觸發詞 | 回覆 |
|--------|------|
| `課程` / `course` / `最新` | 最新 5 門課程列表（爬 DSEDJ） |
| `報名` / `報名表` / `報名連結` | 官網報名指引 |
| `你好` / `hello` / `hi` / `help` / `幫助` | 幫助菜單 |
| 其他 | 引導詞提示 |

### 6.2 非文字消息

圖片、語音等回覆提示「目前只支援文字消息查詢課程」。

---

## 7. 檔案清單

```
project/
├── .env                          # 環境變數（機敏，不進 git）
├── .env.example                  # 環境變數模板
├── .gitignore
├── Dockerfile
├── README.md
├── SPEC.md                       # 本文件
├── docker-compose.yml
├── requirements.txt
├── start.sh / start.bat
├── zeabur.json                   # Zeabur 部署配置
├── zeabur.yaml
├── zbpack.json
├── logo_20260520.png
├── src/
│   ├── api_server.py             # FastAPI 主服務 (407 行)
│   ├── api_server.py.bak         # 備份
│   ├── bot_server.py             # Bot 核心
│   ├── bot_webhook.py            # 課程推送邏輯
│   ├── chat_flow.py              # 對話流程
│   ├── classifier.py             # 消息分類
│   ├── config.py                 # 配置管理
│   ├── main.py                   # CLI 入口
│   ├── scheduler.py              # 排程
│   ├── scraper.py                # DSEDJ 爬蟲
│   ├── subscription.py           # 訂閱管理
│   ├── user_store.py             # 用戶儲存
│   ├── wechat_bot.py             # 微信 Bot
│   ├── wecom_crypto.py           # WeCom AES 加解密
│   ├── wecom_cs_api.py           # WeCom 客服 API
│   ├── wecom_cs_handler.py       # WeCom 客服邏輯
│   ├── wecom_poller.py           # 輪詢模式
│   └── whatsapp_handler.py       # WhatsApp 處理器
├── tests/
└── tools/
    └── wecom_verify_server.py    # WeCom 調試工具
```

---

## 8. 部署工作流

```
本地修改 → git commit → git push → Zeabur 自動偵測 → 構建部署 → 重啟服務
```

**注意**：修改環境變數後需手動重啟服務，否則不生效。

---

## 9. 當前狀態

### 已驗證

- ✅ WeCom 客服：輪詢模式運行中（err 48002 需確認後台客服帳號配置）
- ✅ WhatsApp API：發送正常，你已收到測試訊息
- ✅ WhatsApp Webhook：Meta 驗證通過
- ✅ Health endpoint：返回 `whatsapp: true`、`poller.running: true`

### 限制

| 項目 | 限制 | 解決方案 |
|------|------|----------|
| WeCom ICP | 回調 URL 需域名綁定企業主體 | 改用輪詢模式（已實作） |
| WeCom 客服帳號 | err 48002（無可用客服帳號） | 需在企業微信後台創建客服帳號 |
| WhatsApp 測試號 | 僅支援 API 發送，無法接收用戶消息 | 換正式號碼 + 商業驗證 |
| WhatsApp Token | Temporary，24h 過期 | 轉 Permanent Token（商業驗證後） |

### 待處理

- [ ] WeCom 後台創建客服帳號，解除 48002 錯誤
- [ ] WhatsApp 商業驗證（免費，需上傳機構證明）
- [ ] WhatsApp 換正式澳門號碼
- [ ] WhatsApp Token 轉 Permanent
- [ ] 端到端測試：家長發消息 → bot 回覆

---

## 10. WhatsApp 上線路徑

```
現在（開發模式）
  └── 測試號碼 +1 555-633-0155，API 單向發送 ✅
       │
       ▼
Step 1: 準備澳門手機號（未註冊過 WhatsApp）
       │
       ▼
Step 2: Meta 後台加到 Business Account
       │
       ▼
Step 3: 商業驗證（Business Verification）
        免費，上傳機構資料即可
       │
       ▼
Step 4: 轉 Permanent Token（24h → 永久）
       │
       ▼
Step 5: 更新 PHONE_NUMBER_ID + ACCESS_TOKEN
       │
       ▼
Step 6: 產生 QR code → wa.me/+853xxxxxxx
        家長掃碼即開對話，webhook 雙向收發 ✅
```

---

## 11. 成本估算

| 項目 | 費用 |
|------|------|
| Zeabur | ~$5/月（1 實例） |
| WhatsApp Cloud API | 前 1,000 對話/月免費，之後 ~$0.02/對話 |
| 域名（如有） | ~$10/年 |

初期月費 $5-10 美元，量不大時基本在免費額度內。

---

## 12. 相關連結

- GitHub: `https://github.com/samulee003/parent-school-cron`
- Zeabur Dashboard: `https://dash.zeabur.com`
- Meta Developer: `https://developers.facebook.com`
- DSEDJ 家長學堂: `https://portal.dsedj.gov.mo`

---

## 13. 變更記錄

| 日期 | 版本 | 內容 |
|------|------|------|
| 2026-05-20 | 2.3.0 | 新增 WhatsApp handler + webhook 路由 |
| 2026-05-20 | 2.2.0 | 新增 WeCom 輪詢模式（繞過 ICP） |
| 2026-05-20 | 2.0.0 | WeCom 客服模組初版 |
| 2026-05-19 | 1.0.0 | 專案啟動，課程爬蟲 + 推送基礎 |
