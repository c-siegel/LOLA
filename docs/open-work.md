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

Nothing is implemented. Start with phase 0:
- Add time to first token and cached-token counts to the Latency Lab and measure the `off` baseline.
- Add `ThinkTagGuard`: a model that emits `<think>` in its content is spoken aloud today. This is a
  bug on its own, so it needs a regression test.
- Decide, from the baseline, whether moving per-turn material behind the history (prompt order)
  pays off.
- Open: the ethics process for labelled lesson data, and whether students see a thinking indicator.
