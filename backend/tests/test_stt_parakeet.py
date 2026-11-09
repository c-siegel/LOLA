"""Tests for the server-side Parakeet engine (app/features/ai/stt/parakeet_local.py): that it is
the local engine, that missing model files are an error rather than a silent fallback, and the
pure helpers that cut long audio and turn tokens into text. The last test runs the real model,
but only where its files have been downloaded (scripts/fetch-stt-model.sh) — CI skips it."""

from pathlib import Path

import numpy as np
import pytest

from app.core.config import settings
from app.features.ai.stt import get_stt_client, parakeet_local
from app.features.ai.stt.parakeet_local import (
    FRAME_SAMPLES,
    LocalParakeetClient,
    SttModelMissing,
    chunk_boundaries,
    detokenize,
)
from app.features.ai.stt.saia import SaiaClient
from app.features.api_keys.models import UserApiKey

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_DIR = REPO_ROOT / "models" / "parakeet-redux" / "v1"
SAMPLE_CLIP = REPO_ROOT / "local-tts" / "voices" / "de_ai.wav"


@pytest.fixture(autouse=True)
def _fresh_model_cache(monkeypatch):
    # The loaded model is cached per process — each test decides for itself whether it exists.
    monkeypatch.setattr(parakeet_local, "_loaded_model", None)
    monkeypatch.setattr(parakeet_local, "_missing_model_logged", False)


def test_without_a_key_the_local_client_transcribes() -> None:
    assert isinstance(get_stt_client(None), LocalParakeetClient)


def test_model_available_checks_for_the_manifest(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "stt_parakeet_model_dir", str(tmp_path))
    assert not parakeet_local.model_available()
    (tmp_path / "manifest.json").write_text("{}")
    assert parakeet_local.model_available()


def test_a_cloud_key_wins_over_the_local_model() -> None:
    key = UserApiKey(user_id="u", provider="gwdg_saia", encrypted_api_key="x", masked_key="x")
    assert isinstance(get_stt_client(key), SaiaClient)


def test_missing_model_files_raise_instead_of_falling_back(monkeypatch, tmp_path) -> None:
    # Regression: before faster-whisper was removed, missing files silently switched to Whisper.
    monkeypatch.setattr(settings, "stt_parakeet_model_dir", str(tmp_path))
    with pytest.raises(SttModelMissing):
        LocalParakeetClient().transcribe(b"audio", "de")


def test_long_audio_is_cut_at_the_quietest_moment() -> None:
    max_samples = 100 * FRAME_SAMPLES
    total = 250 * FRAME_SAMPLES
    probabilities = np.ones(250, dtype=np.float32)
    probabilities[80] = 0.0  # a pause inside the allowed window (frames 60-100) of chunk one
    cuts = chunk_boundaries(probabilities, total, max_samples)

    assert cuts[0] == 80 * FRAME_SAMPLES
    bounds = [0, *cuts, total]
    assert all(0 < b - a <= max_samples for a, b in zip(bounds, bounds[1:]))


def test_short_audio_is_not_cut() -> None:
    assert chunk_boundaries(np.ones(10, dtype=np.float32), 10 * FRAME_SAMPLES, 100 * FRAME_SAMPLES) == []


def test_detokenize_joins_words_and_drops_special_tokens() -> None:
    vocab = ["<unk>", "<|nospeech|>", "▁Hallo", "▁Wel", "t", "."]
    assert detokenize([1, 2, 3, 4, 5, 0], vocab) == "Hallo Welt."


@pytest.mark.skipif(
    not (MODEL_DIR / "manifest.json").is_file() or not SAMPLE_CLIP.is_file(),
    reason="needs the downloaded model (scripts/fetch-stt-model.sh) and a local sample clip",
)
def test_transcribes_real_speech(monkeypatch) -> None:
    monkeypatch.setattr(settings, "stt_parakeet_model_dir", str(MODEL_DIR))
    text = LocalParakeetClient().transcribe(SAMPLE_CLIP.read_bytes(), "de")
    assert "herzlich willkommen" in text.lower()
