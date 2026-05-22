# WhatsApp Harness Engineering Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the WhatsApp parent-school bot from "LLM sometimes helps" into a bounded harness where ASR, local rules, LLM extraction, course retrieval, memory, recommendation, admin handoff, and evals each have clear contracts.

**Architecture:** Keep FastAPI, SQLite, `WhatsAppHandler`, `WhatsAppMemoryStore`, and the current `/admin` console. Add a small harness layer around the existing handler: local rules decide cheap/safe cases first, LLM is only called for in-domain ambiguous profile extraction or bounded recommendation, and every decision leaves a testable trace for admin review and future evals.

**Tech Stack:** Python 3, FastAPI, SQLite, WhatsApp Cloud API, StepFun ASR, DeepSeek chat completion, `unittest`, existing DSEDJ scraper in `src/scraper.py`.

---

## Harness Engineering Definition

在這個專案，harness engineering 不是「加一個更聰明的 prompt」。它是把模型包在一整套可控工程系統裡：

```text
WhatsApp text / audio
  -> webhook ingestion + duplicate protection
  -> audio transcription when needed
  -> input normalization
  -> local intent and safety gate
  -> deterministic profile extraction
  -> LLM JSON extraction only for in-domain ambiguous messages
  -> profile memory merge
  -> DSEDJ course fetch + detail summary + registration URL
  -> candidate ranking
  -> LLM bounded recommendation only from candidates
  -> post-checks: URLs, length, off-topic, uncertainty
  -> transcript, flags, admin handoff, eval feedback
```

The harness exists to make these promises true:

- LLM never invents courses, dates, registration status, links, or quota state.
- LLM never answers restaurant, weather, investment, homework, translation, coding, or generic chat requests.
- Local rules handle commands, obvious ages, obvious topics, pagination, consent, reset, and hard off-topic cases before any token is spent.
- LLM handles only natural in-domain ambiguity, such as `8 and 6`, `大仔小學細仔幼稚園`, `成日爆喊`, `升中好焦慮`, or ASR text with mixed Mandarin/Cantonese.
- If the profile is incomplete, the bot asks one short missing-field question instead of dumping courses.
- If confidence is low, the bot creates an admin flag or asks a clarification question instead of pretending to know.
- Every change is backed by golden tests, and every production miss can become an eval case.

---

## Current State

Already implemented:

- WhatsApp Cloud API webhook and duplicate protection in `src/api_server.py` and `src/whatsapp_handler.py`.
- StepFun ASR for WhatsApp voice notes, with OpenAI transcription fallback.
- Deterministic parsing for commands, Arabic-number ages, Chinese-number ages, topics, targets, and pain points.
- LLM semantic profile extraction for in-domain ambiguous text.
- DeepSeek recommendation from DSEDJ candidate courses.
- Per-user and global LLM quota plus response cache in `src/whatsapp_memory.py`.
- DSEDJ course scraping, detail summary extraction, registration link extraction, and WhatsApp-safe URL normalization in `src/scraper.py`.
- Agent Inbox basics: transcript, structured profile, flags, drafts, human takeover, admin replies, QA feedback.

Current weakness:

- Harness decisions are still spread through a large `src/whatsapp_handler.py`.
- The code has tests, but no single golden eval suite that says what the agent must do for the most important parent phrases.
- LLM extraction and local extraction are not represented by one explicit decision object.
- Admin can see messages and profile, but not yet a clean "why did the agent route this way?" trace.
- Production misses are saved as QA feedback, but not yet converted into repeatable regression tests.

---

## Local Rule vs LLM Policy

Use local rules for:

- Exact commands: `課程`, `全部課程`, `更多`, `下一頁`, `報名`, `重設`, `同意推送`, `停止推送`.
- Greetings and help menu: `hi`, `你好`, `help`, `使用方法`.
- Obvious ages: `8歲`, `八歲`, `十三歲`, `小學`, `幼稚園`, `中學`, `13-18歲`.
- Obvious concern keywords: `情緒`, `壓力`, `親子`, `學習`, `升學`, `溝通`, `繪本`, `感統`.
- Hard off-topic: restaurant, weather, stock, investment, homework, translation, code, celebrity, news, medical diagnosis, legal advice.
- Pagination, profile reset, proactive consent, admin takeover, and message dedupe.

Use LLM profile extraction for:

- Short in-domain natural language that local rules cannot fully parse.
- Mixed language age phrases: `8 and 6`, `one kid is 4 and one is 12`.
- Family structure phrases: `大仔中學，細仔幼稚園`.
- Pain descriptions without exact keywords: `成日爆喊`, `好難坐定`, `青春期好難溝通`, `升中好焦慮`.
- ASR transcript that is short, parent-school related, and not already solved locally.

Use LLM recommendation for:

- Profile is ready: at least one age group plus one concern signal.
- Candidate courses were fetched from DSEDJ.
- Candidate payload includes only official course fields: id, title, date, age group, target, topic, summary, registration URL, detail URL.
- The reply can only reference candidate course IDs and URLs provided in the prompt.

Never use LLM for:

