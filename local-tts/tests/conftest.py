"""Shared fixtures for the sidecar tests.

Pocket TTS is faked at TTSModel.load_model, so nothing is downloaded and no model runs: the fake
records which voice sources it was asked for and returns silence. The cache directories point at
throwaway folders before app.config is imported (synthesis.py hands model_cache_dir to
huggingface_hub at import time), and no Hugging Face token from the environment leaks in.
"""

import os
import tempfile

os.environ["TTS_MODEL_CACHE_DIR"] = tempfile.mkdtemp(prefix="lola-tts-cache-")
os.environ["TTS_CUSTOM_VOICES_DIR"] = tempfile.mkdtemp(prefix="lola-tts-voices-")
os.environ["TTS_LANGUAGES"] = "de,en"
os.environ.pop("TTS_HF_TOKEN", None)
os.environ.pop("HF_TOKEN", None)

import json  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
import torch  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from pydantic import SecretStr  # noqa: E402

from app import synthesis  # noqa: E402
from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402


class FakeModel:
    sample_rate = 24000

    def __init__(self, config: Path, has_voice_cloning: bool):
        self.config = Path(config)
        self.has_voice_cloning = has_voice_cloning
        self.prompts: list[str] = []
        self.generated: list[tuple[str, str]] = []

    def get_state_for_audio_prompt(self, source, truncate=False):
        self.prompts.append(str(source))
        return {"source": str(source)}

    def generate_audio(self, state, text):
        self.generated.append((state["source"], text))
        return torch.zeros(2400)


class FakePocket:
    """Stands in for pocket-tts: `cloning` decides whether loaded models got the gated weights."""

    def __init__(self):
        self.cloning = False
        self.models: list[FakeModel] = []

    def load_model(self, config):
        model = FakeModel(config, self.cloning)
        self.models.append(model)
        return model

    @property
    def prompts(self) -> list[str]:
        return [prompt for model in self.models for prompt in model.prompts]


@pytest.fixture
def pocket(monkeypatch, tmp_path):
    fake = FakePocket()
    monkeypatch.setattr(synthesis.TTSModel, "load_model", staticmethod(lambda config: fake.load_model(config)))
    # The fake states aren't tensors; store them as JSON instead of safetensors.
    monkeypatch.setattr(synthesis, "export_model_state", lambda state, dest: Path(dest).write_text(json.dumps(state)))
    monkeypatch.setattr(settings, "custom_voices_dir", str(tmp_path / "custom-voices"))
    monkeypatch.setattr(settings, "hf_token", None)
    monkeypatch.setattr(settings, "donated_voices", "all")
    monkeypatch.setattr(synthesis, "_models", {})
    monkeypatch.setattr(synthesis, "_states", synthesis.OrderedDict())
    return fake


@pytest.fixture
def with_token(monkeypatch, pocket):
    """Cloning on: a token is configured and the models load with the gated weights."""
    monkeypatch.setattr(settings, "hf_token", SecretStr("hf_test"))
    pocket.cloning = True
    return pocket


@pytest.fixture
def client():
    # Not used as a context manager: the lifespan would start loading models in a thread.
    return TestClient(app)
