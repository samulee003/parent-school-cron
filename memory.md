# Project Memory

最後更新：2026-05-21

這份文件是本地接手記憶，記錄目前已完成的狀態、產品判斷、風險與下一步。不要在這裡寫入任何 token、secret、PIN、平台 ID、Phone Number ID 或 webhook verify token。

## Current State

專案已從企業微信方向轉為 WhatsApp-first。

目前主線功能：

- 家長可透過 WhatsApp 查詢澳門家長學堂課程。
- 已接入 WhatsApp Cloud API。
- 已接入 DeepSeek 作為課程推薦文字助手。
- 已有 SQLite 記憶：家長偏好、上次查詢、訊息去重、LLM 使用量、LLM 快取。
- 已有 off-topic guardrail，無關問題不應進入 DeepSeek。
- 課程回覆已改為直接附官方報名連結，不再要求先回覆 `詳情1` 才看到 link。
- `更多`、`下一頁`、`還有嗎` 會延續上次查詢。
- `13-18歲` / `青少年` 查詢已修正為會抓該年齡層來源，不只看首頁 open list。
- 根目錄 `/` 是給 Meta / 家長看的公開 landing page，`HEAD /` 也會回 200。

家長入口：

```text
https://wa.me/8614714949607?text=%E8%AA%B2%E7%A8%8B
```

本地 QR 圖：

```text
whatsapp_parent_school_qr.png
whatsapp_parent_school_qr_clean.png
```

線上 QR：

```text
https://parent-school-bot.zeabur.app/whatsapp-qr.png
https://parent-school-bot.zeabur.app/whatsapp-qr-clean.png
```

## Verified

最近一次完整本地測試曾通過：

```text
python -m unittest
python -B -m compileall src
git diff --check
```

最近一次已知測試數量：`39` 個 unittest 通過。

線上曾驗證：

- `/health` 顯示 healthy。
- WhatsApp 設定存在。
- Meta webhook GET 驗證成功。
- WABA 已訂閱 `messages` field。
- WhatsApp Cloud API phone 已完成 Cloud API registration。
- 使用者曾回報自己發測試訊息成功。
- 2026-05-21 修正了 DSEDJ detail URL 的 `&regstatus` / `®status` 問題；現在課程連結會改成 `?regstatus=...&msg_id=...&langsel=C`，避免 WhatsApp 或 LLM 把 `&regstatus` 變成註冊商標符號。

注意：這些是 2026-05-21 的接手記憶。若要對外宣布現在狀態，先重新跑健康檢查與端到端測試。

## Important Decisions

### 1. WhatsApp is the main channel

企業微信折騰太久，主線改為 WhatsApp。WeCom 程式保留，但不要再把主要時間花在 WeCom，除非使用者重新指定。

### 2. AI must be domain-limited

使用者明確擔心 DeepSeek API 會被無關問題燒爆。現在方向是：

- 規則先判斷是否與家長學堂課程有關。
- 無關問題直接本地拒答。
- 有關問題才可用 DeepSeek 改寫/推薦。
- DeepSeek 只能基於候選課程回答。

### 3. Direct links are better than detail follow-up

為了節省使用者 token 和家長步驟，課程列表應直接包含官方連結，不要再要求家長回 `詳情1`、`詳情2`。

課程連結要先經過 `normalize_course_detail_url()`。這不是美觀問題，而是 WhatsApp/LLM 可能把 `&regstatus` 轉成 `®status`，導致手機端打不開。

### 4. Agentic means memory plus handoff

「Agentic AI 助手」不只是 LLM 回答。真正需要的是：

- 記得每位家長的孩子年齡和偏好。
- 看到完整對話。
- AI 不確定時可以標記。
- 人可以接手。
- 接手後 AI 暫停。
- 人恢復 AI 後，AI 繼續用同一份記憶工作。

## Current Code Reality

### Already exists

- WhatsApp webhook receiving.
- Text reply through Cloud API.
- Course scraping by DSEDJ age/topic/target/status.
- Basic memory tables.
- DeepSeek prompt and fallback.
- LLM cache and quota.
- Off-topic tests.
- Admin API only at a primitive level: `/api/users`, `/api/push`, `/api/cron`.

### Does not exist yet

- Real `/admin` dashboard.
- Full inbound/outbound transcript table.
- Human takeover status.
- Manual reply UI.
- Parent notes/tags.
- AI uncertainty queue.
- Proactive personalized course push flow.
- WhatsApp template management for messages outside the 24-hour user window.

## Recommended Next Build

Build the operator dashboard first.

Minimum useful MVP:

1. Add database tables for conversation messages and parent agent state.
2. Record every inbound WhatsApp text message.
3. Record every outbound bot/admin reply.
4. Add `/admin` HTML dashboard.
5. Show parent list, latest message, memory summary, and transcript.
6. Add human takeover / resume AI.
7. Add manual send message endpoint.
8. Add tests for auth, transcript logging, takeover suppression, and manual reply.

Recommended table direction:

```text
whatsapp_conversations
- phone primary key
- display_name
- status: ai | human
- tags_json
- notes
- last_message_at
- updated_at

whatsapp_messages
- id primary key
- phone
- direction: inbound | outbound
- source: parent | ai | admin | system
- body
- meta_json
- created_at

whatsapp_agent_flags
- id primary key
- phone
- flag_type: no_match | uncertain | handoff_needed | error
- summary
- resolved_at
- created_at
```

Keep this inside `WhatsAppMemoryStore` first unless the app clearly outgrows SQLite.

## Admin UX Notes

This is not a marketing page. It should feel like a quiet work console:

- Left: parents / conversations.
- Center: transcript.
- Right: memory, tags, notes, takeover switch.
- Top: search by phone, status filters.
- Buttons: send, take over, resume AI, clear memory.
- Copy should be Traditional Chinese and short.

Do not build a large hero page. Do not add decorative sections. The first screen should be the actual inbox.

## Risk Register

- DSEDJ page HTML can change and break scraping.
- WhatsApp Cloud API templates/payment/review may affect proactive outbound messages.
- A Cloud API number cannot be used as a normal WhatsApp app number.
- If platform secrets are rotated, Zeabur must be updated and service restarted.
- DeepSeek model names can change; keep fallback working.
- Logging full phone/message content has privacy risk; be careful before expanding logs.
- Current `/health` includes `webhook_configured`, but that field comes from legacy WeCom and can be misleading for WhatsApp.

## Useful Manual Checks

Before saying production is healthy:

```text
1. Check web health endpoint.
2. Verify WhatsApp webhook GET challenge.
3. Send a WhatsApp message from a real phone: "小朋友13歲，想家長課".
4. Confirm reply includes only relevant courses and direct links.
5. Send "還有嗎" and confirm next page.
6. Send an off-topic question like "推薦餐廳" and confirm DeepSeek is not called.
7. Check service logs for errors without printing secrets.
```

## Parent-Facing Copy Direction

Good:

```text
我先按「青少年期 / 家長」幫你挑少量課程：
```

Good:

```text
目前沒有完全符合「青少年期 / 親子」的課程。你可以放寬成只看青少年，或回覆「全部課程」。
```

Avoid:

```text
以下是全部 60 個課程...
```

Avoid:

```text
我可以回答任何問題。
```

Avoid:

```text
請回覆詳情1看連結。
```

## Handoff Reminder

When picking this up again, start with:

```text
1. Read agent.md.
2. Read this memory.md.
3. Check git status.
4. Run tests before and after behavior changes.
5. Keep WhatsApp/DeepSeek secrets out of output.
```
