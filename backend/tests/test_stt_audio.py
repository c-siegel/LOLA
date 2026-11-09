"""Tests for the server's own audio decoding (app/features/ai/stt/audio.py), which replaced
faster-whisper's decode_audio. The uploads are built here with PyAV, in the formats browsers
actually send, so no binary fixtures are needed."""

import io
import wave

import av
import numpy as np
import pytest

from app.features.ai.stt.audio import SAMPLE_RATE, AudioDecodeError, decode_audio


def _tone(seconds: float, rate: int, frequency: float = 440.0) -> np.ndarray:
    t = np.arange(int(seconds * rate)) / rate
    return (0.5 * np.sin(2 * np.pi * frequency * t)).astype(np.float32)


def _webm_opus(samples: np.ndarray, rate: int = 48000, channels: int = 1) -> bytes:
    """What Chrome/Firefox MediaRecorder uploads: Opus in a WebM container."""
    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format="webm") as container:
        layout = "mono" if channels == 1 else "stereo"
        stream = container.add_stream("libopus", rate=rate, layout=layout)
        frame_size = 960  # 20 ms at 48 kHz, Opus's default frame
        planar = np.tile(samples, (channels, 1))
        for start in range(0, planar.shape[1], frame_size):
            frame = av.AudioFrame.from_ndarray(
                np.ascontiguousarray(planar[:, start : start + frame_size]), format="fltp", layout=layout
            )
            frame.sample_rate = rate
            frame.pts = start
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buffer.getvalue()


def _wav(samples: np.ndarray, rate: int) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(rate)
        out.writeframes((samples * 32767).astype("<i2").tobytes())
    return buffer.getvalue()


def test_decodes_webm_opus_to_16k_mono_float() -> None:
    audio = decode_audio(_webm_opus(_tone(1.0, 48000)))

    assert audio.dtype == np.float32
    assert audio.ndim == 1
    # Opus adds a little encoder delay/padding; the length must still be about one second.
    assert abs(len(audio) - SAMPLE_RATE) < 0.05 * SAMPLE_RATE
    assert 0.2 < np.abs(audio).max() <= 1.0


def test_downmixes_stereo_uploads() -> None:
    audio = decode_audio(_webm_opus(_tone(0.5, 48000), channels=2))
    assert audio.ndim == 1
    assert abs(len(audio) - SAMPLE_RATE // 2) < 0.05 * SAMPLE_RATE


def test_resamples_wav_and_keeps_the_signal() -> None:
    source = _tone(1.0, 44100, frequency=300.0)
    audio = decode_audio(_wav(source, 44100))

    assert abs(len(audio) - SAMPLE_RATE) <= 32
    # The tone's frequency survives resampling: the spectrum peaks at 300 Hz.
    spectrum = np.abs(np.fft.rfft(audio))
    peak_hz = np.argmax(spectrum) * SAMPLE_RATE / len(audio)
    assert abs(peak_hz - 300.0) < 5


def test_rejects_bytes_that_are_not_audio() -> None:
    with pytest.raises(AudioDecodeError):
        decode_audio(b"this is not a media file at all")
