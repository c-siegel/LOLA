# Plan: EmbeddingGemma 2 as a local embedding model for the knowledge service

Status: **steps 1–4 done** (loader, allowlist entry, tests); the model is selectable but opt-in.
Next: step 5 (evaluation), then step 6 (docs). Written 2026-10-13; spike results added 2026-10-20.

## Spike results (step 1, 2026-10-20)

- Repo revision `daa72c51243991dfcaf9f9137d2c573d8f7790c0`, licence `apache-2.0` in the card
  metadata (Gemma 4 licence = Apache 2.0). No separate `LICENSE` file, and the card has no
  Prohibited Use Policy section, so there is no extra note for the README.
- Text graph: `onnx/model_quantized.onnx` + `onnx/model_quantized.onnx_data` (314 MB).
  Inputs `input_ids`, `attention_mask` and `image_/video_/audio_features [n, 512]`; outputs
  `last_hidden_state`, then `sentence_embedding [b, 768]`. So fastembed can't load it → option B
  (`OnnxTextModel`). Pooling and projection are inside the graph; the output is already L2-normalised.
- Prompts (`config_sentence_transformers.json`): query `task: search result | query: `,
  document `title: none | text: `.
- 4 German passages × 4 questions: each question's right passage is nearest (cosine distance
  0.18–0.25 vs. 0.40–0.51 for the others). ~45 ms per query, ~10 s for 64 × 1400 characters,
  peak RSS ~1.5 GB during the batch (developer laptop, CPU).
- **For step 5:** small talk "Hallo!" lands at distance ~0.35 to every passage, well below the
  default `RAG_MAX_VECTOR_DISTANCE=0.75`, so it would retrieve passages. The threshold needs
  tuning for this model (somewhere between ~0.25 and ~0.35 on this sample).

## Goal

Offer `onnx-community/embeddinggemma-2-ONNX` (text tower, q8) as an **opt-in** local embedding
model next to the current default `jinaai/jina-embeddings-v2-base-de`. Switch the default only if
the evaluation in step 5 shows a clear win on German material.

## Why this model, and why not just switch

| | Jina v2 base-de (today) | EmbeddingGemma 2, text-only q8 |
|---|---|---|
| Licence | Apache-2.0 | Apache-2.0 (plain licence text in the ONNX repo) |
| Text model size | ~640 MB | ~314 MB (q8; fp32 is 1085 MB) |
| Dimensions | 768 | 768 (MRL: 512 / 256 / 128 possible) |
| Context | 8192 tokens | 8192 tokens |
| Languages | German/English | 100+, "may not perform equally across languages" |
| MTEB multilingual v2 | – | 61.36 (EmbeddingGemma 1: 61.15, i.e. flat) |
| Maintenance | 2023 model | 2026, Google, actively used on-device |

The attraction is footprint, MRL (3× smaller vectors at about −1 MTEB point for 256d), long-term
support and licence clarity. There is **no evidence yet that it retrieves German better**, hence
opt-in first.

Rejected options (details in the research notes of the PR/issue that links this file):

- `google/embeddinggemma-300m` (v1): Gemma licence, not permissive → fails the licence rule.
- The OpenVINO int8 conversion (`wesjos/embeddinggemma-2-openvino-int8`): unofficial, three hops
  from Google, adds a second inference runtime. The onnx-community export covers the same need.
- q4 / q4f16 (text cosine to fp32 only 0.988) and fp16 (the card warns about fp16 activations):
  too lossy or risky for stored vectors. Use **q8** (worst-case cosine 0.9997) or fp32.

## What to know about the code first

All in `rag/` (knowledge service). Relevant code:

- `rag/app/embedding.py`: `LOCAL_MODELS` allowlist of `LocalModelSpec(dimensions, licence,
  chunk_size, chunk_overlap, query_prefix, passage_prefix)`. `LocalEmbedder` wraps any object with
  `.embed(texts, batch_size)` (fastembed's `TextEmbedding` in production, `FakeLocalModel` in
  `rag/tests/conftest.py`). `_load_local_model(model_id)` is the test seam.
- `rag/app/config.py`: `RAG_LOCAL_EMBEDDING_MODEL`. `rag/app/main.py` validates it at startup via
  `local_model_spec()` and compares `config.model` to it on upload/search.
