# AI providers

Every project picks its own LLM (large language model, generates the replies) and, optionally, its own TTS (text-to-speech) voice. Each user connects them with their own API key under **API Keys** in the dashboard.

| Provider | Used for | Notes |
|---|---|---|
| Anthropic | LLM | Claude models |
| OpenAI | LLM + TTS | GPT models for chat, `tts-1` for voices |
| Google Gemini | LLM + TTS | |
| Mistral | LLM | |
| Ollama | LLM | Self-hosted, e.g. on your own GPU server |
| OpenAI-compatible endpoint | LLM | Any API that mimics OpenAI's (vLLM, Together.ai, Groq, …) |
| Cartesia | TTS | |
| Google Cloud TTS | TTS | The classic per-character Cloud TTS product, not Gemini's built-in voice |
| GWDG Arcana | LLM | German academic cloud; adds RAG against a knowledge base configured there |

## Speech input and output without a key

- **STT (speech-to-text)** needs no key. It runs on the visitor's device (Parakeet Redux via WebGPU, with live text while speaking) and falls back to a bundled offline model on the server. See [on-device-speech.md](on-device-speech.md).
- **TTS** can work without a key too: leave a project's TTS key unset and it uses the [local-TTS sidecar](../local-tts/README.md), if the deployment runs it. Otherwise there is no speech output.

## Reasoning setting

Projects whose model can reason get a **Reasoning** setting in the configurator's technical step: *Model default* (nothing is sent), *Off*, or *Always*, plus a level (low, medium, high) where the model takes one. Whether a model can be switched is decided per model, not per provider (`gpt-4o` can't, `o3` can); the setting is hidden when it couldn't do anything. What is sent:

| Model | Off | Always |
|---|---|---|
| OpenAI o-series / GPT-5 | `reasoning_effort` `low` (`minimal` for GPT-5); o-series can't be fully switched off | the level, else the model's default |
| Anthropic | extended thinking not requested | extended thinking with the level (`medium` without one) |
| Gemini 2.5 | thinking disabled (not possible for Pro) | the level, else the model's default |
| Ollama | `think: false` | `think: true` or the level |
| OpenAI-compatible endpoint, GWDG Arcana (vLLM-style) | `chat_template_kwargs.enable_thinking: false` | `enable_thinking: true` (no levels) |

Temperature and top P are not sent along with a level to OpenAI and Anthropic, which reject them for reasoning. If a provider still rejects the setting, the request is repeated once without it, so a chat never fails because of it. Mainly useful to compare one model's latency with and without reasoning in the Latency Lab, which shows prompt, cached and reasoning tokens per turn.

Projects created before this setting existed keep their old choice (*On* became *Always*), and old project exports still import.

Whatever the setting, a reply's `<think>…</think>` and `<reasoning>…</reasoning>` blocks are cut out before the text reaches the avatar or the transcript (`ThinkTagGuard`), because some servers write reasoning into the reply text. An unterminated block is dropped to the end of the reply.

## Custom endpoints

For Ollama and OpenAI-compatible keys the endpoint can be anything on your own network (e.g. a school's LAN GPU box). The backend rejects only addresses that can never be a real LLM/TTS server: cloud metadata endpoints and this deployment's own Docker services (`rag`, `tts-local`, `docling`, …). `localhost` stays allowed for a local Ollama.

## Adding a provider

One entry in [`backend/app/core/providers.py`](../backend/app/core/providers.py); the frontend's provider list and validation are generated from it. Details: [backend/README.md](../backend/README.md#adding-an-ai-provider).
