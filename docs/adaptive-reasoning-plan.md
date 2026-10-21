# Plan: adaptive reasoning for LOLA avatars

Status: **draft, nothing implemented.** Written 2026-10-20, reviewed against the code the same day
(see "Changes from the first draft"). Open work: [open-work.md](open-work.md).

The goal is to answer simple turns fast and give hard turns (theses, arguments, counterarguments) a
reasoning model, without the student waiting in silence.

## Changes from the first draft

The first draft was checked against the code and the licence/cost rules. What changed:

- **The prompt-cache claim was too simple.** The system prompt also changes per turn through the
  checklist status line (`chat/checklist.py`), not only through knowledge passages. The fix is to
  move *all* per-turn material behind the stable prefix, and to measure the effect before
  promising a saving (§4.3).
- **Thinking gestures already exist** (`Project.thinking_motions_enabled`). The plan reuses them
  instead of adding a new mechanism.
- **The classifier no longer lives in `rag/`.** `rag/` is an optional service (`RAG_ENABLED=false`
  by default) and its embedding model is configurable (Jina, EmbeddingGemma 2 or an API model).
  Tying adaptive routing to it would make an unrelated feature depend on the knowledge service and
  tie labelled data to one embedding model. Rules first; a classifier only if the data shows rules
  are not enough (§7).
- **Laya and GLiClass were dropped from the plan** until a measured need exists. The draft's claims
  about them (release date, licence, German quality) were not verified; if they come back, check
  the licence on the official model card first.
- **Added: cost and abuse limits.** The public chat is unauthenticated and bills the teacher's key.
  `always` mode and a student-triggerable `::deep::` marker are a cost lever for any visitor (§4.7).
- **Added: a baseline measurement first** (phase 0), because the latency and cost numbers in §6 are
  estimates, not measurements.
- **Added:** `litellm>=1.48` is not pinned to a version that is known to map `reasoning_effort`;
  the mapping must be verified and the lower bound raised (§4.1).
- **Added:** bridge phrases only make sense when the project speaks; text-only and on-device TTS
  projects need their own handling (§4.6).
- **Added:** labelled lesson data is student data (AGENTS.md); it needs the ethics process and
  pseudonymisation before anyone collects it (§10).

## 1. Goals and non-goals

**Goals**
- Keep the first audible word at ≤ ~1.5 s for ordinary turns (greeting, small talk, simple factual
  questions).
- Use reasoning when it matters, for example when a student defends a thesis or brings an argument
  or a counterargument.
- Make the trade-off a teacher setting per project, not a code change.
- Log which route each turn took, as metadata only, so latency and quality can be evaluated in the
  Latency Lab and in analytics.
- Work with cloud providers (OpenAI, Anthropic, Gemini, Mistral), GWDG (SAIA/Arcana) and
  self-hosted open models (Ollama, vLLM, OpenAI-compatible endpoints).

**Non-goals**
- Guardrails/moderation and safeguarding classifiers (need their own privacy review).
- Automatic coding of conversations for analytics.
- VAD (separate, independent change).

## 2. Current state (checked against the code)

