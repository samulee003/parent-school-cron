# WhatsApp Parent Onboarding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a short WhatsApp parent onboarding flow that asks for child age and concern before recommending courses, stores that context as memory/tags/notes, and only asks for proactive push consent after useful recommendations.

**Architecture:** Keep the existing `WhatsAppHandler` state shape and SQLite store. Tighten recommendation readiness, add small helper methods for onboarding copy/metadata/consent prompt, and extend tests around first-contact, partial answers, stored profile, and consent prompt repetition.

**Tech Stack:** Python 3, FastAPI runtime, SQLite-backed `WhatsAppMemoryStore`, `unittest`, existing deterministic extractors in `src/whatsapp_handler.py`.

---

## File Structure

- Modify `src/whatsapp_handler.py`
  - Add constants for onboarding copy.
  - Tighten `_profile_ready_for_recommendation()`.
  - Update `_onboarding_text()` copy.
  - Add helper methods for onboarding tags/notes and post-recommendation consent prompt.
  - Call metadata helper after `_update_profile_from_text()`.
  - Append consent prompt only after a recommendation and only when consent is `unknown`.
- Modify `tests/test_whatsapp_handler.py`
  - Replace older broad onboarding assertions with new wording.
  - Add tests for complete onboarding, partial onboarding, tags/notes, and consent prompt suppression.
- Optionally update `memory.md` after implementation to record the new capability.

---

### Task 1: Lock New Onboarding Prompt Behavior With Tests

**Files:**
- Modify: `tests/test_whatsapp_handler.py`

- [ ] **Step 1: Update the existing broad course query test**

Replace `test_courses_keyword_without_profile_asks_for_context` with:

```python
    def test_courses_keyword_without_profile_asks_parent_interview_question(self):
        handler, sent = self.make_handler()

        handler._handle_text_message("85360000000", "課程")

        self.assertEqual(sent[0][0], "85360000000")
        self.assertIn("小朋友幾多歲", sent[0][1])
        self.assertIn("情緒", sent[0][1])
        self.assertIn("學習", sent[0][1])
        self.assertIn("親子溝通", sent[0][1])
        self.assertIn("升學壓力", sent[0][1])
        self.assertNotIn("嬰幼繪本氹氹轉", sent[0][1])
```

- [ ] **Step 2: Add a complete onboarding answer test**

Add this test near the profile/onboarding tests:

```python
    def test_complete_onboarding_answer_stores_memory_and_adds_soft_consent_prompt(self):
        courses = [
            Course(
                id="c-health",
                name="健康情緒與青少年同行",
                date="2026/05/31 星期日 10:30-12:00",
                date_parsed=None,
                age_group="13-18歲",
                age_groups=["13-18歲"],
                topic="身心健康",
                target="家長",
                status="報名中",
                detail_url="https://example.test/course/health",
                summary="覺察青少年壓力與焦慮，學習親子衝突後真誠對話。",
                registration_url="https://example.test/register/health",
            )
        ]
        handler = WhatsAppHandler()
        handler._get_bot = lambda: type("Bot", (), {"scraper": FakeCrawler(courses)})()
        sent = []
        handler._send_text = lambda to, text: sent.append((to, text)) or True

        handler._handle_text_message("85360000000", "課程")
        handler._handle_text_message("85360000000", "13歲，最近情緒壓力大")

        profile = handler._memory.get_profile("85360000000")
        conversation = handler._memory.get_conversation("85360000000")

        self.assertEqual(profile["age_groups"], ["13-18歲"])
        self.assertIn("情緒壓力", profile["pain_points"])
        self.assertIn("健康情緒與青少年同行", sent[1][1])
        self.assertIn("https://example.test/register/health", sent[1][1])
        self.assertIn("同意推送", sent[1][1])
        self.assertIn("青少年", conversation["tags"])
        self.assertIn("情緒壓力", conversation["tags"])
        self.assertIn("onboarding:", conversation["notes"])
```

- [ ] **Step 3: Add partial answer tests**

Add:

```python
    def test_onboarding_age_only_asks_for_concern_without_calling_deepseek(self):
        handler, sent = self.make_handler()

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            with patch("whatsapp_handler.requests.post") as post:
                handler._handle_text_message("85360000000", "13歲")

        self.assertFalse(post.called)
        self.assertIn("最想處理", sent[0][1])
        self.assertIn("情緒", sent[0][1])
        self.assertNotIn("青少年親子溝通工作坊", sent[0][1])

    def test_onboarding_pain_only_asks_for_child_age_without_calling_deepseek(self):
        handler, sent = self.make_handler()

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            with patch("whatsapp_handler.requests.post") as post:
                handler._handle_text_message("85360000000", "最近情緒壓力大")

        self.assertFalse(post.called)
        self.assertIn("小朋友幾多歲", sent[0][1])
        self.assertNotIn("青少年親子溝通工作坊", sent[0][1])
```

- [ ] **Step 4: Add consent prompt suppression test**

Add:

```python
    def test_recommendation_does_not_repeat_consent_prompt_after_consent_set(self):
        handler, sent = self.make_handler()
        handler._memory.update_conversation("85360000000", consent_status="allowed")

        handler._handle_text_message("85360000000", "小朋友1歲，想親子活動")

        self.assertIn("嬰幼繪本氹氹轉", sent[0][1])
        self.assertNotIn("同意推送", sent[0][1])
```

- [ ] **Step 5: Run tests and verify expected failures**

Run:

```bash
.venv/bin/python -m unittest tests.test_whatsapp_handler
```

Expected: FAIL because current onboarding copy/readiness/metadata behavior is not implemented.

---

### Task 2: Tighten Recommendation Readiness And Onboarding Copy

**Files:**
- Modify: `src/whatsapp_handler.py`

- [ ] **Step 1: Add onboarding copy constants**

Add near the keyword constants:

```python
ONBOARDING_QUESTION = (
    "我先幫你縮窄，不直接丟一堆課程。\n\n"
    "小朋友幾多歲？最近比較想處理："
    "*情緒*、*學習*、*親子溝通*、*升學壓力*，還是其他？"
)

ONBOARDING_CONCERN_QUESTION = (
    "收到，我先記住孩子年齡。\n\n"
    "最近比較想處理哪方面？"
    "可以直接回覆：*情緒*、*學習*、*親子溝通*、*升學壓力*，或用一句話說明。"
)

ONBOARDING_AGE_QUESTION = (
    "收到，我先記住你關心的方向。\n\n"
    "小朋友幾多歲？例如：*4歲*、*小學*、*13歲*。"
)

PROACTIVE_CONSENT_PROMPT = (
    "\n\n之後如果有貼近你情況的新課程，我可以偶爾提醒你。"
    "回覆「同意推送」即可。"
)
```

- [ ] **Step 2: Replace `_profile_ready_for_recommendation()`**

Change it to:

```python
    def _profile_ready_for_recommendation(self, profile: Dict[str, Any]) -> bool:
        if not self._profile_age_groups(profile):
            return False
        return bool(
            profile.get("pain_points")
            or profile.get("target")
            or profile.get("topic")
        )
```

- [ ] **Step 3: Replace `_onboarding_text()` with missing-field copy**

Use:

```python
    def _onboarding_text(self, profile: Dict[str, Any]) -> str:
        if not self._profile_has_signal(profile):
            return ONBOARDING_QUESTION

        has_age = bool(self._profile_age_groups(profile))
        has_concern = bool(
            profile.get("pain_points")
            or profile.get("topic")
            or profile.get("target")
        )
        if has_age and not has_concern:
            return ONBOARDING_CONCERN_QUESTION
        if has_concern and not has_age:
            return ONBOARDING_AGE_QUESTION
        return ONBOARDING_QUESTION
```

- [ ] **Step 4: Run targeted tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_whatsapp_handler.WhatsAppHandlerTests.test_courses_keyword_without_profile_asks_parent_interview_question tests.test_whatsapp_handler.WhatsAppHandlerTests.test_onboarding_age_only_asks_for_concern_without_calling_deepseek tests.test_whatsapp_handler.WhatsAppHandlerTests.test_onboarding_pain_only_asks_for_child_age_without_calling_deepseek
```

Expected: these tests PASS, unless metadata/consent tests are also included by mistake.

---

### Task 3: Store Onboarding Tags And Notes

**Files:**
- Modify: `src/whatsapp_handler.py`

- [ ] **Step 1: Add helper for age labels**

Add inside `WhatsAppHandler` near `_profile_text()`:

```python
    def _profile_tag_labels(self, profile: Dict[str, Any]) -> List[str]:
        tags: List[str] = []
        for age_group in self._profile_age_groups(profile):
            label = AGE_GROUP_LABELS.get(age_group, age_group)
            short_label = label.split("（", 1)[0].strip()
            if short_label and short_label not in tags:
                tags.append(short_label)
        for pain in [str(p) for p in profile.get("pain_points", []) if p]:
            if pain not in tags:
                tags.append(pain)
        if profile.get("target") and str(profile["target"]) not in tags:
            tags.append(str(profile["target"]))
        return tags[:8]
