"""
Embeddings: Local First, API Optional

Local (default): fastembed runs an ONNX model on the CPU; models fastembed can't load run
through OnnxTextModel instead (onnxruntime directly). Only models on the allowlist below
can be configured — each one's licence has been checked to be permissive, and fastembed also
supports non-commercial models (e.g. jina-embeddings-v3, CC-BY-NC-4.0) that must never be
picked by accident. API: any embedding model litellm can call, with the teacher's own key,
passed in per request and never stored.

A knowledge base is embedded with exactly one model for its lifetime (vectors from different
models aren't comparable), so every request names the model and app/store.py rejects a mismatch.

How to use:
    embedder = get_embedder(config)          # EmbeddingConfig from the request
    vectors = embedder.embed_passages(["…", "…"])
    query_vector = embedder.embed_query("…")
"""

import logging
import math
import threading
from dataclasses import dataclass
from typing import Protocol

from app import errors
from app.config import settings
from app.errors import RagError
from app.schemas import EmbeddingConfig

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OnnxSource:
    """
    Where to get a model fastembed can't load (see OnnxTextModel). Pinned to a commit so a
    force-pushed upstream repo can't change the vectors under existing knowledge bases, and listed
    file by file so only the text graph is downloaded, never e.g. a multimodal model's encoders.
    """

    revision: str
    graph: str
    sidecars: tuple[str, ...] = ()
    tokenizer: str = "tokenizer.json"
    max_tokens: int = 8192


@dataclass(frozen=True)
class LocalModelSpec:
    dimensions: int
    licence: str
    # Chunk size in characters (app/chunking.py) that stays inside the model's input limit.
    chunk_size: int
    chunk_overlap: int
    query_prefix: str = ""
    passage_prefix: str = ""
    # None: a model fastembed supports, loaded by its name.
    onnx: OnnxSource | None = None


LOCAL_MODELS: dict[str, LocalModelSpec] = {
    # German/English bilingual, 8192-token input, so chunks are never truncated. The default.
    "jinaai/jina-embeddings-v2-base-de": LocalModelSpec(768, "Apache-2.0", 1400, 200),
    # Strongest multilingual option, about 3x slower on a CPU; trained with these prefixes.
    "intfloat/multilingual-e5-large": LocalModelSpec(
        1024, "MIT", 1400, 200, query_prefix="query: ", passage_prefix="passage: "
    ),
    # Many languages, but truncates at 128 tokens (~500 characters) — hence the small chunks.
    "sentence-transformers/paraphrase-multilingual-mpnet-base-v2": LocalModelSpec(768, "Apache-2.0", 450, 80),
    # Google's EmbeddingGemma 2 (Apache-2.0) in the onnx-community export: 100+ languages, 8192
    # tokens, ~314 MB. q8, because q4/q4f16 drift too far from fp32 for stored vectors and the
    # card warns about fp16 activations. Prefixes are the model's own retrieval prompts.
    "onnx-community/embeddinggemma-2-ONNX": LocalModelSpec(
        768,
        "Apache-2.0",
        1400,
        200,
        query_prefix="task: search result | query: ",
        passage_prefix="title: none | text: ",
        onnx=OnnxSource(
            revision="daa72c51243991dfcaf9f9137d2c573d8f7790c0",
            graph="onnx/model_quantized.onnx",
            sidecars=("onnx/model_quantized.onnx_data",),
        ),
    ),
}

# Chunk size for API models: every current provider model accepts far more than this.
API_CHUNK_SIZE, API_CHUNK_OVERLAP = 1400, 200

_API_BATCH_SIZE = 64
_LOCAL_BATCH_SIZE = 16


