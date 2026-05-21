# WhatsApp 上線設定指南

這份指南只保留目前要走的主線：WhatsApp Cloud API。

## 1. Meta 後台

1. 到 Meta Developers 建立或打開 App。
2. 加入 WhatsApp 產品。
3. 取得 `Phone Number ID`。
4. 建立 Permanent Access Token。
5. 在 Webhooks 設定 callback：
   - Callback URL：`https://你的域名/api/whatsapp/webhook`
   - Verify Token：填入你設定的 `WHATSAPP_VERIFY_TOKEN`
   - Subscribe field：`messages`

## 2. Zeabur 環境變數

必填：

```text
WHATSAPP_PHONE_NUMBER_ID=...
WHATSAPP_ACCESS_TOKEN=...
WHATSAPP_VERIFY_TOKEN=...
WHATSAPP_APP_SECRET=...
CRON_SECRET=...
ADMIN_SECRET=...
WXAGENT_DATA_DIR=/app/data
```

`WHATSAPP_APP_SECRET` 用來驗證 Meta 發來的 webhook 簽名，正式上線建議一定要填。

## 3. 驗證

打開：

```text
https://你的域名/health
```

Meta webhook 驗證通過後，用允許的測試手機號向 WhatsApp 測試號碼發：

```text
課程
```

應該收到最新課程列表。再測：

```text
0-2歲
報名
幫助
```

## 4. 公開給家長前

- 測試號碼只能給白名單用戶；公開前要換正式號碼。
- Temporary token 會過期；公開前要換 Permanent token。
- 主動每週推送需要已核准 WhatsApp message template。Meta 審批通過後，在
  Zeabur 設定 `WHATSAPP_PROACTIVE_TEMPLATE_NAME`，語言可用
  `WHATSAPP_PROACTIVE_TEMPLATE_LANGUAGE=zh_HK`。
