"""
Pocket TTS Model Loading And Synthesis

Loads one Kyutai Pocket TTS model per configured language (Settings.languages) and turns text into
WAV audio. The voice is a preset (a precomputed voice state, see app/voices.py), a donated voice, or
a teacher's own clip sent by the backend and stored by its SHA-256 hash (see store_voice). The last
two are cloned, which needs the gated weights (Settings.hf_token); without them those requests fall
back to the language's default preset, so speech never fails over a voice.

How to use:
    from app.synthesis import synthesize

    audio_bytes = synthesize("Hallo, wie kann ich helfen?", "de")
    audio_bytes = synthesize("Hallo!", "de", voice="estelle")
    audio_bytes = synthesize("Hallo!", "de", voice_sha256="3f2a...")  # a stored custom clip
"""

import hashlib
import io
import logging
import os
import re
import threading
from collections import OrderedDict
from pathlib import Path

import numpy as np
import soundfile as sf

from app.config import settings
from app.voices import DONATION_PREFIX, LANGUAGES, PRESETS, default_voice, donated_name

# huggingface_hub reads its cache location once, when it's imported (through pocket_tts below).
os.environ["HF_HUB_CACHE"] = settings.model_cache_dir
if settings.hf_token:
    # Read by huggingface_hub on every download — what lets pocket_tts fetch the gated weights.
    os.environ["HF_TOKEN"] = settings.hf_token.get_secret_value()

import torch  # noqa: E402
from pocket_tts import TTSModel, export_model_state  # noqa: E402
from pocket_tts.utils.config import CONFIGS_DIR  # noqa: E402

# Importing pocket_tts sets this to 1 process-wide; make it an explicit setting instead.
torch.set_num_threads(max(1, settings.torch_threads))

logger = logging.getLogger(__name__)

# pocket-tts 3.3.0 pins its weights to 2026-09-24. These revisions carry Kyutai's October
# retrains (German, French, Italian, Spanish, Portuguese on 2026-10-01, English on 2026-10-11) with
# the same tokenizers and architecture, and the matching preset voice states — a state only fits
# the weights it was computed with. The browser engine (scripts/fetch-tts-model.sh) runs the same
# German weights.
POCKET_REV = "a3055dcb533cf2ec90458008a01e7cef550bae2a"  # kyutai/pocket-tts-without-voice-cloning
GATED_REV = "a72dba0d1a5f1387c104f9d3e17abb8ab60b4fae"  # kyutai/pocket-tts (with the voice encoder)
VOICES_REV = "323332d33f997de8394f24a193e1a76df720e01a"  # kyutai/tts-voices (donated recordings)

# Bounds how many synthesis calls run at once — see Settings.max_concurrent_synthesis for why a
# BoundedSemaphore (not a plain lock) instead of just serializing everything.
_synthesis_slots = threading.BoundedSemaphore(max(1, settings.max_concurrent_synthesis))

# Silence appended to every synthesized clip, see _wav_bytes.
TAIL_SILENCE_SECONDS = 0.4

# Also what keeps a hash from being used as a path (no "/" or ".." can get through).
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Voice states kept in memory (a few MB each). Presets of the configured languages always fit;
# cloned ones beyond this are reloaded from their file in custom_voices_dir.
_STATE_CACHE_SIZE = 64


class UnsupportedLanguageError(ValueError):
    """Raised when the requested language isn't one of Settings.languages."""


class VoiceNotStoredError(LookupError):
    """Raised when a custom voice hasn't been sent to this service yet — the backend then sends
    it (PUT /voices/{sha256}) and retries."""


class InvalidVoiceError(ValueError):
    """Raised when an uploaded clip doesn't match its hash or isn't readable audio."""


# ==================== MODELS ====================

_models: dict[str, TTSModel] = {}
_model_locks = {code: threading.Lock() for code in LANGUAGES}