| Finding | Where | Consequence |
|---|---|---|
| One LLM per project: `Project.llm_api_key_id` → `UserApiKey` (provider + `model_id` + `api_base`) | `projects/models.py`, `api_keys/models.py` | No second model and no per-turn choice |
| The call sends only `messages` + temperature/top_p | `ai/llm/history.py::sampling_params`, `litellm_provider.py`, `arcana.py::_body` | Reasoning can't be switched on or off; the provider's default applies |
| The stream yields only `delta.content` | `litellm_provider.py::stream` | Reasoning tokens are dropped, but the first content token arrives only after the thinking phase |
| No `<think>` filter | (missing) | Qwen3 or DeepSeek-R1 behind Ollama, or vLLM without a reasoning parser, emit `<think>…</think>` as content and the avatar would **speak** it. This is a bug today, independent of this plan |
| Knowledge passages **and** the checklist status line go into the system prompt | `history.py::build_messages`, `chat/checklist.py` | The system prompt changes per turn, so prefix caching only covers the persona part |
| Retrieval runs before the LLM call, in sequence | `chat/pipeline.py::_retrieve` → `llm.stream` | A router that needs I/O must run in parallel with retrieval |
| Markers in the reply are parsed (`::wave::`, `::done_N::`) | `chat/motion.py`, `chat/checklist.py`, `_split_markers` | A `::deep::` marker fits the pattern but must be cut out *before* the sentence chunker |
| Thinking gestures while a reply is generated | `Project.thinking_motions_enabled`, `chat/schemas.py` | Already covers the "avatar is thinking" cue |
| The greeting is pre-rendered once as audio | `projects/start_audio.py` (invalidated on prompt/voice/TTS-key change) | Bridge phrases can reuse the idea, but one clip per phrase and language |
| The Latency Lab measures every step | `chat/latency_router.py`, `pages/Dashboard/LatencyLab` | The natural testbed |
| Messages are stored as JSON (`Conversation.messages_json`) | `chat/models.py` | A per-message `route` field needs no migration |
| `litellm>=1.48` is the only bound | `backend/pyproject.toml` | `reasoning_effort` mapping depends on newer versions |

## 3. Two models, or one model with a switch?

| | A: one model, switch per request | B: two models (fast + deep) | C: hybrid (chosen) |
|---|---|---|---|
| Idea | Same model; per turn send `reasoning_effort` / `thinking` / `enable_thinking` | Separate fast and deep key/model | A **fast profile** and a **deep profile**; deep is either the same key with reasoning on, or a second key |
| Persona/voice style | identical | can drift between models | teacher's choice |
| Prompt/KV cache | one cache | two caches, cold start on first escalation | depends |
| Availability | many models aren't switchable (Qwen3 2507 split into Instruct and Thinking; GWDG lists the Instruct one) | works with any two models | works |
| Fast path speed | a reasoning model with thinking "off" is often slower or worse than a small dedicated model | fast model can be tiny | works |
| Teacher setup | one dropdown | two keys | "same model" default, second key optional |