- Off-topic questions.
- Long noisy messages that do not look like a parent-school course request.
- Secrets, admin operations, tokens, PINs, platform settings, or deployment advice.
- Building course URLs.
- Scraping or deciding the official course truth.
- Replying while the conversation is in human takeover.

---

## File Structure

- Create `docs/harness_engineering.md`
  - Human-readable product and engineering contract for the WhatsApp harness.
  - Defines routing policy, model boundaries, data contracts, eval policy, and admin loop.
- Create `src/whatsapp_nlu.py`
  - Owns input normalization, local command detection, off-topic detection, deterministic age/topic/pain extraction, and route preconditions.
- Create `src/whatsapp_harness.py`
  - Owns `HarnessDecision`, `ProfilePatch`, and orchestration helpers that decide local vs LLM vs recommend vs ask-missing vs handoff.
- Modify `src/whatsapp_handler.py`
  - Keep WhatsApp API behavior, message sending, ASR integration, course formatting, and handler entry points.
  - Delegate NLU and decision-making to `src/whatsapp_nlu.py` and `src/whatsapp_harness.py`.
  - Keep DeepSeek as an enhancer, not a source of truth.
- Modify `src/whatsapp_memory.py`
  - Add a lightweight harness trace stored in existing message metadata or flags.
  - Add helper methods to fetch unresolved QA misses for eval generation.
- Modify `src/api_server.py`
  - Show harness state and last route in `/admin`.
  - Add admin API for exporting QA feedback as eval fixture JSON without private phone numbers.
- Create `tests/test_whatsapp_nlu.py`
  - Tests cheap local parsing and off-topic behavior without WhatsApp side effects.
- Create `tests/test_whatsapp_harness.py`
  - Tests local vs LLM routing, profile patch merge, uncertainty, and human takeover behavior.
- Create `tests/test_whatsapp_harness_eval.py`
  - Golden parent phrases and expected behavior.
- Create `tests/fixtures/whatsapp_harness_cases.json`
  - Portable regression cases from product QA and friend testing.

---

### Task 1: Write The Harness Contract Doc

**Files:**
- Create: `docs/harness_engineering.md`

- [ ] **Step 1: Add the contract document**

Create `docs/harness_engineering.md` with this content:

```markdown
# WhatsApp Harness Engineering

This bot is a bounded course assistant for Macau Parent School. The model is useful only inside a harness that controls source of truth, token usage, safety, memory, and handoff.

## Source Of Truth

- Courses, dates, registration status, summaries, and links come from DSEDJ pages through `src/scraper.py`.
- LLM output may rank, summarize, and explain candidates, but it cannot create or modify official course facts.
- Every parent-facing course reply must include an official detail or registration URL already present in the candidate payload.

## Routing Order

1. Ingest WhatsApp event and dedupe message IDs.
2. If media is audio, transcribe it with StepFun first.
3. Normalize text: whitespace, punctuation, simplified/traditional variants, Cantonese wording, Chinese numerals, and common English age phrases.
4. Run local command and safety gate.
5. Run deterministic extraction for age, topic, target, and pain points.
6. If still ambiguous but in-domain, call LLM JSON extraction.
7. Merge the profile patch into `profile_json`.
8. If profile lacks age or concern, ask one missing-field question.
9. If profile is ready, fetch DSEDJ candidates and rank them.
10. If candidates exist, use bounded LLM recommendation or deterministic fallback.
11. Post-check reply for official URLs, length, and off-topic leakage.
12. Persist transcript, harness route, flags, and admin-visible state.

## Model Boundaries

The LLM can:

- Convert short in-domain parent text into structured profile JSON.
- Select and explain from provided course candidates.
- Produce concise parent-facing Cantonese/Traditional Chinese copy.

The LLM cannot:

- Answer general knowledge questions.
- Invent course facts.
- Build links.
- See secrets.
- Override admin takeover.
- Ignore quota.

## Fail Closed Rules

- Off-topic messages get a local refusal and no LLM call.
- Low-confidence in-domain messages ask one clarification question.
- ASR failures create a handoff flag and ask the parent to send text.
- No candidate courses produces a no-match reply and a flag.
- Human takeover stores inbound messages only and does not trigger AI.

## Eval Policy

Every fixed production miss becomes a regression case with:

- input text or ASR transcript
- existing profile
- expected route
- expected profile patch
- expected reply intent
- expected LLM call count

The golden suite must run in `python -m unittest` without external network calls.
```

- [ ] **Step 2: Commit the doc**

```bash
git add docs/harness_engineering.md
git commit -m "docs: define whatsapp harness contract"
```

Expected: commit succeeds with only documentation changes.

---

### Task 2: Extract Local NLU Into A Focused Module

**Files:**
- Create: `src/whatsapp_nlu.py`
- Modify: `src/whatsapp_handler.py`
- Test: `tests/test_whatsapp_nlu.py`

- [ ] **Step 1: Write failing NLU tests**

Create `tests/test_whatsapp_nlu.py`:

```python
import unittest

from whatsapp_nlu import (
    detect_child_age_groups,
    detect_local_intent,
    extract_local_profile_patch,
    is_hard_off_topic,
    normalize_parent_text,
)


class WhatsAppNluTests(unittest.TestCase):
    def test_chinese_and_english_age_phrases_are_normalized(self):
        self.assertEqual(detect_child_age_groups("八歲，情緒"), ["7-12歲"])
        self.assertEqual(detect_child_age_groups("十三歲想搵情緒課"), ["13-18歲"])
        self.assertEqual(detect_child_age_groups("8 and 6"), ["3-6歲", "7-12歲"])

    def test_family_structure_age_phrases_are_detected(self):
        self.assertEqual(
            detect_child_age_groups("大仔中學，細仔幼稚園"),
            ["3-6歲", "13-18歲"],
        )

    def test_local_profile_patch_extracts_obvious_concern(self):
        patch = extract_local_profile_patch("八歲，最近情緒壓力大")

        self.assertEqual(patch["age_groups"], ["7-12歲"])
        self.assertIn("情緒壓力", patch["pain_points"])

    def test_hard_off_topic_is_rejected_locally(self):
        self.assertTrue(is_hard_off_topic("推薦餐廳"))
        self.assertTrue(is_hard_off_topic("幫我寫 Python code"))
        self.assertFalse(is_hard_off_topic("青少年家長課"))

    def test_exact_commands_are_local_intents(self):
        self.assertEqual(detect_local_intent("更多"), "next_page")
        self.assertEqual(detect_local_intent("全部課程"), "all_courses")
        self.assertEqual(detect_local_intent("重設"), "reset")

    def test_normalize_parent_text_keeps_parent_meaning(self):
        self.assertEqual(
            normalize_parent_text("  小朋友８歲，想搵 情緒  "),
            "小朋友8歲，想搵 情緒",
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
.venv/bin/python -m unittest tests.test_whatsapp_nlu
```

Expected: FAIL with `ModuleNotFoundError: No module named 'whatsapp_nlu'`.

- [ ] **Step 3: Create the NLU module**

Create `src/whatsapp_nlu.py`:

```python
from __future__ import annotations

import re
import unicodedata
from typing import Any, Dict, List, Optional

AGE_GROUP_OPTIONS = ["0-2歲", "3-6歲", "7-12歲", "13-18歲"]

CHINESE_NUMERAL_VALUES = {
    "零": 0,
    "〇": 0,
    "一": 1,
    "二": 2,
    "兩": 2,
    "俩": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}

HARD_OFF_TOPIC_KEYWORDS = [
    "餐廳",
    "美食",
    "天氣",
    "股票",
    "投資",
    "功課答案",
    "翻譯",
    "寫 code",
    "寫code",
    "python",
    "javascript",
    "電影",
    "新聞",
]

LOCAL_COMMANDS = {
    "更多": "next_page",
    "下一頁": "next_page",
    "全部": "all_courses",
    "全部課程": "all_courses",
    "課程": "courses",
    "報名": "registration",
    "重設": "reset",
    "重新開始": "reset",
    "同意推送": "consent_allow",
    "停止推送": "consent_stop",
}

PAIN_POINT_KEYWORDS = {
    "情緒壓力": ["情緒", "壓力", "焦慮", "爆喊", "發脾氣", "青春期"],
    "親子溝通": ["親子", "溝通", "衝突", "傾偈", "頂嘴"],
    "學習動機": ["學習", "功課", "專注", "坐定", "讀書"],
    "環境適應": ["升學", "升中", "幼升小", "小一", "適應"],
}


def normalize_parent_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text or "")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return normalized


def parse_chinese_number(value: str) -> Optional[int]:
    value = value.strip()
    if not value:
        return None
    if value.isdigit():
        return int(value)
    if value in CHINESE_NUMERAL_VALUES:
        return CHINESE_NUMERAL_VALUES[value]
    if "十" in value:
        left, _, right = value.partition("十")
        tens = CHINESE_NUMERAL_VALUES.get(left, 1) if left else 1
        ones = CHINESE_NUMERAL_VALUES.get(right, 0) if right else 0
        return tens * 10 + ones
    return None


def age_to_group(age: int) -> Optional[str]:
    if age < 0:
        return None
    if age < 3:
        return "0-2歲"
    if age <= 6:
        return "3-6歲"
    if age <= 12:
        return "7-12歲"
    if age <= 18:
        return "13-18歲"
    return None


def _append_unique(values: List[str], value: Optional[str]) -> None:
    if value and value not in values:
        values.append(value)


def detect_child_age_groups(text: str) -> List[str]:
    text = normalize_parent_text(text)
    groups: List[str] = []
    for match in re.finditer(r"(\d{1,2})\s*(?:歲|years? old|yo)?", text, re.I):
        _append_unique(groups, age_to_group(int(match.group(1))))
    for match in re.finditer(r"([零〇一二兩俩三四五六七八九十]{1,3})\s*歲", text):
        parsed = parse_chinese_number(match.group(1))
        if parsed is not None:
            _append_unique(groups, age_to_group(parsed))
    lower = text.lower()
    if "幼稚園" in text or "幼兒" in text:
        _append_unique(groups, "3-6歲")
    if "小學" in text or "小朋友" in text and any(k in text for k in ["小一", "小二", "小三", "小四", "小五", "小六"]):
        _append_unique(groups, "7-12歲")
    if "中學" in text or "青少年" in text or "teen" in lower:
        _append_unique(groups, "13-18歲")
    return groups


def detect_pain_points(text: str) -> List[str]:
    text = normalize_parent_text(text)
    points: List[str] = []
    for label, keywords in PAIN_POINT_KEYWORDS.items():
        if any(keyword in text for keyword in keywords):
            points.append(label)
    return points


def detect_local_intent(text: str) -> Optional[str]:
    normalized = normalize_parent_text(text)
    return LOCAL_COMMANDS.get(normalized)


def is_hard_off_topic(text: str) -> bool:
    normalized = normalize_parent_text(text).lower()
    return any(keyword.lower() in normalized for keyword in HARD_OFF_TOPIC_KEYWORDS)


def extract_local_profile_patch(text: str) -> Dict[str, Any]:
    return {
        "age_groups": detect_child_age_groups(text),
        "pain_points": detect_pain_points(text),
    }
```

