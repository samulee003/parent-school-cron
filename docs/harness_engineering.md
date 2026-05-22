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
