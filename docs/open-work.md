# Open work

Unfinished items from the plans in this folder, in one place. Delete a line when it's done, and
delete the plan once nothing in it is open.

## EmbeddingGemma 2 ([plan](embeddinggemma-2-plan.md))

- Evaluate against Jina v2 base-de on a real German knowledge base with `rag-eval/` (step 5), then
  decide: stay opt-in or become the default.
- Tune `RAG_MAX_VECTOR_DISTANCE` for this model: small talk lands at ~0.35, so it needs a value
  between ~0.25 and ~0.35.
- Docs and Docker (step 6): `rag/README.md` model table, Dockerfile preload for the non-fastembed
  loader, `docs/rag-plan.md` §4, `.env.example`.
- Decide whether to ship MRL (256d) truncation at all.

## Pocket TTS ([plan](pocket-tts-plan.md))

- Test real voice cloning with `TTS_HF_TOKEN` (a teacher clip and a donated voice).
- Build the Docker image of the sidecar (only release CI does so far).
- English verbalizer: numbers, years, money, ranges, ordinals, superscripts, before English
  projects rely on local TTS.
- Curate the donated voices by ear and set `TTS_DONATED_VOICES`.
- Ask Kyutai where the `juergen` (German) and `rafael` (Portuguese) presets come from.
- Browser engine: ONNX bundles for languages other than German.
- Choose the iPad models for on-device tests (decides whether the 24-layer German model is worth
  offering in the browser).
- Consent checkbox for voice uploads, required before Pocket is offered beyond teachers' own clips.
- More languages for LOLA (French as pilot): see the plan's section of that name.

## Adaptive reasoning ([plan](adaptive-reasoning-plan.md))

Done: phase 0a (`ThinkTagGuard`), 0b (token and cache counts in the Latency Lab) and phase 1
(`reasoning_mode` default/off/always with effort, per-model provider mapping, reasoning budgets,
timeout with fallback to the fast route, SSE keep-alives, the route in the Latency Lab).

Phase 2 is done except for the experimental self-escalation (the fast model starting a reply with
`::deep::`), which was left out on purpose: build it only if the Latency Lab shows the rules miss too
many arguments. Decided differently from the plan: the bridge audio is rendered lazily into an
in-memory cache (per voice and key) instead of a stored file per project.

Open after phase 2:
- Phase 3, the evaluation (plan §10): router precision and recall against human labels, answer
  quality, perceived latency. Needs the ethics process before real lesson turns are labelled.
- A classifier only if the rules prove too weak (plan §7).
- Show the route in the transcript view of the analytics page (the API already returns it).
- Warm the bridge phrases when a project is opened instead of on its first turn.

Phase 0c, prompt order, waits for measurements:
- Run the Latency Lab script with reasoning `off` on each target provider (OpenAI, GWDG, Ollama or
  vLLM) and note first-token time and cached prompt tokens against prompt tokens.
- Decide from those numbers whether moving the per-turn material (checklist status line, knowledge
  passages) behind the history pays off; if so, do it as its own PR and re-check answer quality with
  `rag-eval`. Test cases to write then: the system prompt is identical across turns of one
  conversation, the late material still reaches every provider (including the Arcana body), and a
  provider that rejects a second system message gets the user-role preface.
- Open: the ethics process for labelled lesson data, and whether students see a thinking indicator.
