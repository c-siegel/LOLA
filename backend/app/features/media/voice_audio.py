"""
Voice Clip Audio Checks

Validates an uploaded or browser-recorded voice clip and converts it into the one format the
voice library stores: a mono, 16-bit WAV at 24 kHz (the sample rate the local-TTS model itself
produces). Converting once at upload means the sidecar never has to deal with WebM/MP3/...
quirks, and every clip's length is known up front.

How to use:
    from app.features.media.voice_audio import normalize_voice_clip

    wav_bytes, duration_seconds = normalize_voice_clip(uploaded_bytes)
"""

import io
import wave

import av
import numpy as np

from app.core.error_codes import ErrorCode
from app.core.errors import DomainError

TARGET_SAMPLE_RATE = 24000
# The voice-cloning model (Pocket TTS in local-tts/) needs a few seconds to pick up a voice and
# reads at most the first 30 s of a clip.
MIN_SECONDS = 3.0
MAX_SECONDS = 30.0

# (offset, signature) pairs of the container formats a browser recording or a typical audio file
# comes in. Checked before handing the bytes to the decoder, so arbitrary files are rejected by
# their content, not by their name or the Content-Type the browser claims.
_SIGNATURES = [
    (0, b"RIFF"),  # WAV (followed by "WAVE" at offset 8, checked below)
    (0, b"\x1a\x45\xdf\xa3"),  # WebM / Matroska — what MediaRecorder produces in Chrome/Firefox
    (0, b"OggS"),  # Ogg (Opus/Vorbis)
    (0, b"ID3"),   # MP3 with an ID3 tag
    (0, b"fLaC"),  # FLAC
    (4, b"ftyp"),  # MP4 / M4A — what MediaRecorder produces in Safari
]


class VoiceClipInvalidAudio(DomainError):
    status_code = 400
    detail = ErrorCode.VOICE_CLIP_INVALID_AUDIO


class VoiceClipTooShort(DomainError):
    status_code = 400
    detail = ErrorCode.VOICE_CLIP_TOO_SHORT


def looks_like_audio(content: bytes) -> bool:
    """Whether `content` starts like one of the accepted audio container formats."""
    if content[:4] == b"RIFF" and content[8:12] != b"WAVE":
        return False
    # A bare MP3 without an ID3 tag starts directly with an MPEG frame header (11 set sync bits).
    if len(content) > 1 and content[0] == 0xFF and content[1] & 0xE0 == 0xE0:
        return True
    return any(content[offset : offset + len(sig)] == sig for offset, sig in _SIGNATURES)


def normalize_voice_clip(content: bytes) -> tuple[bytes, float]:
    """Decode any accepted format into mono 16-bit 24 kHz WAV bytes, plus the clip's duration.

    Raises VoiceClipInvalidAudio for anything that isn't decodable audio and VoiceClipTooShort below
    MIN_SECONDS. Anything longer than MAX_SECONDS is cut to its first MAX_SECONDS instead of rejected
    (browser recordings that stop "at 30 s" routinely end a bit over, and uploads may be longer).
    """
    if not looks_like_audio(content):
        raise VoiceClipInvalidAudio()
    resampler = av.AudioResampler(format="s16", layout="mono", rate=TARGET_SAMPLE_RATE)
    chunks: list[np.ndarray] = []
    max_samples = int(MAX_SECONDS * TARGET_SAMPLE_RATE)
    try:
        with av.open(io.BytesIO(content), mode="r") as container:
            if not container.streams.audio:
                raise VoiceClipInvalidAudio()
            for frame in container.decode(audio=0):
                for resampled in resampler.resample(frame):
                    chunks.append(resampled.to_ndarray().reshape(-1))
                # Stop decoding early instead of first decoding an arbitrarily long file.
                if sum(len(c) for c in chunks) >= max_samples:
                    break
            for resampled in resampler.resample(None):  # flush what the resampler still holds
                chunks.append(resampled.to_ndarray().reshape(-1))
    except VoiceClipInvalidAudio:
        raise
    except Exception as exc:  # PyAV raises a whole family of errors for broken input
        raise VoiceClipInvalidAudio() from exc

    samples = np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.int16)
    samples = samples[:max_samples]
    duration = len(samples) / TARGET_SAMPLE_RATE
    if duration < MIN_SECONDS:
        raise VoiceClipTooShort()

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(TARGET_SAMPLE_RATE)
        wav.writeframes(samples.astype(np.int16).tobytes())
    return buffer.getvalue(), round(duration, 2)