- `rag/app/store.py`: one `vec0` table per dimension (`vec_chunks_<dim>`). A 768-dim model reuses
  `vec_chunks_768`, but a knowledge base is bound to one model (see the "re-create" note in the
  README), so existing KBs are **not** converted when the setting changes.
- `docker/rag.Dockerfile` preloads a model with fastembed (`PRELOAD_EMBEDDING_MODEL`).
- `scripts/check-licences.sh` gates Python packages only (not model weights). Model licences are
  checked by hand via the allowlist comment in `embedding.py`.

## Steps

### 1. Spike (needs `huggingface.co` and `cdn-lfs.huggingface.co` reachable)

Run in a scratch directory, not in the repo. Decide with the results how step 2 is built.

```bash
pip install huggingface_hub onnxruntime tokenizers numpy
python -I - <<'EOF'
from huggingface_hub import list_repo_files, hf_hub_download
repo = "onnx-community/embeddinggemma-2-ONNX"
print("\n".join(sorted(list_repo_files(repo))))          # which files, which dtype suffixes
EOF
```

Then download only the text graph (the q8 variant, probably `onnx/model_quantized.onnx` plus any
`.onnx_data` sidecar) and `tokenizer.json`, and print:

- `sess.get_inputs()` / `sess.get_outputs()` names and shapes. Expected: `input_ids`,
  `attention_mask`, output `sentence_embedding` `[b, 768]`. **Check whether extra inputs are
  required** (e.g. `image_features`, position ids): that decides whether fastembed can load it.
- Whether the output is already L2-normalised (norm ≈ 1). `LocalEmbedder` normalises anyway.
- Embed 10 German sentence pairs with the documented prefixes (`task: search result | query: `,
  `title: none | text: `) and check that related pairs score clearly higher than unrelated ones.
- Memory and time: peak RSS after load, ms for one query, seconds for 64 × 1400-character chunks,
  on the machine that will host the service.
- Confirm the repo's `LICENSE` file is Apache-2.0 and note the **revision hash** to pin.

Exit criteria: the text graph runs on onnxruntime CPU, outputs 768 sane dimensions, and German
sanity pairs rank correctly. If not, stop and report; nothing else in this plan is worth doing.

### 2. Loader

Two options, chosen by the spike result:

- **A. fastembed custom model** (preferred if the graph only needs `input_ids` / `attention_mask`):
  `TextEmbedding.add_custom_model(...)` with `PoolingType.DISABLED` (the graph already pools) and
  the downloaded model file name. Smallest diff: only `_load_local_model` and the spec change.
  fastembed 0.9 takes the first graph output and feeds only `input_ids`, `attention_mask` and
  optionally `token_type_ids` (`text/onnx_text_model.py`), so this fails if the text graph needs
  other inputs.
- **B. Small adapter class** in `embedding.py` (e.g. `OnnxTextModel`) implementing
  `.embed(texts, batch_size)` with `onnxruntime` + `tokenizers` + `huggingface_hub` (all already
  installed through fastembed; list them explicitly in `rag/pyproject.toml` with a comment, like
  `onnxruntime` is listed in `backend/pyproject.toml`). Pad per batch, truncate at 8192 tokens,
  honour `RAG_EMBEDDING_THREADS` through `SessionOptions.intra_op_num_threads`. It plugs into
  `LocalEmbedder` unchanged, so search/ingest code does not move.

In both cases download with `huggingface_hub` into `settings.model_cache_dir` and pin the
`revision` found in step 1 in the spec, so a force-pushed upstream repo cannot change vectors under
existing knowledge bases. Download only the text graph and tokenizer, never the vision/audio
encoders.

### 3. Allowlist entry and spec

In `LOCAL_MODELS` add `onnx-community/embeddinggemma-2-ONNX`:

- `dimensions=768`, `licence="Apache-2.0"`, `chunk_size=1400`, `chunk_overlap=200` (well inside
  8192 tokens; revisit in step 5).
- `query_prefix="task: search result | query: "`, `passage_prefix="title: none | text: "`.
- A comment saying it is the community ONNX export of Google's model, which dtype is used and why
  (q8, not fp16/q4), and the Gemma Prohibited Use Policy note from the model card.