```

- [ ] **Step 2: Add onboarding note helper**

Add:

```python
    def _onboarding_note_text(self, profile: Dict[str, Any]) -> str:
        parts: List[str] = []
        age_groups = self._profile_age_groups(profile)
        if age_groups:
            parts.append("、".join(age_groups))
        if profile.get("pain_points"):
            parts.append("、".join([str(p) for p in profile.get("pain_points", []) if p][:3]))
        elif profile.get("topic"):
            parts.append(str(profile["topic"]))
        if profile.get("target"):
            parts.append(str(profile["target"]))
        return "onboarding: " + " / ".join([p for p in parts if p])
```

- [ ] **Step 3: Add metadata sync helper**

Add:

```python
    def _sync_onboarding_conversation_meta(
        self,
        from_number: str,
        profile: Dict[str, Any],
    ) -> None:
        if not self._profile_has_signal(profile):
            return
        tags = self._profile_tag_labels(profile)
        note = self._onboarding_note_text(profile)
        kwargs: Dict[str, Any] = {}
        if tags:
            kwargs["tags"] = tags
        if note != "onboarding: ":
            kwargs["notes"] = note
        if kwargs:
            self._memory.update_conversation(from_number, **kwargs)
```

- [ ] **Step 4: Call helper after profile update**

In `_handle_text_message()`, immediately after:

```python
        profile = self._update_profile_from_text(from_number, text)
```

add:

```python
        self._sync_onboarding_conversation_meta(from_number, profile)
```

- [ ] **Step 5: Run metadata test**

Run:

```bash
.venv/bin/python -m unittest tests.test_whatsapp_handler.WhatsAppHandlerTests.test_complete_onboarding_answer_stores_memory_and_adds_soft_consent_prompt
```

Expected: still FAIL only on consent prompt if Task 4 is not done; profile/tags/notes assertions should pass.

---

### Task 4: Append Consent Prompt Only After Recommendation

**Files:**
- Modify: `src/whatsapp_handler.py`

- [ ] **Step 1: Add helper to check consent prompt eligibility**

Add inside `WhatsAppHandler`:

```python
    def _should_append_proactive_consent_prompt(self, from_number: str) -> bool:
        conversation = self._memory.get_conversation(from_number)
        return conversation.get("consent_status", "unknown") == "unknown"
```

- [ ] **Step 2: Add helper to append prompt once per recommendation response**

Add:

```python
    def _with_proactive_consent_prompt(self, from_number: str, reply: str) -> str:
        if not reply or not self._should_append_proactive_consent_prompt(from_number):
            return reply
        if "同意推送" in reply:
            return reply
        return reply + PROACTIVE_CONSENT_PROMPT
```

- [ ] **Step 3: Apply helper in `_get_agentic_recommendation_text()` only on ready recommendations**

Change the method body so onboarding replies are returned directly, while LLM/rule-based recommendations pass through `_with_proactive_consent_prompt()`:

```python
    def _get_agentic_recommendation_text(
        self,
        from_number: str,
        profile: Dict[str, Any],
        user_text: str = "",
    ) -> str:
        if not self._profile_has_signal(profile) or not self._profile_ready_for_recommendation(profile):
            return self._onboarding_text(profile)

        llm_reply = self._get_llm_recommendation_text(from_number, user_text, profile)
        if llm_reply:
            return self._with_proactive_consent_prompt(from_number, llm_reply)

        reply = self._get_courses_text(
            from_number=from_number,
            age_group=self._profile_age_groups(profile),
            target=profile.get("target", ""),
            topic=profile.get("topic", ""),
            page=1,
            agentic=True,
            profile=profile,
        )
        return self._with_proactive_consent_prompt(from_number, reply)
