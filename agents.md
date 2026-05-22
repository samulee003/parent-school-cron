# Agents

最後更新：2026-05-22

這份文件定義本專案的 agent 分工、接手方式與驗證責任。任何 agent 接手前，先讀：

1. `agent.md`
2. `agents.md`
3. `memory.md`
4. `docs/harness_engineering.md`
5. `README.md` 和 `SPEC.md`

不要在本文件寫入 token、secret、PIN、Phone Number ID、App Secret、service id、webhook verify token、真實家長電話或完整對話。

## Mission

本專案是 WhatsApp-first 的澳門家長學堂課程小助手。目標不是讓 LLM 自由聊天，而是讓家長用 WhatsApp 自然輸入，系統透過記憶、課程資料、guardrail、人工接手和 eval 迭代，提供少量、準確、有官方連結的課程推薦。

Agentic AI 在這裡的意思是：

- 先訪談家長，理解孩子年齡、痛點、偏好。
- 把資訊寫入結構化 profile 和 tags。
- 只在澳門家長學堂課程範圍內使用 LLM。
- 不確定時標 flag，讓人接手。
- 人工修正後，AI 後續要沿用更新後的記憶。
- 朋友測試出的失敗案例要變成 QA feedback 和 eval cases。

## Core Boundaries

- DSEDJ 家長學堂公開頁面是課程真相來源。
- DeepSeek 只做語意抽取和候選課程內的推薦文案，不是資料來源。
- StepFun ASR 只負責 WhatsApp 語音轉文字，轉錄後仍要進同一套文字 harness。
- Admin profile update 不能繞過 off-topic guardrail。
- 主動推送必須尊重 WhatsApp 24 小時窗口和 approved template 規則。
- `/admin` 和所有 WhatsApp 管理 API 必須保持 admin auth。
- QA eval export 只能輸出 scrubbed/anonymized case，不能輸出 raw phone 或完整原始 transcript。
- 家長自助資料控制指令必須本地處理：`私隱`、`暫停推送`、`退訂`、`stop`、`刪除資料` 不交給 LLM。

## Agent Roles

### 1. Lead Controller

負責範圍控制、任務排序、最後驗證和對使用者匯報。

責任：

- 先讀 `agent.md`、`agents.md`、`memory.md`。
- 把需求拆成可測的工程任務。
- 避免讓不同 worker 改同一批檔案。
- 收斂 code review feedback，不盲目接受，也不忽略。
- 最後跑完整 verification，再回報。

不得：

- 把「程式碼已改」等同「線上已成功」。
- 跳過 reviewer 提出的 guardrail/security 問題。
- 在 final answer 裡輸出任何 secret。

### 2. Product Agent

負責家長體驗、onboarding 話術、WeChat 分享入口、Agent Inbox 工作流。

判斷原則：

- 家長第一次來，不直接倒全部課程，先問一個短問題。
- 若家長只說「課程」或「搜尋活動」，問孩子年齡和痛點。
- 若家長已給年齡和痛點，推薦少量課程並附官方連結。
- 回覆要短、自然、像真人助手，不像公告。
- WeChat 分享以 `/whatsapp` 分享頁為主，不直接把 raw `wa.me` 當主入口。
- 公開 Facebook / 朋友測試時，要標示測試版、官方網站為準、不要輸入小朋友姓名或學校。

### 3. Harness Engineer

負責 bounded AI harness：local NLU、routing decision、LLM gating、harness trace。

主要檔案：

- `src/whatsapp_nlu.py`
- `src/whatsapp_harness.py`
- `src/whatsapp_handler.py`
- `tests/test_whatsapp_nlu.py`
- `tests/test_whatsapp_harness.py`
- `tests/fixtures/whatsapp_harness_cases.json`
- `tests/test_whatsapp_harness_eval.py`

必守規則：

- off-topic 必須先 fail closed。
- 混合句如「小朋友13歲情緒壓力大，想推薦餐廳」不能進 DeepSeek。
- 購物近似句如「牛仔褲8折邊度買」「女裝8折」不能被 `仔` / `女` 誤判成年齡。
- `八歲`、`十三歲`、`8 and 6` 要能轉成官方年齡層。
- LLM domain flag 只有明確 true 才可接受；false、maybe、缺失、無法解析都 fail closed。

### 4. Conversation Memory Agent

負責家長記憶、profile、tags、notes、flags、drafts。

主要檔案：

- `src/whatsapp_memory.py`
- `src/api_server.py`
- `tests/test_whatsapp_handler.py`

核心資料：

- `whatsapp_memory.profile_json`
- `whatsapp_memory.last_query_json`
- `whatsapp_conversations`
- `whatsapp_messages`
- `whatsapp_agent_flags`
- `whatsapp_proactive_drafts`
- `llm_daily_usage`
- `llm_response_cache`

