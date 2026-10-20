"""
Local-TTS Sidecar

The fallback used when a project has TTS enabled but no cloud key configured — the TTS
counterpart to local Whisper for STT (see features/ai/stt/whisper_local.py), just
out-of-process: the model needs more CPU/RAM than fits alongside the request-serving backend, so
it runs in its own optional container (see local-tts/ and docker/local-tts.Dockerfile). Only
used when settings.local_tts_enabled is set.

Voices: a project's `tts_voice` names one of the sidecar's voices (a Pocket TTS preset, or
"donation:<name>" — see sidecar_voices); unknown names, e.g. a cloud provider's voice left over
after switching to local TTS, make the sidecar use the language's default. A teacher's own clip
(see features/media/voices_router.py) is identified by its SHA-256 hash; the sidecar answers 404
VOICE_NOT_STORED until it has that clip, in which case it's sent once (PUT /voices/{sha256}) and
the request retried. So the two services share no files. Clips and donated voices need the
sidecar's voice cloning; without it, the sidecar speaks with the preset instead.
"""

import logging
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.core.config import settings
from app.features.ai.http import make_ipv4_client

logger = logging.getLogger(__name__)

_client = make_ipv4_client()


@dataclass(frozen=True)
class VoiceReference:
    """A teacher's voice clip (a WAV file in the backend's storage) to clone the voice from."""

    sha256: str
    path: str


def _url(path: str) -> str:
    return f"{settings.local_tts_url.rstrip('/')}{path}"


def _raise_for_status(response: httpx.Response) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        raise httpx.HTTPStatusError(f"{exc}: {response.text[:500]}", request=exc.request, response=exc.response) from exc


class LocalTTSClient:
    """Synthesizes via the local-TTS sidecar's POST /synthesize; returns real WAV audio."""

    def __init__(self, voice_clip: VoiceReference | None = None, timeout_seconds: float | None = None):
        self.voice_clip = voice_clip
        self.timeout = timeout_seconds or settings.local_tts_request_timeout_seconds

    def _post_synthesize(self, text: str, voice: str | None, language: str) -> httpx.Response:
        body = {"text": text, "language": language}
        if voice:
            body["voice"] = voice
        if self.voice_clip:
            body["voice_sha256"] = self.voice_clip.sha256
        return _client.post(_url("/synthesize"), json=body, timeout=self.timeout)

    def synthesize(self, text: str, voice: str | None, language: str) -> tuple[bytes, str]:
        """`voice` is one of the sidecar's voices (see sidecar_voices); `voice_clip`, if set, takes
        precedence where the sidecar can clone it."""
        response = self._post_synthesize(text, voice, language)
        if response.status_code == 404 and self.voice_clip:
            upload = _client.put(
                _url(f"/voices/{self.voice_clip.sha256}"),
                content=Path(self.voice_clip.path).read_bytes(),
                timeout=self.timeout,
            )
            _raise_for_status(upload)
            response = self._post_synthesize(text, voice, language)
        _raise_for_status(response)
        return response.content, "audio/wav"


def forget_voice(sha256: str) -> None:
    """Ask the sidecar to drop a deleted clip's copy. Best effort: the sidecar's copies are only a
    cache, so a sidecar that's down or disabled is no reason to fail the delete."""
    if not settings.local_tts_enabled:
        return
    try:
        _client.delete(_url(f"/voices/{sha256}"), timeout=settings.local_tts_request_timeout_seconds)
    except httpx.HTTPError:
        logger.warning("Couldn't remove voice clip %s from the local-TTS sidecar.", sha256, exc_info=True)


@dataclass(frozen=True)
class SidecarVoices:
    """What the sidecar can speak (its GET /voices)."""

    languages: list[str]
    # Whether teachers' clips and donated voices are cloned (the sidecar has the gated weights).
    cloning: bool
    # {"id", "label", "kind": "preset" | "donation", "licence"} per voice.
    voices: list[dict]


# Asked by every configurator and Voices page load, while the answer only changes when the sidecar
# restarts: cached briefly, and a failure for a shorter time so a sidecar that just came up shows
# soon.
_VOICES_TTL_SECONDS = 60.0
_VOICES_FAILURE_TTL_SECONDS = 10.0
_voices_cache: tuple[float, SidecarVoices | None] | None = None
_voices_lock = threading.Lock()


def sidecar_voices() -> SidecarVoices | None:
    """The sidecar's languages, cloning status and voices, or None when it's disabled or can't be
    reached."""
    global _voices_cache
    if not settings.local_tts_enabled:
        return None
    with _voices_lock:
        if _voices_cache and _voices_cache[0] > time.monotonic():
            return _voices_cache[1]
    try:
        response = _client.get(_url("/voices"), timeout=3.0)
        _raise_for_status(response)
        body = response.json()
        result: SidecarVoices | None = SidecarVoices(
            languages=list(body.get("languages", [])),
            cloning=bool(body.get("cloning", False)),
            voices=list(body.get("voices", [])),
        )
        ttl = _VOICES_TTL_SECONDS
    except (httpx.HTTPError, ValueError):
        logger.warning("Couldn't read the voices of the local-TTS sidecar.", exc_info=True)
        result, ttl = None, _VOICES_FAILURE_TTL_SECONDS
    with _voices_lock:
        _voices_cache = (time.monotonic() + ttl, result)
    return result


def clear_voices_cache() -> None:
    """Forget the cached GET /voices answer (tests, or after changing settings)."""
    global _voices_cache
    with _voices_lock:
        _voices_cache = None