- [ ] **Step 4: Run NLU tests**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_whatsapp_nlu
```

Expected: PASS.

- [ ] **Step 5: Move existing handler helpers to use NLU module**

Modify `src/whatsapp_handler.py` imports:

```python
from whatsapp_nlu import (
    detect_child_age_groups,
    detect_local_intent,
    extract_local_profile_patch,
    is_hard_off_topic,
    normalize_parent_text,
    parse_chinese_number,
)
```

Keep compatibility wrappers only where existing tests import helper functions from `whatsapp_handler.py`.

- [ ] **Step 6: Run existing handler tests**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_whatsapp_handler
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add src/whatsapp_nlu.py src/whatsapp_handler.py tests/test_whatsapp_nlu.py
git commit -m "refactor: extract whatsapp local nlu"
```

Expected: commit succeeds.

---

### Task 3: Add Explicit Harness Decisions

**Files:**
- Create: `src/whatsapp_harness.py`
- Test: `tests/test_whatsapp_harness.py`

- [ ] **Step 1: Write failing harness route tests**

Create `tests/test_whatsapp_harness.py`:

```python
import unittest

from whatsapp_harness import decide_message_route


class WhatsAppHarnessTests(unittest.TestCase):
    def test_off_topic_routes_without_llm(self):
        decision = decide_message_route("推薦餐廳", profile={})

        self.assertEqual(decision["route"], "off_topic")
        self.assertFalse(decision["allow_llm"])
        self.assertEqual(decision["recommended_action"], "local_refusal")

    def test_exact_next_page_command_routes_locally(self):
        decision = decide_message_route("更多", profile={"age_groups": ["7-12歲"]})

        self.assertEqual(decision["route"], "local_command")
        self.assertEqual(decision["intent"], "next_page")
        self.assertFalse(decision["allow_llm"])

    def test_obvious_profile_patch_routes_locally(self):
        decision = decide_message_route("八歲，情緒", profile={})

        self.assertEqual(decision["route"], "local_profile_update")
        self.assertEqual(decision["profile_patch"]["age_groups"], ["7-12歲"])
        self.assertIn("情緒壓力", decision["profile_patch"]["pain_points"])

    def test_short_ambiguous_in_domain_routes_to_llm_extraction(self):
        decision = decide_message_route("8 and 6", profile={"pain_points": ["親子溝通"]})

        self.assertEqual(decision["route"], "llm_profile_extraction")
        self.assertTrue(decision["allow_llm"])
        self.assertEqual(decision["llm_purpose"], "profile_extraction")

    def test_ready_profile_routes_to_recommendation(self):
        decision = decide_message_route(
            "幫我揀",
            profile={"age_groups": ["13-18歲"], "pain_points": ["情緒壓力"]},
        )

        self.assertEqual(decision["route"], "recommend_courses")
        self.assertTrue(decision["profile_ready"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_whatsapp_harness
```

Expected: FAIL with `ModuleNotFoundError: No module named 'whatsapp_harness'`.

- [ ] **Step 3: Add harness decision module**

Create `src/whatsapp_harness.py`:

```python
from __future__ import annotations

from typing import Any, Dict, List

from whatsapp_nlu import (
    detect_local_intent,
    extract_local_profile_patch,
    is_hard_off_topic,
    normalize_parent_text,
)

RECOMMENDATION_INTENTS = {"推薦", "幫我揀", "幫我選", "搵課程", "找課程"}


def _has_profile_signal(profile: Dict[str, Any]) -> bool:
    return bool(
        profile.get("age_groups")
        or profile.get("pain_points")
        or profile.get("topic")
        or profile.get("target")
    )


def _profile_ready(profile: Dict[str, Any]) -> bool:
    return bool(profile.get("age_groups")) and bool(
        profile.get("pain_points") or profile.get("topic") or profile.get("target")
    )


def _has_patch_signal(patch: Dict[str, List[str]]) -> bool:
    return bool(patch.get("age_groups") or patch.get("pain_points"))


def _looks_in_domain(text: str, profile: Dict[str, Any]) -> bool:
    if _has_profile_signal(profile):
        return True
    keywords = ["小朋友", "孩子", "家長", "課程", "親子", "情緒", "升學", "學習", "青少年"]
    return any(keyword in text for keyword in keywords)


def _is_short_enough_for_profile_llm(text: str) -> bool:
    return 1 <= len(text) <= 180


def decide_message_route(text: str, profile: Dict[str, Any]) -> Dict[str, Any]:
    normalized = normalize_parent_text(text)
    intent = detect_local_intent(normalized)
    ready = _profile_ready(profile)

    if intent:
        if intent in {"courses"} and ready:
            return {
                "route": "recommend_courses",
                "intent": intent,
                "allow_llm": True,
                "llm_purpose": "bounded_recommendation",
                "profile_ready": ready,
                "recommended_action": "recommend",
            }
        return {
            "route": "local_command",
            "intent": intent,
            "allow_llm": False,
            "profile_ready": ready,
            "recommended_action": "handle_command",
        }

    if is_hard_off_topic(normalized):
        return {
            "route": "off_topic",
            "intent": "",
            "allow_llm": False,
            "profile_ready": ready,
            "recommended_action": "local_refusal",
        }

    patch = extract_local_profile_patch(normalized)
    if _has_patch_signal(patch):
        return {
            "route": "local_profile_update",
            "intent": "",
            "allow_llm": False,
            "profile_patch": patch,
            "profile_ready": ready,
            "recommended_action": "merge_profile_then_continue",
        }

    if normalized in RECOMMENDATION_INTENTS and ready:
        return {
            "route": "recommend_courses",
            "intent": "recommend",
            "allow_llm": True,
            "llm_purpose": "bounded_recommendation",
            "profile_ready": ready,
            "recommended_action": "recommend",
        }

    if _is_short_enough_for_profile_llm(normalized) and _looks_in_domain(normalized, profile):
        return {
            "route": "llm_profile_extraction",
            "intent": "",
            "allow_llm": True,
            "llm_purpose": "profile_extraction",
            "profile_ready": ready,
            "recommended_action": "extract_profile",
        }

    return {
        "route": "unknown",
        "intent": "",
        "allow_llm": False,
        "profile_ready": ready,
        "recommended_action": "ask_for_supported_query",
    }
```

- [ ] **Step 4: Run harness tests**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_whatsapp_harness
```

Expected: PASS.

- [ ] **Step 5: Run full tests before integration**

This task only creates the decision module and its tests. Handler integration happens in Task 4 after the trace storage helper exists.

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/whatsapp_harness.py tests/test_whatsapp_harness.py
git commit -m "feat: add whatsapp harness decisions"
```

Expected: commit succeeds.

---

### Task 4: Store Harness Trace For Admin And Debugging

**Files:**
- Modify: `src/whatsapp_memory.py`
- Modify: `src/api_server.py`
- Modify: `src/whatsapp_handler.py`
- Test: `tests/test_whatsapp_handler.py`

- [ ] **Step 1: Add failing trace persistence test**

Add to `tests/test_whatsapp_handler.py`:

```python
    def test_harness_trace_is_saved_for_admin_detail(self):
        handler, sent = self.make_handler()

        handler._handle_text_message("85360000000", "推薦餐廳")

        conversation = handler._memory.get_conversation("85360000000")
        self.assertEqual(conversation["last_harness_route"], "off_topic")
        self.assertEqual(conversation["last_harness_action"], "local_refusal")
        self.assertFalse(conversation["last_harness_allow_llm"])
```

- [ ] **Step 2: Add trace columns to existing conversation table**

In `src/whatsapp_memory.py`, extend the conversation schema migration with columns:

```python
("last_harness_route", "TEXT NOT NULL DEFAULT ''"),
("last_harness_intent", "TEXT NOT NULL DEFAULT ''"),
("last_harness_action", "TEXT NOT NULL DEFAULT ''"),
("last_harness_allow_llm", "INTEGER NOT NULL DEFAULT 0"),
("last_harness_at", "TEXT NOT NULL DEFAULT ''"),
```

- [ ] **Step 3: Add memory helper**

Add to `WhatsAppMemoryStore`:

```python
    def record_harness_trace(
        self,
        phone: str,
        *,
        route: str,
        intent: str = "",
        recommended_action: str = "",
        allow_llm: bool = False,
    ) -> None:
        self.ensure_conversation(phone)
        now = datetime.utcnow().isoformat()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE whatsapp_conversations
                SET last_harness_route = ?,
                    last_harness_intent = ?,
                    last_harness_action = ?,
                    last_harness_allow_llm = ?,
                    last_harness_at = ?,
                    updated_at = ?
                WHERE phone = ?
                """,
                (
                    route,
                    intent,
                    recommended_action,
                    1 if allow_llm else 0,
                    now,
                    now,
                    phone,
                ),
            )
```

- [ ] **Step 4: Integrate decision trace into the handler**

Modify the top of `src/whatsapp_handler.py`:

```python
from whatsapp_harness import decide_message_route
```

Inside `_handle_text_message`, immediately after `profile = self._memory.get_profile(from_number)`, add:

```python
decision = decide_message_route(text, profile)
self._memory.record_harness_trace(
    from_number,
    route=decision.get("route", "unknown"),
    intent=decision.get("intent", ""),
    recommended_action=decision.get("recommended_action", ""),
    allow_llm=bool(decision.get("allow_llm")),
)
```

This first integration only records the route. It does not replace the existing reply behavior yet, so old WhatsApp behavior remains protected by existing tests.

- [ ] **Step 5: Surface trace in admin state**

Modify `_build_agent_state()` in `src/api_server.py` to include:

```python
"last_harness_route": conversation.get("last_harness_route", ""),
"last_harness_intent": conversation.get("last_harness_intent", ""),
"last_harness_action": conversation.get("last_harness_action", ""),
"last_harness_allow_llm": bool(conversation.get("last_harness_allow_llm", 0)),
"last_harness_at": conversation.get("last_harness_at", ""),
```

- [ ] **Step 6: Render trace in `/admin` right panel**

Add a compact row near Agent State:

```html
<div class="state-line">Harness: <span id="harnessRoute">--</span></div>
```

In the dashboard JS detail render:

```javascript
document.getElementById("harnessRoute").textContent =
  [state.last_harness_route, state.last_harness_action].filter(Boolean).join(" / ") || "--";
```

- [ ] **Step 7: Run trace tests**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_whatsapp_handler
```

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add src/whatsapp_memory.py src/api_server.py src/whatsapp_handler.py tests/test_whatsapp_handler.py
git commit -m "feat: show whatsapp harness trace"
```

Expected: commit succeeds.

---

### Task 5: Harden LLM JSON Extraction Contract

**Files:**
- Modify: `src/whatsapp_handler.py`
- Test: `tests/test_whatsapp_handler.py`

- [ ] **Step 1: Add tests for strict extraction**

Add these tests:

```python
    def test_llm_profile_extraction_rejects_unknown_values(self):
        handler, sent = self.make_handler()
        fake_response = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"in_domain": true, "age_groups": ["99歲"], '
                            '"pain_points": ["火星移民"], "topic": "不存在", '
                            '"target": "外星人", "confidence": 0.9}'
                        )
                    }
                }
            ]
        }

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            with patch("whatsapp_handler.requests.post") as post:
                post.return_value.status_code = 200
                post.return_value.json.return_value = fake_response
                handler._handle_text_message("85360000000", "小朋友有點難搞")

        profile = handler._memory.get_profile("85360000000")
        self.assertNotIn("99歲", profile.get("age_groups", []))
        self.assertNotIn("火星移民", profile.get("pain_points", []))
        self.assertNotEqual(profile.get("topic"), "不存在")
        self.assertNotEqual(profile.get("target"), "外星人")

    def test_llm_profile_extraction_low_confidence_asks_clarifying_question(self):
        handler, sent = self.make_handler()
        fake_response = {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"in_domain": true, "age_groups": [], '
                            '"pain_points": [], "confidence": 0.2, '
                            '"clarifying_question": "小朋友幾多歲？"}'
                        )
                    }
                }
            ]
        }

        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "test-key"}, clear=False):
            with patch("whatsapp_handler.requests.post") as post:
                post.return_value.status_code = 200
                post.return_value.json.return_value = fake_response
                handler._handle_text_message("85360000000", "佢最近好麻煩")

        self.assertIn("小朋友", sent[-1][1])
```

- [ ] **Step 2: Normalize the LLM extraction schema**

Ensure the prompt asks for exactly:

```json
{
  "in_domain": true,
  "age_groups": ["0-2歲"],
  "pain_points": ["情緒壓力"],
  "topic": "身心健康",
  "target": "家長",
  "pain_summary": "短句",
  "confidence": 0.8,
  "clarifying_question": ""
}
```

Allowed values:

```python
ALLOWED_LLM_AGE_GROUPS = {"0-2歲", "3-6歲", "7-12歲", "13-18歲"}
ALLOWED_LLM_PAIN_POINTS = {"情緒壓力", "親子溝通", "學習動機", "環境適應", "社交人際", "生活照顧", "科技使用"}
ALLOWED_LLM_TOPICS = set(TOPIC_OPTIONS)
ALLOWED_LLM_TARGETS = set(TARGET_OPTIONS)
```

- [ ] **Step 3: Filter LLM output through allowlists**

In `_update_profile_from_llm_text()`, after JSON parse:

```python
age_groups = [
    value for value in data.get("age_groups", [])
    if value in ALLOWED_LLM_AGE_GROUPS
]
pain_points = [
    value for value in data.get("pain_points", [])
    if value in ALLOWED_LLM_PAIN_POINTS
]
topic = data.get("topic") if data.get("topic") in ALLOWED_LLM_TOPICS else ""
target = data.get("target") if data.get("target") in ALLOWED_LLM_TARGETS else ""
confidence = float(data.get("confidence") or 0)
```

- [ ] **Step 4: Add low-confidence behavior**

If `confidence < 0.45` and no valid patch fields exist, send a clarification question and do not recommend:

```python
question = data.get("clarifying_question") or self._onboarding_text(profile)
self._send_text(from_number, question)
return
```

