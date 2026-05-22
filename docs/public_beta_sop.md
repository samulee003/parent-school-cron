# Public Beta SOP

最後更新：2026-05-22

本文件是「個人 Facebook / 朋友測試」前的營運清單。不要在本文件寫入 token、secret、平台 ID、真實家長電話或完整私密對話。

## Beta Positioning

對外用語：

- 說「測試版 WhatsApp 課程小助手」。
- 說「課程資料以澳門家長學堂官方網站為準」。
- 說「不用輸入小朋友姓名、學校、證件、住址或敏感資料」。
- 不說「官方助手」。
- 不說「保證報名成功」。
- 不要求家長提供小朋友姓名、學校或其他身份資料。

分享入口：

```text
https://parent-school-bot.zeabur.app/whatsapp
```

不要把 raw `wa.me` 當主入口貼到 WeChat 或 Facebook；分享頁會提供 WhatsApp app handoff、WeChat 提示和 QR fallback。

## Parent Controls

家長可以在 WhatsApp 直接使用：

```text
私隱
```

用途：查看資料使用、敏感資料提醒、停止推送和刪除資料說明。

```text
暫停推送
退訂
stop
```

用途：停止主動課程提醒。即時查課程仍然可用。

```text
刪除資料
```

用途：清除該 WhatsApp 家長在本服務保存的 profile、last query、conversation row、transcript、flags、proactive drafts、QA feedback、LLM usage，以及全域 LLM response cache。全域 cache 沒有 phone 欄位，為了避免無法定向刪除，刪除請求會保守清空整個 cache。

## Facebook Post Checklist

發布前確認：

```text
GET /health -> 200
GET /admin -> 200
GET /api/whatsapp/agent-tasks without auth -> 401
GET /api/whatsapp/qa-feedback/eval-cases without auth -> 401
```

文案必須包含：

- 測試版。
- 官方網站為準。
- 不用輸入敏感資料。
- 答得不準可直接回覆指出。

## Friend Test SOP

1. 請朋友用 `/whatsapp` 分享頁或 QR 開始。
2. 鼓勵自然語句，例如「小朋友8歲，最近情緒好大」。
3. 在 `/admin` 觀察 transcript、profile、harness trace。
4. 對每個答錯、不懂、太長、推薦不準的案例標 QA feedback。
5. 定期用 `/api/whatsapp/qa-feedback/eval-cases` 匯出 scrubbed cases。
6. 只把代表性 scrubbed case 加進 eval fixture，不把 raw transcript 放進 git。

## Operator Triage

每天至少看一次：

- 未解 flags。
- 新 QA feedback。
- profile_ready=false 但對話已多輪的家長。
- draft queue 裡等待審批的主動推送。
- 語音轉文字失敗 flag。

處理順序：

1. 先看 safety 或 privacy 相關回饋。
2. 再看「AI 不理解自然語句」。
3. 再看「課程推薦不準」。
4. 最後看 UI/文案體驗。

## Release Gate

小範圍朋友 beta 可以開始，但大規模公開前至少完成：

- 所有已暴露 API key rotate 完成。
- Meta WhatsApp template 狀態確認 approved。
- 一次 outside-window template controlled send test。
- 至少一輪 QA feedback -> scrubbed eval fixture -> tests pass。
- 每日 health / admin / unauthorized API 401 巡檢 automation。