```

- [ ] **Step 4: Run consent tests**

Run:

```bash
.venv/bin/python -m unittest tests.test_whatsapp_handler.WhatsAppHandlerTests.test_complete_onboarding_answer_stores_memory_and_adds_soft_consent_prompt tests.test_whatsapp_handler.WhatsAppHandlerTests.test_recommendation_does_not_repeat_consent_prompt_after_consent_set
```

Expected: PASS.

---

### Task 5: Preserve Existing Behaviors And Adjust Affected Tests

**Files:**
- Modify: `tests/test_whatsapp_handler.py`
- Modify: `src/whatsapp_handler.py` only if a regression is real

- [ ] **Step 1: Run the full WhatsApp handler suite**

Run:

```bash
.venv/bin/python -m unittest tests.test_whatsapp_handler
```

Expected: PASS. If any old test fails because it expected age-only recommendation, update the assertion only if the new onboarding design intentionally changed that behavior. Do not loosen off-topic, URL allowlist, webhook, admin auth, or duplicate-message tests.

- [ ] **Step 2: Run all tests**

Run:

```bash
.venv/bin/python -m unittest
```

Expected: PASS.

- [ ] **Step 3: Run static checks**

Run:

```bash
.venv/bin/python -B -m compileall src
git diff --check
.venv/bin/python -m pip check
```

Expected:

```text
compileall succeeds
git diff --check emits no output
No broken requirements found.
```

---

### Task 6: Update Handoff Docs And Commit

**Files:**
- Modify: `agent.md`
- Modify: `memory.md`

- [ ] **Step 1: Update `agent.md` conversation rules**

In `Conversation Design Rules`, ensure the bullets include:

```markdown
- First broad requests without profile should start the short onboarding question:
  child age plus one concern area.
- Only recommend courses after the profile has at least one age group and one
  concern signal such as pain point, topic, target, or clear preference.
- Ask for proactive push consent only after a useful recommendation, not before.
```

- [ ] **Step 2: Update `memory.md` current state**

Add to Current State:

```markdown
- WhatsApp onboarding 已改成兩步訪談：先問孩子年齡和關心痛點，資料足夠才推薦少量課程；推薦後才柔性提示「同意推送」。
```

- [ ] **Step 3: Commit**

Run:

```bash
git status --short
git add src/whatsapp_handler.py tests/test_whatsapp_handler.py agent.md memory.md
git commit -m "feat: add whatsapp parent onboarding"
```

Expected: commit succeeds.

---

### Task 7: Deploy And Verify Live

**Files:**
- No code changes expected.

- [ ] **Step 1: Push commit**

Run:

```bash
git push origin main
```

Expected: push succeeds.

- [ ] **Step 2: Direct deploy to existing Zeabur service**

Use the existing project/service IDs from current operational context. Do not print secrets.

Run:

```bash
npx zeabur@latest deploy --project-id 6a0d3e3433d1a635fa37e4c5 --service-id 6a0d3e4433d1a635fa37e4c9 --json
```

Expected: JSON response has `"status": "success"`.

- [ ] **Step 3: Poll health and admin**

Run:

```bash
curl -fsS --max-time 10 https://parent-school-bot.zeabur.app/health
curl -sS -o /tmp/ps_admin.out -w "%{http_code}" --max-time 10 https://parent-school-bot.zeabur.app/admin
```

Expected:

```text
/health returns JSON with "status":"healthy"
/admin returns 200 login page
```

- [ ] **Step 4: Optional live WhatsApp smoke**

If the operator has a real test phone available, send:

```text
課程
```

Expected: onboarding question, not full course list.

Then send:

```text
13歲，最近情緒壓力大
```

Expected: small relevant recommendation with official link and one soft `同意推送` prompt.

---

## Self-Review

- Spec coverage: first broad query, complete answer, partial answers, memory/tags/notes, consent prompt timing, and DeepSeek cost control are covered by Tasks 1-4.
- No placeholders: all tasks include exact files, commands, and code snippets.
- Type consistency: helper methods use existing `Dict[str, Any]`, `List[str]`, `WhatsAppMemoryStore.update_conversation()`, and existing `WhatsAppHandler` profile helpers.