規則：

- 人工改 profile 後，AI 後續推薦以 `profile_json` 為準。
- admin notes 不可被 onboarding machine note 覆蓋。
- human takeover 期間，只記 transcript，不自動回覆。
- flags 解決前要保留給 operator 看。
- `刪除資料` 要清 profile、last query、conversation、transcript、flags、drafts、QA feedback、LLM usage；因 LLM cache 沒有 phone 欄位，採保守清空。

### 5. Course Data Agent

負責 DSEDJ 爬蟲、分類、詳情頁大綱、報名 link。

主要檔案：

- `src/scraper.py`
- `tests/test_scraper_classifier.py`

規則：

- 優先使用官方 DSEDJ 資料。
- 課程推薦不能只看 title，要看 summary / outline 是否對應痛點。
- 發給家長的 link 必須經過 URL normalization。
- 避免 WhatsApp 把 `&regstatus` 變成 `®status`。
- 若 DSEDJ HTML 改版，先寫 regression test，再修 parser。

### 6. Admin Inbox Agent

負責 `/admin` 工作台和人工接手效率。

主要檔案：

- `src/api_server.py`
- `tests/test_whatsapp_handler.py`

產品形態：

- 左欄：家長列表、搜尋、狀態 filter。
- 中欄：transcript、方向、來源、人工回覆。
- 右欄：takeover 狀態、profile、tags、notes、flags、drafts、harness trace。

規則：

- 不做 landing page。
- 不塞大段說明文字。
- 密度要高，像 Intercom / Crisp 的客服 inbox。
- Auth 一律保留 HttpOnly cookie / Bearer admin auth。

### 7. Ops Agent

負責 Zeabur、Meta WhatsApp Cloud API、health checks、template 狀態、部署。

規則：

- 不輸出平台 secret。
- 更新環境變數時只檢查 key 是否存在，不列值。
- 部署後必查 `/health`、`/admin`、未授權 API 是否 401。
- 不能把 `webhook_configured` 當成 WhatsApp 完整健康狀態，它有 legacy WeCom 背景。
- Meta template approval 是外部狀態，對外說明前要重新到 Meta 後台確認。

### 8. QA / Eval Agent

負責把朋友測試和 operator feedback 轉成可重跑的 eval。

主要檔案：

- `tests/fixtures/whatsapp_harness_cases.json`
- `tests/test_whatsapp_harness_eval.py`
- `src/whatsapp_memory.py`
- `src/api_server.py`

流程：

1. Operator 在 `/admin` 標記錯誤回覆或不確定案例。
2. 系統用 `/api/whatsapp/qa-feedback/eval-cases` 匯出 scrubbed case。
3. 人或 agent 把代表性案例放進 fixture。
4. 每次改 harness 都跑 golden eval。

隱私：

- eval fixture 用 synthetic 或 scrubbed。
- 不放電話、email、學校、姓名、完整聊天紀錄。
- 真實資料只留在受保護 DB，不進 commit。

### 9. Safety Reviewer

負責 review off-topic、PII、secret、LLM 成本和資料外洩風險。

必測例子：

- `推薦餐廳`
- `我小朋友13歲情緒壓力大，想推薦餐廳`
- `牛仔褲8折邊度買`
- `女裝8折`
- `幫我寫 Python code`
- `孩子最近做功課很拖拉`
- `8 and 6`
- `八歲，情緒`

通過標準：

- 無關問題不 call DeepSeek。
- 真家長痛點不被誤殺。
- admin export 不帶 raw phone。
- WhatsApp `私隱`、`退訂`、`stop`、`刪除資料` 不 call DeepSeek。
- tests 和 compile 都過。

## Standard Build Flow

1. Read docs and current memory.
2. Identify exact files to change.
3. Add or update tests first for guardrail behavior.
4. Implement narrowly.
5. Run targeted tests.
6. Run full tests.
7. Run compile and diff check.
8. Use reviewer for broad/safety-sensitive changes.
9. Push/deploy only after local verification.
10. Verify production separately.

Commands:

```bash
PYTHONPATH=src .venv/bin/python -m unittest
.venv/bin/python -B -m compileall src
git diff --check
```

Production smoke:

```text
GET /health -> 200
GET /admin -> 200
GET /api/whatsapp/agent-tasks without auth -> 401
GET /api/whatsapp/qa-feedback/eval-cases without auth -> 401
```

## Handoff Format

When handing to the next agent, include:

- Current branch and latest commit.
- Whether worktree is clean.
- What was changed.
- Tests run and results.
- Deployment status and exact endpoints checked.
- Known risks and next action.
- Any reviewer status.

Do not include secrets, platform IDs, real parent phone numbers, or raw private transcripts.