def _pinned_config(pocket_name: str) -> Path:
    """pocket-tts' own config for the language, with every Hugging Face path moved to the pinned
    revisions above. Written next to the model cache; architecture and text rules stay pocket-tts'."""
    text = (CONFIGS_DIR / f"{pocket_name}.yaml").read_text(encoding="utf-8")
    text = re.sub(r"(hf://kyutai/pocket-tts-without-voice-cloning/[^@\s]+)@\w+", rf"\g<1>@{POCKET_REV}", text)
    text = re.sub(r"(hf://kyutai/pocket-tts/[^@\s]+)@\w+", rf"\g<1>@{GATED_REV}", text)
    path = Path(settings.model_cache_dir) / "configs" / f"{pocket_name}.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def model(language: str) -> TTSModel:
    """The model for `language`, loaded (and its weights downloaded) on first use."""
    if language not in settings.language_codes:
        raise UnsupportedLanguageError(
            f"Language '{language}' is not enabled here (TTS_LANGUAGES={settings.languages!r})."
        )
    with _model_locks[language]:
        if language not in _models:
            pocket_name = LANGUAGES[language]
            logger.info("Loading Pocket TTS model for %s", pocket_name)
            loaded = TTSModel.load_model(config=_pinned_config(pocket_name))
            if settings.hf_token and not loaded.has_voice_cloning:
                # pocket-tts silently falls back to the ungated weights when the gated download fails.
                logger.warning("TTS_HF_TOKEN is set, but the gated weights for %s couldn't be loaded — "
                               "has this token's account accepted the terms of kyutai/pocket-tts?", pocket_name)
            _models[language] = loaded
        return _models[language]


def load_all() -> None:
    """Load every configured language now (at startup), so the first request isn't the slow one."""
    for language in settings.language_codes:
        try:
            model(language)
        except Exception:
            logger.exception("Loading the Pocket TTS model for '%s' failed; retried on its first request", language)


def cloning_available() -> bool:
    """Whether voice cloning works: a token is configured, and no loaded model fell back to the
    ungated weights. Before any model has loaded, the token alone decides."""
    if not settings.hf_token:
        return False
    return all(m.has_voice_cloning for m in list(_models.values()))


# ==================== VOICES ====================

_states: OrderedDict[tuple[str, str], dict] = OrderedDict()
_states_lock = threading.Lock()


def _cached_state(language: str, key: str, load) -> dict:
    with _states_lock:
        if (language, key) in _states:
            _states.move_to_end((language, key))
            return _states[(language, key)]
    state = load()
    with _states_lock:
        _states[(language, key)] = state
        while len(_states) > _STATE_CACHE_SIZE:
            _states.popitem(last=False)
    return state


def _preset_state(language: str, name: str) -> dict:
    pocket_name = LANGUAGES[language]
    source = f"hf://kyutai/pocket-tts-without-voice-cloning/languages/{pocket_name}/embeddings/{name}.safetensors@{POCKET_REV}"
    return _cached_state(language, name, lambda: model(language).get_state_for_audio_prompt(source))


def _state_file(language: str, key: str) -> Path:
    return Path(settings.custom_voices_dir) / "states" / language / f"{key}.safetensors"


