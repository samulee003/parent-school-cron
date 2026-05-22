# Project Memory

最後更新：2026-05-22

這是本地接手記憶。它記錄目前產品狀態、最近驗證、風險和下一步。不要在這裡寫入 token、secret、PIN、平台 ID、Phone Number ID、App Secret、webhook verify token、真實家長電話或完整私密對話。

## Current State

本專案主線是 WhatsApp-first 的「澳門家長學堂課程小助手」。

目前已完成：

- WhatsApp Cloud API webhook receiving and reply sending.
- WhatsApp text, image/unknown media fallback, and voice-note transcription path.
- StepFun ASR priority path, OpenAI transcription fallback when configured.
- DSEDJ 家長學堂課程爬取，包含列表、年齡層、分類、詳情頁大綱和報名 link。
- 直接回覆官方課程 link，不要求家長再回 `詳情1` 才拿 link。
- DSEDJ detail URL normalization，避免 `&regstatus` 在 WhatsApp 變成 `®status`。
- SQLite memory：profile、last query、transcript、human takeover、flags、drafts、LLM cache、LLM usage。
- Agent Inbox `/admin`：登入頁、HttpOnly cookie、Bearer admin auth、家長列表、transcript、人工接手/恢復、人工回覆、profile 編輯、tags、notes、flags、drafts、harness trace。
- Structured profile：`age_groups`、`pain_points`、`topic`、`target`、`pain_summary`。
- WhatsApp onboarding：先問孩子年齡和痛點，不直接倒全部課程。
- Proactive consent：家長可回 `同意推送` / `暫停推送`。
- Proactive drafts：先產生草稿，operator 審批後才發送。
- WhatsApp template send path：超出 24 小時窗口時使用 configured approved template。
- QA feedback：operator 可標記失敗或不確定案例。
- Scrubbed eval export：`/api/whatsapp/qa-feedback/eval-cases`。
- Harness engineering：local NLU、pure route decision、admin-visible trace、golden eval fixture。
- Public beta safety：`/whatsapp` 分享頁標示測試版、官方網站為準、不要輸入敏感資料。
- WhatsApp privacy controls：家長可回 `私隱` 查看資料說明，回 `暫停推送` / `退訂` / `stop` 停止主動提醒，回 `刪除資料` 清除保存的對話記錄和偏好。

## Latest Verification

最新已知線上基準 commit：

```text
a90813d fix: harden whatsapp harness guardrails
```

最近一次本地驗證：

```text
PYTHONPATH=src .venv/bin/python -m unittest
Ran 127 tests OK

.venv/bin/python -B -m compileall src
OK

git diff --check
OK
```

最近一次 reviewer 狀態：

```text
Final reviewer: APPROVED
```

最近一次線上 smoke：

```text
GET https://parent-school-bot.zeabur.app/health -> 200
GET https://parent-school-bot.zeabur.app/admin -> 200
GET /api/whatsapp/agent-tasks without auth -> 401
GET /api/whatsapp/qa-feedback/eval-cases without auth -> 401
```

Deployment note:

- Latest direct Zeabur deployment was observed as `RUNNING`.
- GitHub-triggered deployment for the same commit was still transitioning on the last poll. Do not treat this as failure unless a later check reports failed/removed without a replacement running deployment.

Current local change set:

- Public beta privacy controls and docs were implemented after `a90813d`.
- Before reporting this as deployed, confirm the latest pushed commit and recheck production smoke.

## Public Entry Points

Parent share page:

```text
https://parent-school-bot.zeabur.app/whatsapp
```

Use this for WeChat sharing, posters, QR stickers, and friend tests. It is more WeChat-friendly than raw `wa.me`.

QR images:

```text
https://parent-school-bot.zeabur.app/whatsapp-qr.png
https://parent-school-bot.zeabur.app/whatsapp-qr-clean.png
```

Local QR files:

```text
whatsapp_parent_school_qr.png
whatsapp_parent_school_qr_clean.png
```

Admin:

```text
https://parent-school-bot.zeabur.app/admin
```

Admin uses `ADMIN_SECRET` through login cookie or Bearer auth. Never put the secret in URL or docs.

## Harness Behavior

Key modules:

```text
src/whatsapp_nlu.py
src/whatsapp_harness.py
src/whatsapp_handler.py
src/whatsapp_memory.py
```

Current expected behavior:

- `八歲，情緒` -> local profile update, age group `7-12歲`, pain point `情緒壓力`.
- `十三歲想搵情緒課` -> local age group `13-18歲`, pain point route.
- `8 and 6` -> local age groups `3-6歲` and `7-12歲`.
- `孩子最近做功課很拖拉` -> valid parent pain, ask for age, do not reject as off-topic.
- `推薦餐廳` -> local refusal, no DeepSeek.
- `我小朋友13歲情緒壓力大，想推薦餐廳` -> local refusal, no profile update, no DeepSeek.
- `推薦餐廳課程` -> local refusal, harness trace must say off-topic.
- `牛仔褲8折邊度買` / `女裝8折` / `我想買女裝8號` -> no age parsing, no DeepSeek, supported-query prompt.
- LLM profile extraction requires `in_domain` or `is_course_related` to coerce to true. false, maybe, missing, or invalid flags fail closed.

Golden eval:

```text
tests/fixtures/whatsapp_harness_cases.json
tests/test_whatsapp_harness_eval.py
```

## Conversation Product Rules

