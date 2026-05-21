# Agent Guide

本文件是給 Codex / Claude / 其他 agent 接手本專案時先讀的工作規則。請先讀本文件，再讀 `memory.md`、`README.md`、`SPEC.md`。

## Product Direction

這個專案目前是 WhatsApp-first 的「澳門家長學堂課程小助手」。

核心目標不是把全部課程倒給家長，而是：

- 讓家長用 WhatsApp 問一句自然語句。
- 記住孩子年齡、偏好、上次查詢。
- 只推薦少量相關課程。
- 直接附官方報名連結。
- 不回答與家長學堂課程無關的通用 AI 問題，避免 API 成本失控。

企業微信相關程式留作 legacy fallback。除非使用者明確要求，新的工作一律以 WhatsApp Cloud API 為主。

## Non-Negotiables

- 不要把任何 token、secret、PIN、Phone Number ID、App Secret、service id、webhook verify token 寫入文件、測試、log 或 commit。
- 不要執行會完整列出 Zeabur / Meta secrets 的命令；如果必須檢查變數，只檢查 key 是否存在或手動遮蔽值。
- 不要把 WhatsApp Cloud API access token 回覆給使用者。
- 不要把 WhatsApp Cloud API PIN 回覆給使用者；本機如需保存，只能放在 macOS Keychain 或平台 secret store。
- 不要讓 DeepSeek 回答餐廳、天氣、投資、功課、翻譯、寫 code 等無關問題。
- 不要為了看起來「更 AI」而創造不存在的課程、日期、名額或連結。
- 課程資料以 DSEDJ 家長學堂公開頁面為來源，實際報名狀態以官方網站為準。

## Main Files

| File | Purpose |
| --- | --- |
| `src/api_server.py` | FastAPI 入口、健康檢查、WhatsApp webhook、管理 API |
| `src/whatsapp_handler.py` | WhatsApp 對話邏輯、課程推薦、DeepSeek guardrail、分頁、人工回覆 |
| `src/whatsapp_memory.py` | SQLite 記憶、對話紀錄、接手狀態、去重、LLM 使用量、LLM 快取 |
| `src/scraper.py` | DSEDJ 家長學堂課程抓取、詳情頁大綱與報名連結解析 |
| `tests/test_whatsapp_handler.py` | WhatsApp 對話、DeepSeek 成本守門、webhook 測試 |
| `tests/test_scraper_classifier.py` | 爬蟲與分類測試 |
| `README.md` | 使用者向說明 |
| `SPEC.md` | 系統規格 |
| `memory.md` | 目前接手記憶與下一步 |

## Runtime Shape

```text
Parent WhatsApp message
  -> Meta WhatsApp Cloud API webhook
  -> FastAPI /api/whatsapp/webhook
  -> WhatsAppHandler
  -> WhatsAppMemoryStore
  -> CourseScraper reads DSEDJ parent academy courses
  -> optional DeepSeek recommendation for in-domain queries
  -> WhatsApp Cloud API sends reply
```

The public parent entry point is:

```text
https://parent-school-bot.zeabur.app/whatsapp
```

Use this share page for WeChat, posters, and QR stickers. It shows a WeChat
fallback instruction and tries to open the WhatsApp app from normal mobile
browsers. The raw `wa.me` link still works as a fallback, but do not use it as
the main WeChat sharing URL.

The local QR image currently lives at:

```text
whatsapp_parent_school_qr.png
```

There is also a clean QR-only image:

```text
whatsapp_parent_school_qr_clean.png
```

The public service exposes them at `/whatsapp-qr.png` and `/whatsapp-qr-clean.png`.

## Required Environment Variables

Use environment variables only. Do not hard-code real values.

```text
WHATSAPP_PHONE_NUMBER_ID
WHATSAPP_ACCESS_TOKEN
WHATSAPP_VERIFY_TOKEN
WHATSAPP_APP_SECRET
WHATSAPP_BUSINESS_ACCOUNT_ID
DEEPSEEK_API_KEY
DEEPSEEK_BASE_URL
DEEPSEEK_MODEL
DEEPSEEK_DAILY_LIMIT_PER_USER
DEEPSEEK_DAILY_LIMIT_GLOBAL
WHATSAPP_PROACTIVE_TEMPLATE_NAME
WHATSAPP_PROACTIVE_TEMPLATE_LANGUAGE
CRON_SECRET
ADMIN_SECRET
WXAGENT_DATA_DIR
WHATSAPP_MEMORY_DB
```

