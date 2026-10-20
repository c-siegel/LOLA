import hashlib
import io
import wave

import numpy as np
import pytest
import soundfile as sf

from app import synthesis
from app.config import Settings, settings
from app.synthesis import GATED_REV, POCKET_REV, VOICES_REV


def _wav() -> bytes:
    buffer = io.BytesIO()
    sf.write(buffer, np.zeros(16000, dtype=np.float32), 16000, format="WAV")
    return buffer.getvalue()


def _store_clip(client) -> str:
    content = _wav()
    sha = hashlib.sha256(content).hexdigest()
    assert client.put(f"/voices/{sha}", content=content).status_code == 204
    return sha


def _preset(language: str, name: str) -> str:
    return f"hf://kyutai/pocket-tts-without-voice-cloning/languages/{language}/embeddings/{name}.safetensors@{POCKET_REV}"


def test_synthesize_returns_wav_with_the_default_voice(client, pocket):
    response = client.post("/synthesize", json={"text": "Hallo!", "language": "de"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(response.content)) as wav:
        assert wav.getframerate() == 24000
    assert pocket.prompts == [_preset("german", "alba")]
    assert pocket.models[0].generated == [(_preset("german", "alba"), "Hallo!")]


def test_models_load_the_pinned_october_weights(client, pocket):
    client.post("/synthesize", json={"text": "Hello!", "language": "en"})

    config = pocket.models[0].config.read_text()
    assert f"pocket-tts-without-voice-cloning/languages/english/model.safetensors@{POCKET_REV}" in config
    assert f"hf://kyutai/pocket-tts/languages/english/model.safetensors@{GATED_REV}" in config
    # pocket-tts 3.3.0's own pins (September weights) are all replaced.
    assert "4e1e0a3e611c51c0b4ed8174fc10f32a54644303" not in config


@pytest.mark.parametrize("language", ["fr", "xx"])
def test_language_not_enabled_is_rejected(client, pocket, language):
    response = client.post("/synthesize", json={"text": "Bonjour", "language": language})

    assert response.status_code == 400
    assert pocket.models == []


def test_preset_voice_is_used(client, pocket):
    client.post("/synthesize", json={"text": "Hallo!", "language": "de", "voice": "estelle"})

    assert pocket.prompts == [_preset("german", "estelle")]


@pytest.mark.parametrize("voice", ["cosette", "jean", "juergen", "rafael", "alloy", "../alba"])
def test_voices_outside_the_allowlist_fall_back_to_the_default(client, pocket, voice):
    response = client.post("/synthesize", json={"text": "Hallo!", "language": "de", "voice": voice})

    assert response.status_code == 200
    assert pocket.prompts == [_preset("german", "alba")]


def test_voice_states_are_cached(client, pocket):
    for _ in range(3):
        client.post("/synthesize", json={"text": "Hallo!", "language": "de"})

    assert pocket.prompts == [_preset("german", "alba")]


def test_missing_clip_answers_voice_not_stored(client, pocket):
    response = client.post("/synthesize", json={"text": "Hallo!", "language": "de", "voice_sha256": "a" * 64})

    assert response.status_code == 404
    assert response.json()["detail"] == "VOICE_NOT_STORED"


def test_clip_without_cloning_speaks_with_the_requested_preset(client, pocket):
    sha = _store_clip(client)

    response = client.post("/synthesize", json={"text": "Hallo!", "language": "de", "voice": "anna", "voice_sha256": sha})

    assert response.status_code == 200
    assert pocket.prompts == [_preset("german", "anna")]


def test_clip_with_cloning_is_cloned_once_and_kept_as_a_state_file(client, with_token, monkeypatch):
    sha = _store_clip(client)

    client.post("/synthesize", json={"text": "Hallo!", "language": "de", "voice_sha256": sha})
    state_file = synthesis._state_file("de", sha)
    assert with_token.prompts == [str(synthesis._custom_voice_path(sha))]
    assert state_file.is_file()

    # After a restart (empty memory cache) the state file is loaded instead of cloning again.
    monkeypatch.setattr(synthesis, "_states", synthesis.OrderedDict())
    client.post("/synthesize", json={"text": "Hallo!", "language": "de", "voice_sha256": sha})
    assert with_token.prompts[-1] == str(state_file)


def test_deleting_a_clip_removes_its_cloned_states(client, with_token):
    sha = _store_clip(client)
    client.post("/synthesize", json={"text": "Hallo!", "language": "de", "voice_sha256": sha})

    assert client.delete(f"/voices/{sha}").status_code == 204

    assert not synthesis._custom_voice_path(sha).exists()
    assert not synthesis._state_file("de", sha).exists()
    assert not any(key[1] == sha for key in synthesis._states)


def test_donated_voice_is_cloned_from_the_pinned_recording(client, with_token):
    client.post("/synthesize", json={"text": "Hallo!", "language": "de", "voice": "donation:Selfie"})

    assert with_token.prompts == [f"hf://kyutai/tts-voices/voice-donations/Selfie_enhanced.wav@{VOICES_REV}"]


def test_donated_voice_without_cloning_falls_back_to_the_default(client, pocket):
    client.post("/synthesize", json={"text": "Hallo!", "language": "de", "voice": "donation:Selfie"})

    assert pocket.prompts == [_preset("german", "alba")]


def test_donated_voice_outside_the_configured_list_falls_back(client, with_token, monkeypatch):
    monkeypatch.setattr(settings, "donated_voices", "oldNerd")

    client.post("/synthesize", json={"text": "Hallo!", "language": "de", "voice": "donation:Selfie"})

    assert with_token.prompts == [_preset("german", "alba")]


def test_voices_without_cloning_lists_presets_only(client, pocket):
    body = client.get("/voices").json()

    assert body["languages"] == ["de", "en"]
    assert body["cloning"] is False
    ids = {voice["id"] for voice in body["voices"]}
    assert {"alba", "estelle", "bill_boerst"} <= ids
    assert not ids & {"cosette", "jean", "juergen", "rafael"}
    assert all(voice["kind"] == "preset" for voice in body["voices"])


def test_voices_with_cloning_lists_the_configured_donations(client, with_token, monkeypatch):
    monkeypatch.setattr(settings, "donated_voices", "Selfie, oldNerd")

    body = client.get("/voices").json()

    assert body["cloning"] is True
    donations = [voice for voice in body["voices"] if voice["kind"] == "donation"]
    assert [voice["id"] for voice in donations] == ["donation:Selfie", "donation:oldNerd"]
    assert all(voice["licence"] == "CC0" for voice in donations)


def test_token_without_access_to_the_gated_weights_turns_cloning_off(client, with_token):
    with_token.cloning = False  # the gated download failed, pocket-tts fell back to the ungated weights
    client.post("/synthesize", json={"text": "Hallo!", "language": "de"})

    assert client.get("/voices").json()["cloning"] is False


def test_clip_upload_with_wrong_hash_is_rejected(client, pocket):
    response = client.put(f"/voices/{'b' * 64}", content=_wav())

    assert response.status_code == 400


def test_invalid_language_setting_is_reported():
    with pytest.raises(ValueError, match="unsupported language"):
        _ = Settings(languages="de,klingon").language_codes