Default WhatsApp behavior:

- If parent says only `課程` or `搜尋活動`, ask one short onboarding question.
- If parent gives only age, ask for concern/pain direction.
- If parent gives only pain point, ask for child age.
- If profile has age plus one concern signal, recommend a small set.
- Always include official link in recommendation replies.
- `更多` / `下一頁` / `還有嗎` continues the last persisted query.
- `全部課程` shows compact paginated list.
- `重設` clears current preference.
- `私隱` returns the data-use notice locally without DeepSeek.
- `暫停推送` / `退訂` / `stop` pauses proactive reminders even during human takeover.
- `刪除資料` deletes the parent-owned WhatsApp data from local memory and sends a short confirmation without recording a fresh transcript entry.
- Off-topic requests are answered locally without DeepSeek.
- Human takeover stops AI replies but still records parent transcript.

## Agent Inbox State

The inbox is no longer just a message viewer. It is the operator workspace.

Current capabilities:

- List conversations with status/filter/search.
- View transcript with parent/AI/admin/system source.
- Take over and resume AI.
- Send manual WhatsApp reply.
- Edit structured profile.
- Edit tags and notes.
- View flags and recent proactive drafts.
- View harness route, action, LLM allow status, and purpose.
- Mark QA feedback.
- Export scrubbed feedback as eval cases.

Keep the UI dense and work-console-like. Avoid hero sections and marketing copy.

## Data Tables To Know

Important SQLite tables:

```text
whatsapp_memory
whatsapp_conversations
whatsapp_messages
whatsapp_agent_flags
whatsapp_proactive_drafts
processed_whatsapp_messages
llm_daily_usage
llm_response_cache
whatsapp_qa_feedback
```

Do not create new tables unless the existing JSON fields and queue tables are clearly insufficient.

## Source Of Truth

Course facts come from DSEDJ.

DeepSeek can:

- rewrite recommendation text,
- reason over provided candidate summaries,
- extract profile fields from in-domain parent wording.

DeepSeek cannot:

- invent courses,
- invent dates,
- invent seats or registration status,
- answer restaurants, weather, investment, homework solving, translation, coding, or general chat,
- use URLs outside the candidate payload.

## WhatsApp Template State

Template sending code exists and is tested.

Known template config names used by the app:

```text
WHATSAPP_PROACTIVE_TEMPLATE_NAME
WHATSAPP_PROACTIVE_TEMPLATE_LANGUAGE
```

Known intended template name:

```text
parent_course_reminder
```

Meta approval state is external and can change. Before relying on outside-window proactive pushes, recheck Meta WhatsApp Manager and send one controlled template test.

## Voice Notes

Voice-note path:

```text
WhatsApp audio media
  -> download media
  -> StepFun ASR if configured
  -> OpenAI transcription fallback if configured
  -> normal text harness
```

If transcription fails:

- Record placeholder transcript.
- Add handoff flag with provider error.
- Ask parent to send text or use keyboard voice dictation.

Do not store raw audio in git.

## Friend Testing Protocol

When friends test the bot:

1. Ask them to use the `/whatsapp` share page or QR.
2. Encourage natural language, not only keywords.
3. Watch `/admin` for transcript and harness trace.
4. If AI misses intent, mark QA feedback in admin.
5. Convert representative failures into eval cases.
6. Do not paste raw private conversations into committed files.
7. Tell testers they do not need to provide child name, school, certificate number, address, or other sensitive data.
8. If someone asks about data, tell them to reply `私隱`; if they want removal, tell them to reply `刪除資料`.

Good test prompts:

```text
小朋友8歲，最近情緒好大
13歲，想搵升學壓力課
孩子最近做功課很拖拉
還有嗎
全部課程
推薦餐廳
牛仔褲8折邊度買
私隱
stop
刪除資料
```

## Risk Register

- DSEDJ HTML can change and break scraping.
- Meta template approval and payment/review restrictions can block proactive messages.
- WhatsApp Cloud API number cannot also be a normal WhatsApp app number.
- ASR quality varies between Mandarin, Cantonese, noisy recordings, and short clips.
- DeepSeek output quality depends on JSON compliance, so fail closed.
- Admin transcript contains personal data; keep exports scrubbed.
- Friend tests can reveal missing intent patterns; turn them into evals before changing prompts.
- `/health` has some legacy WeCom fields; verify WhatsApp separately when diagnosing.
- `刪除資料` conservatively clears the whole LLM response cache because cache rows are not phone-scoped.

## Next Recommended Work

1. Use `/admin` during friend testing and mark every bad reply as QA feedback.
2. Commit/push/deploy the public beta privacy controls if not already done.
3. Recheck Meta template approval and perform one outside-window proactive template test.
4. Add a small scheduled eval workflow from scrubbed QA feedback.
5. Improve Agent Inbox polish only where it speeds operator decisions.
6. Add analytics for most common missing fields, off-topic blocks, and no-match flags.
7. Consider a daily agent task queue summary: unresolved flags, draft approvals, stale conversations, new QA feedback.

## Completion Checklist

Before saying a change is done:

```text
1. Read agent.md, agents.md, memory.md.
2. Run targeted tests for changed behavior.
3. Run full unittest suite.
4. Run compileall.
5. Run git diff --check.
6. Check git status.
7. If deployed, verify /health, /admin, and unauthorized API 401.
8. Do not expose secrets in the report.
```
