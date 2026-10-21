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

## Thinking setting

Projects on an OpenAI-compatible endpoint or GWDG Arcana get a **Thinking** setting (model default, on, off) in the configurator's technical step. It is sent as `chat_template_kwargs.enable_thinking`, which vLLM-style servers pass to the model's chat template; models that can't switch reasoning ignore it. Mainly useful to compare one model's latency with and without reasoning in the Latency Lab.

## Custom endpoints

For Ollama and OpenAI-compatible keys the endpoint can be anything on your own network (e.g. a school's LAN GPU box). The backend rejects only addresses that can never be a real LLM/TTS server: cloud metadata endpoints and this deployment's own Docker services (`rag`, `tts-local`, `docling`, …). `localhost` stays allowed for a local Ollama.

## Adding a provider

One entry in [`backend/app/core/providers.py`](../backend/app/core/providers.py); the frontend's provider list and validation are generated from it. Details: [backend/README.md](../backend/README.md#adding-an-ai-provider).
