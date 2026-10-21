# Attribution and licences

LOLA itself is **MIT**-licensed ([LICENSE](LICENSE)). It builds on the open-source components, models and fonts below. This page lists what LOLA uses directly; the in-app version is the Credits page (`/credits`).

Only permissive licences are accepted (MIT, BSD, Apache-2.0, ISC, PSF, MPL-2.0 unmodified; CC BY 4.0 for model weights and voices when credited). For Python packages, including transitive ones, [`scripts/check-licences.sh`](scripts/check-licences.sh) enforces this on every release build.

## Avatar, speech and gestures

| Component | Used for | Author | Licence |
|---|---|---|---|
| [TalkingHead](https://github.com/met4citizen/TalkingHead) | 3D avatar rendering and animation | Mika Suominen | MIT |
| [HeadTTS](https://github.com/met4citizen/HeadTTS) | Source of the bundled avatar models Julia and David ([details](frontend/public/avatars/ATTRIBUTION.md)) | Mika Suominen | MIT |
| [HeadAudio](https://github.com/met4citizen/HeadAudio) | Real-time lip-sync from audio | Mika Suominen | MIT |
| [MotionEngine](https://github.com/lhupyn/motion-engine) | Avatar gestures; vendored without its webcam feature ([details](frontend/src/vendor/motion-engine/ATTRIBUTION.md)) | lhupyn | MIT |
| [Parakeet Redux](https://huggingface.co/moondream/parakeet-redux) ([ONNX export](https://huggingface.co/mrfakename/parakeet-redux-ONNX)) | On-device speech recognition model | NVIDIA, Moondream, eschmidbauer, mrfakename | CC BY 4.0 |
| [Pocket TTS](https://github.com/kyutai-labs/pocket-tts) (weights: [kyutai/pocket-tts](https://huggingface.co/kyutai/pocket-tts), [voices](https://huggingface.co/kyutai/tts-voices), ONNX exports [Lauro00000/k6-onnx-int8](https://huggingface.co/Lauro00000/k6-onnx-int8) and [c-siegel/pocket-tts-en-de-onnx-int8](https://huggingface.co/c-siegel/pocket-tts-en-de-onnx-int8)) | Local and on-device speech output | Kyutai Labs | Code MIT, weights and voices CC BY 4.0 |
| [normt2s](https://github.com/c-siegel/normt2s) | Turns reply text into speakable text | c-siegel | MIT |
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | Server-side speech recognition fallback | SYSTRAN | MIT |
| [ONNX Runtime](https://github.com/microsoft/onnxruntime) and ONNX Runtime Web | Running the speech models | Microsoft | MIT |
| [Tokenizers.js](https://github.com/huggingface/tokenizers.js) | Tokenizing for on-device speech | Hugging Face | Apache-2.0 |

## Knowledge base and evaluation

| Component | Used for | Licence |
|---|---|---|
| [fastembed](https://github.com/qdrant/fastembed) | Local embedding runtime | Apache-2.0 |
| [jina-embeddings-v2-base-de](https://huggingface.co/jinaai/jina-embeddings-v2-base-de), [paraphrase-multilingual-mpnet-base-v2](https://huggingface.co/sentence-transformers/paraphrase-multilingual-mpnet-base-v2), [EmbeddingGemma 2](https://huggingface.co/onnx-community/embeddinggemma-2-ONNX) | Selectable local embedding models | Apache-2.0 |
| [multilingual-e5-large](https://huggingface.co/intfloat/multilingual-e5-large) | Selectable local embedding model | MIT |
| [sqlite-vec](https://github.com/asg017/sqlite-vec) | Vector search in SQLite | MIT / Apache-2.0 (dual) |
| [pypdf](https://github.com/py-pdf/pypdf), [lxml](https://lxml.de/) | Parsing PDF and Word uploads | BSD-3-Clause |
| [docling-serve](https://github.com/docling-project/docling-serve) (optional container) | Layout-aware parsing and OCR | MIT |
| [Ragas](https://github.com/explodinggradients/ragas) | Answer-quality scoring | Apache-2.0 |
| [LangChain Community](https://github.com/langchain-ai/langchain), [instructor](https://github.com/567-labs/instructor) | Ragas dependencies in `rag-eval/` | MIT |
| [Hugging Face Hub](https://github.com/huggingface/huggingface_hub), [tokenizers](https://github.com/huggingface/tokenizers) | Model download and tokenizing | Apache-2.0 |

## Backend (Python)

| Package | Licence |
|---|---|
| [FastAPI](https://github.com/fastapi/fastapi), [SQLModel](https://github.com/fastapi/sqlmodel), [SQLAlchemy](https://www.sqlalchemy.org/), [Alembic](https://github.com/sqlalchemy/alembic), [Pydantic Settings](https://github.com/pydantic/pydantic-settings), [AnyIO](https://github.com/agronholm/anyio), [PyJWT](https://github.com/jpadilla/pyjwt), [PyYAML](https://pyyaml.org/) | MIT |
| [litellm](https://github.com/BerriAI/litellm) (provider-agnostic LLM and TTS client) | MIT |
| [Uvicorn](https://www.uvicorn.org/), [HTTPX](https://www.python-httpx.org/), [PyAV](https://github.com/PyAV-Org/PyAV), [NumPy](https://numpy.org/), [soundfile](https://github.com/bastibe/python-soundfile) | BSD-3-Clause |
| [bcrypt](https://github.com/pyca/bcrypt), [python-multipart](https://github.com/Kludex/python-multipart) | Apache-2.0 |
| [cryptography](https://github.com/pyca/cryptography) | Apache-2.0 OR BSD-3-Clause |
| [email-validator](https://github.com/JoshData/python-email-validator) | Unlicense |
| [normt2s](https://github.com/c-siegel/normt2s) | MIT |

## Frontend (JavaScript)

| Package | Licence |
|---|---|
| [React](https://react.dev/), [React Router](https://reactrouter.com/), [react-i18next](https://github.com/i18next/react-i18next), [i18next](https://www.i18next.com/) and its language detector, [TanStack Query](https://tanstack.com/query), [react-markdown](https://github.com/remarkjs/react-markdown), remark-gfm, remark-math, rehype-katex, [KaTeX](https://katex.org/), [three.js](https://threejs.org/), [Vite](https://vite.dev/) | MIT |
| [Lucide React](https://lucide.dev/) (icons) | ISC |

## Fonts

Self-hosted (no Google Fonts requests). All are variable fonts under the **SIL Open Font License 1.1** ([details](frontend/public/fonts/ATTRIBUTION.md)): [Inter](https://github.com/rsms/inter), [Plus Jakarta Sans](https://github.com/tokotype/PlusJakartaSans), [JetBrains Mono](https://github.com/JetBrains/JetBrainsMono). The LOLA brand marks ([docs/lola-icon-svgs/](docs/lola-icon-svgs/README.txt)) use outlines of Plus Jakarta Sans ExtraBold and Inter SemiBold.

## Deployment

[Caddy](https://caddyserver.com/) (Apache-2.0) serves the frontend and proxies the API. [Docker](https://www.docker.com/) and Compose run the containers.

## Notes

- AI providers (Anthropic, OpenAI, Google, Mistral, Cartesia, GWDG and others) are external services reached with the operator's own API keys. Their terms apply to that traffic; see [docs/ai-providers.md](docs/ai-providers.md).