`WHATSAPP_MEMORY_DB` is optional locally; by default the SQLite DB is under `./data/whatsapp_memory.db`.

## Local Workflow

From the repo root:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python src/api_server.py
```

Verify before reporting completion:

```bash
python -m unittest
python -B -m compileall src
git diff --check
```

Health check:

```bash
curl http://127.0.0.1:8000/health
```

## Deployment Notes

The service is deployed on Zeabur as a FastAPI container. Keep deployment commands and platform IDs outside committed docs unless the user explicitly asks for a private local runbook.

When deploying:

- Confirm tests pass first.
- Do not print secret values while updating Zeabur variables.
- Restart the service after changing WhatsApp or DeepSeek variables.
- Check `/health`.
- WhatsApp POST webhook is fail-closed: `WHATSAPP_APP_SECRET` must be configured
  or incoming webhook events are rejected, unless the temporary
  `WHATSAPP_ALLOW_UNSIGNED_WEBHOOK=true` compatibility switch is explicitly set.
  Remove that switch after Meta App Secret is configured.
- Check Meta webhook verification only with the configured verify token, without exposing it.
- If webhook receives but user sees no reply, check Cloud API registration, app subscription to `messages`, and access-token permissions.

## WhatsApp Cloud API Notes

The public WhatsApp number is a Cloud API business number. A Cloud API number cannot also be used as a normal WhatsApp mobile-app account.

If WhatsApp says the number has no account after adding it, likely causes:

- Phone number was verified in Meta but not registered to Cloud API.
- PIN registration was not completed.
- Wrong number was shared with parents.
- Meta review/payment/template restrictions affect outbound business-initiated messages.

User-initiated replies inside the 24-hour window should work once Cloud API registration and `messages` subscription are correct.

Business-initiated proactive messages outside the 24-hour customer service window
must use an approved WhatsApp template. Configure the template with
`WHATSAPP_PROACTIVE_TEMPLATE_NAME` and optionally
`WHATSAPP_PROACTIVE_TEMPLATE_LANGUAGE` (default `zh_HK`).

Current production template setup:

- Template name: `parent_course_reminder`
- Meta UI language: `Chinese (HKG)`
- API language code configured in Zeabur: `zh_HK`
- Current Meta review status on 2026-05-21: pending review.

## Conversation Design Rules

Default WhatsApp behavior:

- If the user says `課程` with no profile, ask one short narrowing question instead of dumping all courses.
- First broad requests without profile should start the short onboarding question:
  child age plus one concern area.
- Only recommend courses after the profile has at least one age group and one
  concern signal such as pain point, topic, target, or clear preference.
- If the user gives age or preference, update memory; if the profile is still
  incomplete, ask for the one missing piece instead of calling DeepSeek.
- Ask for proactive push consent only after a useful recommendation, not before.
- If the user says `更多`, `下一頁`, or `還有嗎`, continue the last persisted query.
- If the user asks for all courses, paginate compactly.
- Always include official detail links in course replies.
- When agentic recommendation is enabled, use course detail summaries for pain-point
  matching. Do not match only by course name.
- Prefer the real registration URL from the detail page when available; fall back to
  the DSEDJ detail URL.
- DSEDJ detail links must be normalized before sending. Avoid raw `&regstatus`
  in WhatsApp text because it can render as `®status`; use the helper in
  `src/scraper.py` so links become `?regstatus=...&msg_id=...&langsel=C`.
- Keep replies short enough for WhatsApp scanning.

Supported memory:

- `profile_json`: child age groups, target, topic.
- `last_query_json`: age group, target, topic, page.
- `processed_whatsapp_messages`: webhook duplicate protection.
- `llm_daily_usage`: per-user and global DeepSeek limits.
- `llm_response_cache`: cost-saving cache for identical recommendation contexts.
- `whatsapp_conversations`: parent conversation status, latest activity, notes/tags, proactive consent status, proactive notes.
- `whatsapp_messages`: inbound/outbound transcript for parent, AI, admin, and system messages.
- `whatsapp_agent_flags`: placeholder table for no-match/uncertain/handoff flags.
- `whatsapp_proactive_drafts`: persistent proactive draft queue/history; keeps AI original text, operator-edited draft, sent text, match snapshot, status, and send timestamps.

## DeepSeek Guardrail

DeepSeek is only an enhancer, not the source of truth.

Before calling DeepSeek:

- First check if the message is in the course domain.
- Reject off-topic questions locally.
- Reject long/noisy non-course messages locally before profile update or LLM calls.
- Extract age and preference with deterministic rules.
- Fetch candidate courses from DSEDJ.
- Enrich candidates with detail-page summaries when the answer depends on pain
  points or proactive matching.
- Pass only candidate course data to DeepSeek.
- The candidate payload includes `summary`, `registration_url`, and `reply_url`;
  DeepSeek should reason from those fields and paste only provided URLs.
- Enforce per-user and global daily limits.
- Use cached replies where possible.

If DeepSeek fails, times out, exceeds quota, or is disabled, fall back to rule-based recommendations.

## Agentic Admin Direction

The major product direction is not more WhatsApp commands. It is a real operator interface and proactive matching loop.

Already started:

- `/admin` dashboard protected by `ADMIN_SECRET` login and an HttpOnly session
  cookie; do not put admin secrets in URLs.
- Parent list with phone, latest message, known memory, and status.
- Inbox filters for all, human takeover, AI auto, flagged, pushable, and draft conversations.
- Full inbound/outbound message transcript.
- Human takeover / resume AI switch.
- Manual reply from dashboard through WhatsApp Cloud API.
- Structured Profile editor for `age_groups`, `pain_points`, `target`, `topic`, and `pain_summary`; admin-set topic uses `topic_source="admin"`.
- Agent State summary per parent: profile readiness, missing fields, recommended next action, open flag count, and draft count.
- Notes/tags per parent.
- AI uncertainty and no-match flags.
- Proactive matching draft endpoint based on stored memories and course summaries.
- Persistent proactive draft queue/history: generate drafts, edit, send, skip, and inspect past sent/skipped/failed items.
- Proactive draft queue filters by status, consent status, and search text so the operator can find pending/history items quickly.
- Parent consent status for proactive pushes: `unknown`, `allowed`, `paused`.
- Operator-approved proactive draft sending for parents with `allowed` consent.
- Privacy pruning endpoint for old operational history: preview or delete old transcripts, LLM cache, processed message IDs, resolved flags, and closed proactive draft history while preserving parent profiles and open drafts.
- WhatsApp-side consent capture: parents can reply `同意推送` / `同意收課程提醒`
  or `暫停推送`.
- WhatsApp template send path for proactive drafts outside the 24-hour window.

Next:

- Stronger proactive workflow: new course -> match parent memories -> review queue -> operator approve/send.
- Add richer proactive draft filters and retention/pruning as queue volume grows.
- Recheck Meta WhatsApp Manager until `parent_course_reminder` is approved/active, then run one real template send test.

When building this, prefer the existing FastAPI app and SQLite memory store first. Avoid adding a heavy frontend framework unless the dashboard grows beyond simple HTML/JS.

## Testing Priorities

Tests should cover:

- Off-topic messages do not call DeepSeek.
- `更多` works after a restart via persisted last query.
- DeepSeek cache avoids duplicate calls.
- DeepSeek replies that contain URLs outside the candidate course allowlist fall
  back to deterministic recommendations.
- DeepSeek quota fallback still returns useful courses.
- Webhook duplicate message ids are processed once.
- Admin endpoints require auth.
- Human takeover suppresses AI auto-reply.
- Manual admin reply records outbound messages.
- Long non-course messages do not call DeepSeek or update parent profile.

## Working Style

- Keep changes small and behavior-focused.
- Prefer deterministic code before LLM calls.
- Add tests for every behavior that affects cost, privacy, or parent-facing replies.
- Write Traditional Chinese user-facing copy.
- Keep technical logs useful but avoid logging private user content beyond what is needed for debugging.
- When in doubt, optimize for a parent who is tired and wants a short useful answer.