def _cloned_state(language: str, key: str, audio: str | Path) -> dict:
    """A voice state cloned from `audio`, kept as a file so cloning (a few seconds) happens once per
    language and voice. Callers check cloning_available() first."""

    def load() -> dict:
        path = _state_file(language, key)
        if not path.is_file():
            state = model(language).get_state_for_audio_prompt(audio, truncate=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(".part")
            export_model_state(state, temp)
            os.replace(temp, path)
            return state
        return model(language).get_state_for_audio_prompt(path)

    return _cached_state(language, key, load)


def _voice_state(language: str, voice: str | None, voice_sha256: str | None) -> dict:
    if voice_sha256:
        clip = _custom_voice_path(voice_sha256)
        if not clip.is_file():
            raise VoiceNotStoredError(voice_sha256)
        if cloning_available():
            return _cloned_state(language, voice_sha256, clip)
        logger.warning("Voice cloning is off (no usable TTS_HF_TOKEN) — speaking a custom clip's text with a preset voice")
    if voice in PRESETS:
        return _preset_state(language, voice)
    name = donated_name(voice) if voice else None
    allowed = settings.donated_voice_names
    if name and (allowed is None or name in allowed):
        if cloning_available():
            key = "donation-" + hashlib.sha256(name.encode()).hexdigest()[:16]
            return _cloned_state(language, key, f"hf://kyutai/tts-voices/voice-donations/{name}_enhanced.wav@{VOICES_REV}")
        logger.warning("Voice cloning is off — donated voice %r replaced by the default preset", voice)
    elif voice and not voice.startswith(DONATION_PREFIX):
        # E.g. a cloud provider's voice name left over after the project switched to local TTS.
        logger.info("Unknown voice %r — using the default preset", voice)
    return _preset_state(language, default_voice(language))


# ==================== CUSTOM CLIPS ====================


def _custom_voice_path(sha256: str) -> Path:
    if not _SHA256_RE.match(sha256):
        raise InvalidVoiceError("Voice id must be a lowercase SHA-256 hex digest.")
    return Path(settings.custom_voices_dir) / f"{sha256}.wav"


def store_voice(sha256: str, content: bytes) -> None:
    """Keep a custom reference clip under its hash, after checking the hash and that it's audio."""
    path = _custom_voice_path(sha256)
    if hashlib.sha256(content).hexdigest() != sha256:
        raise InvalidVoiceError("Content doesn't match its SHA-256 hash.")
    try:
        sf.info(io.BytesIO(content))
    except Exception as exc:
        raise InvalidVoiceError("Not a readable audio file.") from exc
    path.parent.mkdir(parents=True, exist_ok=True)
    # Written under a temporary name and renamed, so a parallel synthesis never reads half a file.
    temp = path.with_suffix(".part")
    temp.write_bytes(content)
    os.replace(temp, path)


def delete_voice(sha256: str) -> None:
    """Forget a custom clip and the voice states cloned from it (a no-op if it isn't stored)."""
    _custom_voice_path(sha256).unlink(missing_ok=True)
    for language in LANGUAGES:
        _state_file(language, sha256).unlink(missing_ok=True)
    with _states_lock:
        for key in [key for key in _states if key[1] == sha256]:
            del _states[key]


# ==================== SYNTHESIS ====================


def _wav_bytes(audio: torch.Tensor, rate: int) -> bytes:
    """Encode Pocket's mono float output as 16-bit PCM WAV bytes."""
    samples = audio.detach().cpu().float().reshape(-1).numpy()
    # A short silent tail: players and audio devices that stop a hair early would otherwise swallow
    # the end of the sentence.
    samples = np.concatenate([samples, np.zeros(int(TAIL_SILENCE_SECONDS * rate), dtype=np.float32)])
    buffer = io.BytesIO()
    sf.write(buffer, samples, rate, format="WAV", subtype="PCM_16")
    return buffer.getvalue()


def synthesize(text: str, language: str, voice: str | None = None, voice_sha256: str | None = None) -> bytes:
    """Generate speech for `text` in `language` as WAV bytes.

    Voice: the stored custom clip `voice_sha256` if given (and cloning is available), else `voice`
    (a preset, or "donation:<name>" with cloning), else the language's default preset. Raises
    UnsupportedLanguageError for a language that isn't enabled, VoiceNotStoredError if the custom
    clip isn't stored here (yet), or TimeoutError if no synthesis slot frees up within
    synthesis_queue_timeout_seconds — callers should treat a TimeoutError as "busy, retry later",
    not as a real failure (same posture as the backend's transcription queue).
    """
    tts = model(language)
    state = _voice_state(language, voice, voice_sha256)
    acquired = _synthesis_slots.acquire(timeout=settings.synthesis_queue_timeout_seconds)
    if not acquired:
        raise TimeoutError("Synthesis queue is full — no free slot within the timeout.")
    try:
        # copy_state (the default) leaves the cached voice state untouched, so parallel requests
        # can share it — and the model itself, which keeps no per-request state.
        audio = tts.generate_audio(state, text)
    finally:
        _synthesis_slots.release()
    return _wav_bytes(audio, tts.sample_rate)