**Chosen: C.** In code both cases are `(key, reasoning_params)` per route, so one data model and one
router serve both. GWDG currently needs two models (the fast Instruct model can't think).

### Data model (one migration)

`Project`, all with a `server_default` or nullable, via `op.batch_alter_table`, with a real
`downgrade()`:

| Column | Type | Default | Meaning |
|---|---|---|---|
| `reasoning_mode` | str | `'off'` | `off` (as today) · `always` · `adaptive` |
| `reasoning_effort` | str \| null | null | `low`/`medium`/`high` for the deep route; null = provider default |
| `deep_llm_api_key_id` | FK userapikey \| null | null | Second key for the deep route; null = same key as `llm_api_key_id` |
| `deep_llm_model` | str \| null | null | Denormalised like `llm_model`, for analytics and project cards |
| `bridge_enabled` | bool | `true` | Speak a short bridge phrase when a turn escalates |

`deep_llm_api_key_id` goes through the same owned-key check as `llm_api_key_id`
(`get_owned_key_of_type(..., KEY_TYPE_LLM)`) and through project export/import
(`projects/export.py`, `transfer_router.py`) without key material.

## 4. Backend changes

### 4.1 Passing reasoning parameters per provider
`ChatRequest` (`ai/llm/base.py`) gains `reasoning: Literal["off","low","medium","high"] | None`. A
helper `reasoning_params(provider, request)` next to `sampling_params` maps it. "Omit when unset"
stays the rule.

| Provider path | Mapping (**verify against the litellm version in use; raise the lower bound in `pyproject.toml` with a comment**) |
|---|---|
| OpenAI o-series / GPT-5, gpt-oss via OpenAI-compatible | `reasoning_effort` (`off` → omit, or `minimal` where supported) |
| Anthropic | litellm maps `reasoning_effort` → `thinking`; `off` → omit. Extended thinking forbids a custom `temperature`, so drop sampling params on the deep route |
| Gemini 2.5 | `reasoning_effort`; `off` → disable for Flash/Flash-Lite (Pro can't be fully disabled) |
| vLLM / OpenAI-compatible (Qwen3 hybrid) | `extra_body={"chat_template_kwargs": {"enable_thinking": bool}}`. The server needs `--reasoning-parser qwen3`, otherwise the thinking lands in `content` |
| Ollama | `think: true/false` on newer Ollama versions |
| GWDG SAIA / Arcana | depends on the model: gpt-oss → `reasoning_effort`; Instruct-2507 → nothing. Arcana adds it in `arcana.py::_body` |

A capability flag per provider (`supports_reasoning_switch`) tells the configurator which options to
show. The GWDG model list changes, so the model choice stays in the teacher's key, not in code.

### 4.2 Never speak reasoning (do first, independent)
- New `ThinkTagGuard` in `ai/llm/`, same shape as `ArcanaReferenceGuard`. It cuts
  `<think>…</think>` and `<reasoning>…</reasoning>`, including tags split across deltas and an
  unterminated block (drop until close; if the stream ends inside the block, drop the rest).
- Applied in `stream()` and `complete()` for **every** provider, because some servers emit tags
  even with thinking "off".
- `reasoning_content` deltas stay ignored.
- Regression test required (AGENTS.md): a stream with `<think>` in `content` must not reach TTS.

### 4.3 Prompt order (independent; measure before and after)
Keep the system prompt stable (persona + motion prompt) and put everything that changes per turn,
meaning the checklist status line and `reference_material`, **after the history, just before the
user message** (a second system message, or a user-role preface where a provider rejects several
system messages; litellm handles Anthropic). A prefix cache can then cover system prompt and
history.

- Whether this saves anything depends on the provider: OpenAI-style prefix caching starts at about
  1,000 tokens, Anthropic needs explicit cache breakpoints, vLLM needs prefix caching enabled. So
  record time to first token and cached-token counts in the Latency Lab before claiming a gain.
- Check that answer quality is unchanged with `rag-eval`: models weigh instructions at the end of
  the context differently.

### 4.4 Router (`chat/reasoning_router.py`, new)
`decide(context, turn) -> Route` (FAST | DEEP, plus reason, confidence, `router_ms`).
- `off` → FAST; `always` → DEEP; `adaptive` → rules, then optionally self-escalation.
- **Rules** (0 ms): message length, argument cues per language ("weil", "deshalb", "ich behaupte",
  "meine These", "dagegen spricht", "because", "I argue" …), and whether the previous assistant
  message posed a task. Word lists live in a per-language table, not in the router logic. Rules are
  biased towards recall on argument cues; the cost cap (§4.7) bounds over-escalation.
- **Self-escalation** (optional): the fast model may start its reply with `::deep::`. The pipeline
  holds back the first ~12 characters, sees the marker, cancels the fast stream and switches to
  DEEP. It costs the fast model's TTFT on escalated turns and needs no new infrastructure. Caveats:
  the marker must be removed before the chunker, the persona prompt gets longer, behaviour differs
  between models, and a student can try to provoke it (§4.7). Keep it behind the Latency Lab
  evaluation before enabling it by default.
- A **classifier** is out of scope for now (§7). If added later, it runs in parallel with
  `_retrieve` with a hard timeout and falls back to rules, and never blocks the chat.
- Thresholds and timeouts are module constants until evidence shows they need tuning per project.

### 4.5 Pipeline changes (`chat/pipeline.py`)
- `prepare_chat` also resolves the deep key (falling back to the main key).
- `stream_turn`: decide the route (in parallel with `_retrieve` if the router does I/O); for DEEP
  with `bridge_enabled`, emit a `chunk` with a bridge phrase and `"bridge": true` first, so the
  frontend plays it without storing it as reply text; then stream the chosen model's reply as
  today; `done` gains `route`, `routeReason`, `routerMs`, `bridgeMs`, `model`.
- `reply_turn` (non-streamed) gets the same routing without the bridge.
- `_save`: the message entry gets `route` and the model. It is metadata, not student content, so
  exports and retention cover it unchanged. The deep model must honour the same markers
  (motions, checklist ticks); test that the checklist replay works across routes.

### 4.6 Bridge phrases
- A small fixed list per language, chosen at random, **neutral in content** ("Spannender Gedanke,
  lass mich kurz überlegen." / "Good point, give me a second to think that through."), so it can't
  contradict the real answer.
- Pre-rendered and cached per project voice, like `projects/start_audio.py` (one clip per phrase;
  re-render when the voice or TTS key changes), so no TTS call happens at bridge time.
- Projects without TTS get the phrase as text only. Projects using on-device TTS synthesise it in
  the browser. Check both before choosing the storage format.
- Later option: teacher-editable phrases (needs defaults in `de.json` and `en.json`).

### 4.7 Cost and abuse limits
The public chat is unauthenticated and spends the teacher's key; reasoning turns cost 2–6× more.
- The existing chat rate limits apply to deep turns as well; add a lower per-visitor, per-hour cap
  on DEEP turns (falls back to FAST, never an error), and a clear cap for `always`.
- `::deep::` is only honoured when the project mode is `adaptive`, and counts against the same cap,
  so a student can't turn it into a cost lever.
- Show the route rate and estimated deep-turn share in analytics so teachers see the bill coming.
- Log the route and timings only, no message text (AGENTS.md).

## 5. Frontend changes

- **Configurator, Step 2 (Technical)** (`Configurator/steps/Step2Technical.tsx`): a "Reasoning"
  section with mode, effort, an optional second key ("Use a different model for difficult turns")
  and the bridge toggle. Options are hidden or disabled by the provider capability flag.
- **Public chat** (`pages/PublicChat/index.tsx`): play a `bridge` chunk like any chunk but don't
  add it to the history sent back. Existing thinking gestures run until the first real chunk.
- **Latency Lab**: route selector (`auto / force fast / force deep`); show route, router time and
  bridge time per turn.
- **Analytics**: route per message and route counts in the CSV export (`analytics/csv_export.py`).
- All new strings through `t()` and into both `de.json` and `en.json`.

## 6. Compute and cost estimates

Rough orders of magnitude for planning, **not measurements**; replace them with Latency Lab numbers
in phase 0. Assumptions: input ≈ 2,000 tokens per turn, visible reply ≈ 150 tokens, hidden
reasoning ≈ 500–3,000 tokens per deep turn, billed as output.

| Path | Time to first audible word |
|---|---|
| Fast model, cache warm | 0.6–1.5 s |
| Deep model, no bridge | 4–25 s (scales with effort and difficulty) |
| Deep model + bridge | ~1 s for the bridge, then the answer |
| Router overhead | 0 ms (rules) · +0.3–0.8 s on escalated turns (self-escalation) |

| Mode | Output tokens per 1,000 turns | Relative total cost |
|---|---|---|
| `off` | ~150k | 1× |
| `always` | ~650k–3.15M | ~2–6× |
| `adaptive`, 20 % escalated | ~250k–750k | ~1.2–1.8× |

Self-hosted: Qwen3-30B-A3B (4-bit) needs about 18–20 GB VRAM and fits one 24 GB GPU; gpt-oss-20b
about 16 GB (`reasoning_effort` low is fast but never fully off); a small fast model plus
gpt-oss-120b needs an 80 GB GPU or GWDG. Reasoning turns occupy the GPU 5–20× longer than fast
turns, so a class of 30 students sending a thesis at once queues on one GPU. Adaptive mode is a
capacity measure as well as a latency one. A queue-aware fallback to FAST is a later option.

## 7. Classifier: deferred

Rules plus optional self-escalation come first. A classifier is only worth building if the Latency
Lab data and human labels (§10) show that rules miss too many argumentative turns.

If it is built:
- It must **not depend on the optional knowledge service**. Either a small endpoint inside the
  backend (no PyTorch; ONNX or scikit-learn) or its own optional sidecar.
- Embedding + kNN or logistic regression is the cheapest start, but the labels are tied to the
  embedding model that produced the vectors. Changing the model (the knowledge service already
  allows Jina and EmbeddingGemma 2) means re-embedding the labelled set.
- Fine-tuned small encoder (gbert/mmBERT class) is the most accurate option if a few thousand
  labelled turns exist. Zero-shot options (NLI, GLiClass, Laya) need no labels, but their German
  quality and weights licences must be verified before they are even tried.
- Every model must pass the licence rule in AGENTS.md (permissive only, `scripts/check-licences.sh`
  and an allowlist entry).

## 8. Phases

| Phase | Content | Size |
|---|---|---|
| **0** | Baseline: add TTFT, cached tokens and route-ready timings to the Latency Lab; measure `off` on the target providers. `ThinkTagGuard` (4.2) with a regression test. Prompt order (4.3), kept only if it measures better | S |
| **1** | `reasoning_mode` off/always + effort + provider mapping (4.1) + deep-turn cap (4.7); migration; configurator UI; Latency Lab shows the route | M |
| **2** | `adaptive`: second key, rules router, bridge phrases, route stored per message; self-escalation as an experiment | M–L |
| **3** | Evaluation (§10) | Research |
| **4** (only if §7 triggers) | Classifier | M |
| **later** | Reuse for analytics coding or guardrails, each with its own privacy review | – |

Each phase is its own PR to `working-branch`, one topic per commit.

## 9. Tests and checks

- **Backend (pytest, providers faked at the litellm seam):**
  - `reasoning_params` per provider: present only when set; Anthropic drops temperature on deep.
  - `ThinkTagGuard`: tags split across deltas, unterminated block, tag-like text in a closed block.
  - Router: off/always/adaptive; rules table per language; cap falls back to FAST.
  - Pipeline: the bridge chunk comes before deep content and isn't saved as reply text; `route` is
    in `messages_json`; the deep key falls back to the main key; checklist ticks and motions work
    on both routes; `::deep::` is cut out before chunking and ignored outside `adaptive`.
  - Ownership: a foreign `deep_llm_api_key_id` → 404; export contains no key material.
  - Migration: upgrade/downgrade on SQLite, single head.
- **Frontend:** `npm run build`, i18n parity, Playwright screenshots of configurator and Latency Lab.
- **Agents:** `migration-reviewer`, `backend-test-writer`, `i18n-checker`, `frontend-verifier`,
  `security-privacy-auditor` (public chat, keys, cost cap), `docs-sync`.

## 10. Evaluation design (research)

- **Conditions:** `off` · `always` · `adaptive` (with bridge) · optionally `adaptive` without bridge
  to isolate the bridge's effect on perceived latency.
- **Measures:** time to first audible word and to the full answer; router precision and recall
  against human labels; answer quality on argumentative turns (rubric, blind rating); student
  experience (perceived responsiveness, conversation flow); cost per lesson.
- **Router labels:** two raters label a sample of turns ("needs reasoning" yes/no); report Cohen's
  κ. Real lesson turns are student data: get ethics approval, consent and pseudonymisation first,
  and store labels apart from identifying data. Until then use synthetic or staff-written turns.

## 11. Risks and open questions

| Risk / question | Mitigation |
|---|---|
| Fast and deep models sound different (persona drift) | Same system prompt; default "same model"; style check in the Latency Lab |
| Bridge phrases feel formulaic | Random choice; only on escalation; teacher-editable later |
| Over-escalation → slow and expensive | Conservative rules, deep-turn cap, route rate in analytics, `off` always available |
| Under-escalation → shallow feedback on theses | Recall-biased rules; teacher can choose `always` |
| Visitor drives up the teacher's bill | Cap on deep turns, `::deep::` only in `adaptive` (4.7) |
| Classroom burst load on a self-hosted GPU | Adaptive mode; queue-aware fallback to FAST later |
| litellm parameter mapping changes between versions | Unit tests on the mapping; raise and pin the bound with a comment |
| **Open:** Should the visible reply mention that it "thought about it"? | Didactic decision for the research team |
| **Open:** Should students see a "thinking" indicator beyond gestures? | UX test in phase 2 |
| **Open:** Consent and anonymisation for labelled lesson data | Ethics process before any collection |

## Sources

Not re-checked in the review; confirm before relying on them:
- GWDG Chat AI model list: https://docs.hpc.gwdg.de/services/ai-services/chat-ai/models/
- vLLM reasoning outputs (Qwen3 `enable_thinking`, `--reasoning-parser`):
  https://docs.vllm.ai/en/latest/features/reasoning_outputs.html
