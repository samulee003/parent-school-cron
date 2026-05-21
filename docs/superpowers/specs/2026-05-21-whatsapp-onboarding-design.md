# WhatsApp Parent Onboarding Design

Date: 2026-05-21

## Goal

Turn first-time WhatsApp course lookup into a short parent interview instead of a course dump. The bot should learn enough context to recommend a few relevant Macau Parent Academy courses and later support proactive matching.

## User Experience

When a parent first sends a broad message such as `課程`, `你好`, `最新`, or a vague request without stored profile signal, the bot asks one short question:

```text
小朋友幾多歲？最近比較想處理：情緒、學習、親子溝通、升學壓力，還是其他？
```

The parent can reply naturally, for example:

```text
13歲，最近情緒壓力大
```

If the answer contains enough signal, the bot saves the memory and recommends 1-3 relevant courses with official links. After the recommendation, it asks for proactive-message consent softly:

```text
之後如果有貼近你情況的新課程，我可以偶爾提醒你。回覆「同意推送」即可。
```

If the answer is incomplete, the bot asks only for the missing part:

- Age known, pain point missing: ask what the parent is most concerned about.
- Pain point known, age missing: ask the child's age.
- Both missing: repeat the short interview question.

## Data Flow

Use the existing `WhatsAppHandler` and `WhatsAppMemoryStore` shape.

1. Incoming text is recorded in `whatsapp_messages`.
2. Existing deterministic extractors read age groups, target, topic, and pain points.
3. The profile is saved in `profile_json`.
4. Conversation tags are updated from profile signals, such as `青少年`, `情緒壓力`, `親子溝通`.
5. Conversation notes append or refresh a concise onboarding note, such as `onboarding: 13-18歲 / 情緒壓力`.
6. Once enough signal exists, existing course recommendation logic runs.
7. The consent prompt is appended only after a recommendation, and only when `consent_status` is still `unknown`.

## Recommendation Readiness

A profile is ready for course recommendation when it has:

- at least one age group, and
- at least one pain point, topic, target, or clear course preference.

When only one side exists, the bot should not call DeepSeek yet. It should ask a deterministic follow-up locally to control API cost.

## Copy Rules

All parent-facing copy stays short and Traditional Chinese.

Do:

- sound like a helpful human assistant,
- ask one question at a time,
- recommend a small number of courses,
- include official links directly.

Avoid:

- dumping all courses,
- asking for proactive consent before giving value,
- using general AI language such as "我可以回答任何問題",
- calling DeepSeek for unclear onboarding replies.

## Error Handling

If parsing fails after the parent replies to onboarding, add an `uncertain` agent flag and ask the parent to answer with age plus concern in one sentence.

If course scraping fails, reply with the existing course-data failure message and do not mutate consent status.

If no matching course exists, use the existing no-match reply and keep the stored profile for future proactive matching.

## Tests

Add focused tests for:

1. First broad course query without profile asks the onboarding question.
2. Natural onboarding answer stores age group, pain point, tags, and onboarding note.
3. Complete onboarding answer returns a small recommendation plus the soft consent prompt.
4. Partial age-only answer asks for concern and does not call DeepSeek.
5. Partial pain-only answer asks for age and does not call DeepSeek.
6. Consent prompt is not repeated when `consent_status` is `allowed` or `paused`.

## Non-Goals

- Do not add a new frontend framework.
- Do not add multi-step form state tables unless the existing profile memory proves insufficient.
- Do not change WhatsApp template sending in this task.
- Do not broaden LLM scope beyond Parent Academy course matching.