- [ ] **Step 5: Run extraction tests**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_whatsapp_handler
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/whatsapp_handler.py tests/test_whatsapp_handler.py
git commit -m "fix: harden whatsapp llm profile extraction"
```

Expected: commit succeeds.

---

### Task 6: Build Golden Harness Eval Suite

**Files:**
- Create: `tests/fixtures/whatsapp_harness_cases.json`
- Create: `tests/test_whatsapp_harness_eval.py`

- [ ] **Step 1: Create eval fixture**

Create `tests/fixtures/whatsapp_harness_cases.json`:

```json
[
  {
    "name": "chinese_age_and_emotion",
    "input": "八歲，情緒",
    "profile": {},
    "expected_route": "local_profile_update",
    "expected_age_groups": ["7-12歲"],
    "expected_pain_points": ["情緒壓力"],
    "expected_allow_llm": false
  },
  {
    "name": "english_two_ages_with_existing_concern",
    "input": "8 and 6",
    "profile": {"pain_points": ["親子溝通"]},
    "expected_route": "llm_profile_extraction",
    "expected_allow_llm": true
  },
  {
    "name": "hard_off_topic_restaurant",
    "input": "推薦餐廳",
    "profile": {},
    "expected_route": "off_topic",
    "expected_allow_llm": false
  },
  {
    "name": "next_page_command",
    "input": "還有嗎",
    "profile": {"age_groups": ["13-18歲"], "pain_points": ["情緒壓力"]},
    "expected_route": "local_command",
    "expected_allow_llm": false
  },
  {
    "name": "ready_profile_recommend",
    "input": "幫我揀",
    "profile": {"age_groups": ["13-18歲"], "pain_points": ["情緒壓力"]},
    "expected_route": "recommend_courses",
    "expected_allow_llm": true
  },
  {
    "name": "voice_transcript_parent_school",
    "input": "小朋友十三歲想搵情緒壓力課",
    "profile": {},
    "expected_route": "local_profile_update",
    "expected_age_groups": ["13-18歲"],
    "expected_pain_points": ["情緒壓力"],
    "expected_allow_llm": false
  }
]
```

- [ ] **Step 2: Create eval test**

Create `tests/test_whatsapp_harness_eval.py`:

```python
import json
import pathlib
import unittest

from whatsapp_harness import decide_message_route


FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "whatsapp_harness_cases.json"


class WhatsAppHarnessEvalTests(unittest.TestCase):
    def test_golden_harness_routes(self):
        cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
        failures = []

        for case in cases:
            decision = decide_message_route(case["input"], case.get("profile", {}))
            if decision.get("route") != case["expected_route"]:
                failures.append(
                    f"{case['name']}: route {decision.get('route')} != {case['expected_route']}"
                )
            if bool(decision.get("allow_llm")) != bool(case["expected_allow_llm"]):
                failures.append(
                    f"{case['name']}: allow_llm {decision.get('allow_llm')} != {case['expected_allow_llm']}"
                )
            patch = decision.get("profile_patch", {})
            if "expected_age_groups" in case and patch.get("age_groups") != case["expected_age_groups"]:
                failures.append(
                    f"{case['name']}: age_groups {patch.get('age_groups')} != {case['expected_age_groups']}"
                )
            if "expected_pain_points" in case and patch.get("pain_points") != case["expected_pain_points"]:
                failures.append(
                    f"{case['name']}: pain_points {patch.get('pain_points')} != {case['expected_pain_points']}"
                )

        self.assertEqual([], failures)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run eval suite**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_whatsapp_harness_eval
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/whatsapp_harness_cases.json tests/test_whatsapp_harness_eval.py
git commit -m "test: add whatsapp harness golden evals"
```

Expected: commit succeeds.

---

### Task 7: Convert Admin QA Feedback Into Eval Cases

**Files:**
- Modify: `src/whatsapp_memory.py`
- Modify: `src/api_server.py`
- Test: `tests/test_whatsapp_handler.py`

- [ ] **Step 1: Add admin export API test**

Add to `tests/test_whatsapp_handler.py`:

```python
    def test_admin_can_export_qa_feedback_as_private_scrubbed_eval_cases(self):
        import api_server

        store = api_server.get_whatsapp_store()
        store.record_message("85360000000", "parent", "八歲，情緒", source="parent")
        store.record_qa_feedback(
            "85360000000",
            issue_type="missed_profile",
            severity="medium",
            summary="八歲情緒未能識別",
            admin_note="應抽取 7-12歲 和 情緒壓力",
            source_message_id=None,
        )

        with patch.dict(os.environ, {"ADMIN_SECRET": "secret"}, clear=False):
            request = DummyRequest(headers={"authorization": "Bearer secret"})
            result = asyncio.run(api_server.api_whatsapp_qa_feedback_eval_cases(request))

        self.assertEqual(result["cases"][0]["issue_type"], "missed_profile")
        self.assertNotIn("85360000000", json.dumps(result, ensure_ascii=False))
        self.assertIn("八歲情緒未能識別", result["cases"][0]["summary"])