class Embedder(Protocol):
    model_id: str

    def embed_passages(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def _normalized(vector) -> list[float]:
    values = [float(v) for v in vector]
    norm = math.sqrt(sum(v * v for v in values)) or 1.0
    return [v / norm for v in values]


class LocalEmbedder:
    def __init__(self, model_id: str, spec: LocalModelSpec, model) -> None:
        self.model_id = model_id
        self._spec = spec
        self._model = model

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        prefixed = [self._spec.passage_prefix + t for t in texts]
        return [_normalized(v) for v in self._model.embed(prefixed, batch_size=_LOCAL_BATCH_SIZE)]

    def embed_query(self, text: str) -> list[float]:
        return _normalized(next(iter(self._model.embed([self._spec.query_prefix + text]))))


def _field(item, name: str):
    # litellm returns plain dicts for some providers and pydantic objects for others.
    return item[name] if isinstance(item, dict) else getattr(item, name)


# Sent instead of a missing key, see ApiEmbedder._call. Same value as the backend sends.
KEYLESS_API_KEY = "no-key"


class ApiEmbedder:
    def __init__(self, config: EmbeddingConfig) -> None:
        if config.api_key is None and not config.api_base:
            raise RagError(errors.EMBEDDING_KEY_MISSING)
        self.model_id = config.model
        self._config = config

    def _call(self, texts: list[str]) -> list[list[float]]:
        import litellm

        # Never None: litellm would then read the key from this service's environment (e.g.
        # OPENAI_API_KEY) and send it to the teacher's endpoint. Keyless servers ignore the placeholder.
        api_key = self._config.api_key.get_secret_value() if self._config.api_key is not None else KEYLESS_API_KEY
        kwargs = {"model": self._config.model, "input": texts, "api_key": api_key}
        if self._config.api_base:
            kwargs["api_base"] = self._config.api_base
        try:
            response = litellm.embedding(**kwargs)
        except Exception as exc:
            # The provider's message can echo request details; only the type goes to the log.
            logger.warning("API embedding failed (%s): %s", self._config.model, type(exc).__name__)
            raise RagError(errors.EMBEDDING_FAILED, status_code=502) from exc
        items = sorted(response.data, key=lambda item: _field(item, "index"))
        return [_normalized(_field(item, "embedding")) for item in items]

    def embed_passages(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(texts), _API_BATCH_SIZE):
            vectors.extend(self._call(texts[start : start + _API_BATCH_SIZE]))
        return vectors

    def embed_query(self, text: str) -> list[float]:
        return self._call([text])[0]


class OnnxTextModel:
    """
    Runs an ONNX text-embedding graph directly, for models fastembed can't load: fastembed only
    feeds input_ids / attention_mask and takes the first output, while e.g. EmbeddingGemma 2's
    graph also requires image/video/audio feature inputs and puts the pooled sentence embedding
    second. Same embed() interface as fastembed's TextEmbedding, so LocalEmbedder can't tell.
    """

    OUTPUT = "sentence_embedding"

    def __init__(self, session, tokenizer, max_tokens: int) -> None:
        self._session = session
        self._tokenizer = tokenizer
        self._tokenizer.enable_truncation(max_tokens)
        pad_id = tokenizer.token_to_id("<pad>")
        self._pad_id = 0 if pad_id is None else pad_id
        # A multimodal graph embeds text alone when every media input gets zero rows.
        self._media_inputs = {i.name: i.shape[1] for i in session.get_inputs() if i.name.endswith("_features")}
        unknown = {i.name for i in session.get_inputs()} - {"input_ids", "attention_mask", *self._media_inputs}
        if unknown:
            raise RuntimeError(f"ONNX embedding graph needs inputs this loader can't fill: {sorted(unknown)}")

    def embed(self, texts, batch_size: int = 16):
        import numpy as np

        texts = list(texts)
        for start in range(0, len(texts), batch_size):
            encodings = self._tokenizer.encode_batch(texts[start : start + batch_size])
            width = max(len(e.ids) for e in encodings)
            ids = np.full((len(encodings), width), self._pad_id, dtype=np.int64)
            mask = np.zeros((len(encodings), width), dtype=np.int64)
            for row, encoding in enumerate(encodings):
                ids[row, : len(encoding.ids)] = encoding.ids
                mask[row, : len(encoding.ids)] = 1
            feeds = {"input_ids": ids, "attention_mask": mask}
            for name, feature_size in self._media_inputs.items():
                feeds[name] = np.zeros((0, feature_size), dtype=np.float32)
            yield from self._session.run([self.OUTPUT], feeds)[0]


def _download_onnx_model(model_id: str, source: OnnxSource, cache_dir: str) -> tuple[str, str]:
    """Fetch the pinned files (a no-op once cached); returns the graph's and tokenizer's paths."""
    from huggingface_hub import hf_hub_download

    def fetch(filename: str) -> str:
        return hf_hub_download(model_id, filename, revision=source.revision, cache_dir=cache_dir)

    graph = fetch(source.graph)
    # Sidecars (external weights) land next to the graph in the snapshot folder, where onnxruntime looks.
    for sidecar in source.sidecars:
        fetch(sidecar)
    return graph, fetch(source.tokenizer)


def preload_local_model(model_id: str, cache_dir: str) -> None:
    """Download a local model into cache_dir without loading it (docker/rag.Dockerfile's preload)."""
    spec = LOCAL_MODELS[model_id]
    if spec.onnx is not None:
        _download_onnx_model(model_id, spec.onnx, cache_dir)
    else:
        from fastembed import TextEmbedding

        TextEmbedding(model_name=model_id, cache_dir=cache_dir)


_local_lock = threading.Lock()
_local_embedder: LocalEmbedder | None = None


def _load_local_model(model_id: str):
    """Load the local model (downloads into the cache on first use). Faked in tests."""
    spec = LOCAL_MODELS[model_id]
    if spec.onnx is not None:
        import onnxruntime
        from tokenizers import Tokenizer

        graph, tokenizer = _download_onnx_model(model_id, spec.onnx, settings.model_cache_dir)
        options = onnxruntime.SessionOptions()
        if settings.rag_embedding_threads:
            options.intra_op_num_threads = settings.rag_embedding_threads
        session = onnxruntime.InferenceSession(graph, options, providers=["CPUExecutionProvider"])
        return OnnxTextModel(session, Tokenizer.from_file(tokenizer), spec.onnx.max_tokens)

    from fastembed import TextEmbedding

    return TextEmbedding(
        model_name=model_id, cache_dir=settings.model_cache_dir, threads=settings.rag_embedding_threads
    )


def local_model_spec() -> LocalModelSpec:
    spec = LOCAL_MODELS.get(settings.rag_local_embedding_model)
    if spec is None:
        raise RuntimeError(
            f"RAG_LOCAL_EMBEDDING_MODEL={settings.rag_local_embedding_model!r} is not on the allowlist "
            f"in app/embedding.py: {', '.join(LOCAL_MODELS)}"
        )
    return spec


def local_model_loaded() -> bool:
    return _local_embedder is not None


def get_local_embedder() -> LocalEmbedder:
    global _local_embedder
    with _local_lock:
        if _local_embedder is None:
            model_id = settings.rag_local_embedding_model
            _local_embedder = LocalEmbedder(model_id, local_model_spec(), _load_local_model(model_id))
        return _local_embedder


def get_embedder(config: EmbeddingConfig) -> Embedder:
    if config.mode == "local":
        # Only the one configured local model is ever loaded (each costs hundreds of MB of RAM).
        # A KB built with a model the operator has since switched away from can't be queried
        # until it's re-created — the dashboard shows this error.
        if config.model != settings.rag_local_embedding_model:
            raise RagError(errors.EMBEDDING_MODEL_NOT_ALLOWED)
        return get_local_embedder()
    return ApiEmbedder(config)


def chunk_size_for(config: EmbeddingConfig) -> tuple[int, int]:
    if config.mode == "local":
        spec = LOCAL_MODELS[config.model]
        return spec.chunk_size, spec.chunk_overlap
    return API_CHUNK_SIZE, API_CHUNK_OVERLAP
