# GWDG models for LOLA

Snapshot of 2026-10-20. Capabilities come from the model list of the GWDG API (`text`, `image`,
`code`, `omni`, `embeddings`); descriptions and thinking notes come from the
[GWDG model overview](https://docs.hpc.gwdg.de/services/ai-services/chat-ai/models/) and the
[Chat AI issue #172](https://github.com/gwdg/chat-ai/issues/172). GWDG changes the list often, so
check it before relying on a row. The model stays a choice in the teacher's API key, not in code.

## Chat models

| Model | Capabilities | Size / architecture | Thinking | Typical use |
|---|---|---|---|---|
| `meta-llama-3.1-8b-instruct` | text | 8B, dense | none | Fastest and lightest; small talk, simple Q&A, latency baseline |
| `qwen3-30b-a3b-instruct-2507` | text | 30B MoE, ~3B active | none (non-thinking only) | Fast general-purpose chat with good quality; first choice for a snappy avatar |
| `deepseek-v4-flash-0731` | text | MoE, 304B total, 1M context | not stated | Long documents and strong all-round answers |
| `gemma-4-31b-it` | text, image | 31B | not stated | General chat with images |
| `qwen3.6-35b-a3b` | text, image | 35B MoE, ~3B active | switchable, see the model's Hugging Face page | Efficient multimodal chat; test thinking on/off |
| `qwen3.8-27b` | text, image | 27B | "flexible thinking control" | Complex tasks where thinking can be switched per request |
| `qwen3.5-397b-a17b` | text, image | 397B MoE, ~17B active | reports a thought output | Hardest reasoning, maths and coding; slowest and most expensive in compute |
| `glm-5.3-flash` | text, image | MoE, 320B total, ~18B active | levels low / high / max, cannot be switched off | Coding and agentic tasks |
| `qwen3-coder-next` | text, code | 80B MoE, ~3B active | not a reasoning model | Code generation; not meant for a tutoring persona |
| `qwen3-omni-30b-a3b-instruct` | text, omni | 30B MoE, ~3B active | not stated | Text, image, audio and video input |

## Embedding models

| Model | Capabilities | Typical use |
|---|---|---|
| `e5-mistral-7b-instruct` | embeddings | Knowledge-base embeddings through an OpenAI-compatible `embedding` key (larger, slower) |
| `multilingual-e5-large-instruct` | embeddings | Same, smaller; multilingual, a fit for German material |

## What this means for LOLA

- **Speech and latency:** an avatar answers in short spoken turns, so the models without thinking
  (`meta-llama-3.1-8b-instruct`, `qwen3-30b-a3b-instruct-2507`) are the natural fast baseline.
- **Thinking switch (project setting "Thinking"):** the candidates for comparing one model with and
  without reasoning are `qwen3.6-35b-a3b` and `qwen3.8-27b`. `glm-5.3-flash` offers levels only
  and `qwen3-30b-a3b-instruct-2507` cannot think at all, so the switch has no effect on them.
  Whether GWDG forwards `chat_template_kwargs.enable_thinking` is not documented; verify it with
  a latency run.
- **Images and audio:** LOLA sends text only, so `image` and `omni` capabilities are not used yet.
- **Code models** are not a fit for the tutor persona.
- **Embeddings** are only used when the optional knowledge service is on and a teacher picks an
  `embedding` key for a knowledge base.