**Optional, separate commit:** an MRL dimension (`truncate_dim` on the spec, e.g. 256). If added:
slice then L2-normalise (the card says skipping this silently degrades ranking), store the
truncated size as `dimensions`, and make sure query and passage vectors both go through it. It
is extra code that only pays off if step 5 shows 256d is good enough, so decide after it.

If the spec does not yet carry a revision / file-name field, add it in this step as a plain
optional field (default `None` for the fastembed models).

### 4. Tests (no download, no real model)

- Extend `rag/tests/test_api.py` / a new `rag/tests/test_embedding.py`: the allowlist contains the
  new model with the right prefixes, `RAG_LOCAL_EMBEDDING_MODEL` set to it passes
  `local_model_spec()`, a non-allowlisted model (e.g. `jinaai/jina-embeddings-v3`) is still
  rejected, `chunk_size_for` returns the spec's values.
- If option B: unit-test the adapter with a tiny fake `InferenceSession` / tokenizer (padding,
  batching, truncation, normalisation). Never download the real model in tests (AGENTS.md rule).
- Keep `conftest.py` working: it derives `DIMENSIONS` from the configured model, so run the whole
  suite once with the new model selected (`RAG_LOCAL_EMBEDDING_MODEL=... RAG_SERVICE_TOKEN=test
  python -m pytest`) and once with the default.

### 5. Evaluate before changing the default

Use `rag-eval/` (Ragas) on a real German knowledge base (teaching PDFs/DOCX, not English
benchmarks). Compare Jina v2 base-de, EmbeddingGemma 2 q8, and optionally
`Qwen/Qwen3-Embedding-0.6B-Q` (already supported by fastembed 0.9, Apache-2.0) on:

- retrieval quality (context precision / recall, answer relevancy),
- ingest time for the same corpus, query latency, RAM of the service,
- `RAG_MAX_VECTOR_DISTANCE` (default 0.75 is "model-dependent", see `config.py`): check the
  distance of true hits and of small talk ("Hallo!") for the new model and tune if needed,
- chunk size 1400 vs. larger (the new model has the same context window, so try 2000 too).

Record the results in this file or in the PR. Only then decide: keep opt-in, or make it the default.

### 6. Docker and docs

- `rag/README.md`: add the model to the "Local embedding models" table (licence, size, notes),
  mention the re-create-your-knowledge-bases consequence of switching, note the model card's
  Prohibited Use Policy sentence, and update the "about 600 MB" download figure if the default
  changes.
- `docker/rag.Dockerfile`: the preload `RUN` uses fastembed and will not know the new model if
  option B was chosen. Teach it the same download function (e.g. `python -m app.embedding
  --download`, or a small script) so `PRELOAD_EMBEDDING_MODEL` works for it; update the example in
  the comment. Update `docker/README.md` (model size) accordingly.
- `docs/rag-plan.md` §4 (allowlist) and the settings table: add the model.
- `.env.example`: the comment for `RAG_LOCAL_EMBEDDING_MODEL` should list the allowed values.
- Run the `docs-sync` agent after the change.

### 7. Rollout notes (for the PR description)

- Existing knowledge bases keep working only while the service runs the model they were built
  with. Changing `RAG_LOCAL_EMBEDDING_MODEL` makes them show the "re-create" message; there is no
  automatic migration. For a default switch, say so in the release notes.
- Model files are downloaded from Hugging Face into the cache on first use; offline hosts need the
  preload build arg.
- No database migration, no frontend change, no backend change: the service reports
  `local_model` / `local_model_dimensions` via `GET /capabilities`, and 768 dimensions are already
  what `backend/tests/fake_rag.py` assumes.

## Commit breakdown (one topic per commit)

1. Add the loader (option A or B) and its tests.
2. Add EmbeddingGemma 2 to the local model allowlist (spec, prefixes, pinned revision) and tests.
3. Document the model and update the Dockerfile preload.
4. (Optional) Add Matryoshka dimension truncation.
5. (Only after evaluation) Change the default model.

Each commit message ends with the `Co-Authored-By:` line(s) for the contributing model(s) and
nothing after it (AGENTS.md). PRs target `working-branch`.

## Open questions

The graph questions were answered by the spike. What remains is in [open-work.md](open-work.md).