```

- [ ] **Step 2: Add memory helper**

In `WhatsAppMemoryStore`, add:

```python
    def list_qa_feedback_for_eval(self, limit: int = 100) -> List[Dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT id, phone, issue_type, severity, summary, admin_note, status, created_at
                FROM whatsapp_qa_feedback
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [dict(row) for row in rows]
```

- [ ] **Step 3: Add admin endpoint**

In `src/api_server.py`, add:

```python
@app.get("/api/whatsapp/qa-feedback/eval-cases")
async def api_whatsapp_qa_feedback_eval_cases(request: Request, limit: int = 100):
    require_admin_request(request)
    store = get_whatsapp_store()
    cases = []
    for row in store.list_qa_feedback_for_eval(limit=min(limit, 200)):
        cases.append(
            {
                "source": "admin_qa_feedback",
                "issue_type": row.get("issue_type", ""),
                "severity": row.get("severity", ""),
                "summary": _scrub_private_text(row.get("summary", "")),
                "admin_note": _scrub_private_text(row.get("admin_note", "")),
                "status": row.get("status", ""),
                "created_at": row.get("created_at", ""),
            }
        )
    return {"total": len(cases), "cases": cases}
```

- [ ] **Step 4: Run API tests**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest tests.test_whatsapp_handler
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/whatsapp_memory.py src/api_server.py tests/test_whatsapp_handler.py
git commit -m "feat: export whatsapp qa feedback eval cases"
```

Expected: commit succeeds.

---

### Task 8: Verification And Deployment Gate

**Files:**
- Modify only files changed by previous tasks.

- [ ] **Step 1: Run full unit tests**

Run:

```bash
PYTHONPATH=src .venv/bin/python -m unittest
```

Expected:

```text
OK
```

- [ ] **Step 2: Run compile check**

Run:

```bash
.venv/bin/python -B -m compileall src
```

Expected: no syntax errors.

- [ ] **Step 3: Run diff whitespace check**

Run:

```bash
git diff --check
```

Expected: no output.

- [ ] **Step 4: Verify no secrets were added**

Run:

```bash
git diff --cached --stat
git diff --cached -- docs src tests | rg -n "sk-|token|secret|PIN|STEPFUN_API_KEY|DEEPSEEK_API_KEY|WHATSAPP_ACCESS_TOKEN" || true
```

Expected: no secret values. Environment variable names are allowed; real values are not.

- [ ] **Step 5: Deploy after tests pass**

Deploy through Zeabur UI or a private local Zeabur command stored outside this repository. Do not commit project IDs, service IDs, access tokens, or platform secrets in this plan; `agent.md` explicitly keeps those details out of committed docs.

Expected: latest commit reaches a running deployment, and the report includes only redacted evidence such as commit SHA, deployment status, `/health` result, and admin security check.

- [ ] **Step 6: Check production health and admin security**

Run:

```bash
curl -fsS https://parent-school-bot.zeabur.app/health
curl -fsSI https://parent-school-bot.zeabur.app/admin
curl -fsS -o /tmp/agent_tasks_unauth.txt -w "%{http_code}" https://parent-school-bot.zeabur.app/api/whatsapp/agent-tasks
```

Expected:

- `/health` returns HTTP 200 and healthy JSON.
- `/admin` returns HTTP 200 login page or authenticated page.
- unauthenticated `/api/whatsapp/agent-tasks` returns `401`.

- [ ] **Step 7: Manual WhatsApp smoke test**

Send these messages to the bot from a test WhatsApp number:

```text
八歲，情緒
幫我揀
更多
推薦餐廳
```

Expected:

- `八歲，情緒` stores `7-12歲` and `情緒壓力`.
- `幫我揀` recommends real DSEDJ courses with official links.
- `更多` continues the last query.
- `推薦餐廳` gets a course-domain refusal and does not call LLM.

- [ ] **Step 8: Commit final verification note if docs changed**

If `docs/harness_engineering.md` or this plan changed during execution:

```bash
git add docs/harness_engineering.md docs/superpowers/plans/2026-05-22-whatsapp-harness-engineering.md
git commit -m "docs: update whatsapp harness verification notes"
```

Expected: commit succeeds only if there are doc changes.

---

## Self-Review

- Spec coverage: The plan covers local-vs-LLM routing, ASR entry, deterministic extraction, LLM JSON contract, course source of truth, recommendation boundary, memory trace, admin QA, evals, deployment checks, and security boundaries.
- Placeholder scan: The plan contains exact file paths, commands, test cases, and expected results. The only omitted command is Zeabur deployment, because committed docs must not store platform IDs or secrets.
- Type consistency: The plan consistently uses `decision["route"]`, `decision["allow_llm"]`, `decision["recommended_action"]`, and `profile_patch` across tests and implementation.

---

## Recommended Execution Order

1. Task 1 first, because it freezes the product/engineering contract.
2. Task 2 and Task 3 next, because NLU and route decisions are the real harness core.
3. Task 4 after route decisions exist, because admin trace should reflect a stable route model.
4. Task 5 before broader friend testing, because it prevents the LLM from accepting invalid profile values.
5. Task 6 and Task 7 before the next deployment cycle, because they turn testing and friend feedback into a repeatable improvement loop.
6. Task 8 every time before reporting success.
