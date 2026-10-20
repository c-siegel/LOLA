"""OnnxTextModel and the ONNX download, with a fake onnxruntime session and a tiny in-code tokenizer."""

from types import SimpleNamespace

import numpy as np
import pytest
from tokenizers import Tokenizer
from tokenizers.models import WordLevel
from tokenizers.pre_tokenizers import Whitespace
from tokenizers.processors import TemplateProcessing

from app import embedding

VOCAB = {"<pad>": 0, "<bos>": 1, "<eos>": 2, "<unk>": 3, "a": 4, "b": 5, "c": 6, "d": 7}


def make_tokenizer() -> Tokenizer:
    tokenizer = Tokenizer(WordLevel(VOCAB, unk_token="<unk>"))
    tokenizer.pre_tokenizer = Whitespace()
    tokenizer.post_processor = TemplateProcessing(
        single="<bos> $A <eos>", special_tokens=[("<bos>", 1), ("<eos>", 2)]
    )
    return tokenizer


class FakeSession:
    """Mimics EmbeddingGemma 2's graph: media inputs, sentence_embedding as the second output."""

    def __init__(self, extra_inputs=("image_features", "video_features", "audio_features")):
        names = ["input_ids", "attention_mask", *extra_inputs]
        self._inputs = [SimpleNamespace(name=n, shape=["batch", "seq"] if n in names[:2] else ["rows", 512]) for n in names]
        self.calls = []

    def get_inputs(self):
        return self._inputs

    def run(self, output_names, feeds):
        self.calls.append((output_names, feeds))
        ids, mask = feeds["input_ids"], feeds["attention_mask"]
        # Sum of the real tokens' ids: changes if padding leaks into the result.
        pooled = (ids * mask).sum(axis=1, keepdims=True).astype(np.float32)
        return [np.hstack([pooled, mask.sum(axis=1, keepdims=True).astype(np.float32)])]


def test_batches_pad_with_the_pad_token_and_mask_it_out():
    session = FakeSession()
    model = embedding.OnnxTextModel(session, make_tokenizer(), max_tokens=64)

    vectors = list(model.embed(["a", "a b c", "d"], batch_size=2))

    assert [v.tolist() for v in vectors] == [[7.0, 3.0], [18.0, 5.0], [10.0, 3.0]]
    assert len(session.calls) == 2
    output_names, feeds = session.calls[0]
    assert output_names == ["sentence_embedding"]
    assert feeds["input_ids"].tolist() == [[1, 4, 2, 0, 0], [1, 4, 5, 6, 2]]
    assert feeds["attention_mask"].tolist() == [[1, 1, 1, 0, 0], [1, 1, 1, 1, 1]]


def test_media_inputs_get_zero_rows():
    session = FakeSession()
    list(embedding.OnnxTextModel(session, make_tokenizer(), max_tokens=64).embed(["a"]))

    _, feeds = session.calls[0]
    for name in ("image_features", "video_features", "audio_features"):
        assert feeds[name].shape == (0, 512)
        assert feeds[name].dtype == np.float32


def test_long_input_is_truncated_to_max_tokens():
    session = FakeSession()
    list(embedding.OnnxTextModel(session, make_tokenizer(), max_tokens=4).embed(["a b c d a b"]))

    assert session.calls[0][1]["input_ids"].shape == (1, 4)


def test_unknown_graph_input_is_refused():
    with pytest.raises(RuntimeError, match="position_ids"):
        embedding.OnnxTextModel(FakeSession(extra_inputs=("position_ids",)), make_tokenizer(), max_tokens=64)


def test_download_fetches_only_the_pinned_listed_files(monkeypatch):
    import huggingface_hub

    fetched = []

    def fake_download(repo_id, filename, *, revision, cache_dir):
        fetched.append((repo_id, filename, revision, cache_dir))
        return f"{cache_dir}/{filename}"

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", fake_download)
    source = embedding.OnnxSource(
        revision="a" * 40, graph="onnx/model_quantized.onnx", sidecars=("onnx/model_quantized.onnx_data",)
    )

    graph, tokenizer = embedding._download_onnx_model("org/model", source, "/cache")

    assert (graph, tokenizer) == ("/cache/onnx/model_quantized.onnx", "/cache/tokenizer.json")
    assert fetched == [
        ("org/model", "onnx/model_quantized.onnx", "a" * 40, "/cache"),
        ("org/model", "onnx/model_quantized.onnx_data", "a" * 40, "/cache"),
        ("org/model", "tokenizer.json", "a" * 40, "/cache"),
    ]


GEMMA = "onnx-community/embeddinggemma-2-ONNX"


def test_embeddinggemma_is_pinned_to_the_q8_text_graph_only():
    source = embedding.LOCAL_MODELS[GEMMA].onnx

    assert source is not None
    assert len(source.revision) == 40 and int(source.revision, 16) >= 0
    assert source.graph == "onnx/model_quantized.onnx"
    assert source.sidecars == ("onnx/model_quantized.onnx_data",)
    # Never the vision/audio encoders, which the text embedding doesn't need.
    assert not any("encoder" in f for f in (source.graph, *source.sidecars))


def test_embeddinggemma_can_be_configured(monkeypatch):
    from app.config import settings
    from app.schemas import EmbeddingConfig

    monkeypatch.setattr(settings, "rag_local_embedding_model", GEMMA)

    spec = embedding.local_model_spec()

    assert (spec.dimensions, spec.licence) == (768, "Apache-2.0")
    assert embedding.chunk_size_for(EmbeddingConfig(mode="local", model=GEMMA)) == (1400, 200)


def test_embeddinggemma_texts_get_its_retrieval_prefixes():
    class RecordingModel:
        def __init__(self):
            self.texts = []

        def embed(self, texts, batch_size=16):
            self.texts.extend(texts)
            return [[1.0, 0.0] for _ in texts]

    model = RecordingModel()
    embedder = embedding.LocalEmbedder(GEMMA, embedding.LOCAL_MODELS[GEMMA], model)

    embedder.embed_passages(["Die Zelle"])
    embedder.embed_query("Was ist eine Zelle?")

    assert model.texts == ["title: none | text: Die Zelle", "task: search result | query: Was ist eine Zelle?"]
