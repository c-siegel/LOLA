"""
Audio Decoding for Server-Side Transcription

Turns whatever the browser uploaded (usually WebM/Opus from MediaRecorder, sometimes MP4/AAC on
Safari, WAV from tests and tools) into the 16 kHz mono float32 samples the local model expects.
PyAV bundles its own FFmpeg libraries, so no system ffmpeg is needed.

How to use:
    from app.features.ai.stt.audio import decode_audio

    samples = decode_audio(audio_bytes)  # np.ndarray, float32 in [-1, 1]
"""

import io

import av
import numpy as np

SAMPLE_RATE = 16000


class AudioDecodeError(ValueError):
    """The upload isn't audio PyAV can read (corrupt, truncated, or an unsupported format)."""


def decode_audio(audio_bytes: bytes, sampling_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Decode an audio file of any container/codec FFmpeg knows into mono float32 at
    `sampling_rate`. Returns an empty array for a file without audio frames."""
    # Resampling to signed 16-bit and scaling afterwards (rather than asking for float output)
    # keeps the result identical to what the model was tested with before.
    resampler = av.AudioResampler(format="s16", layout="mono", rate=sampling_rate)
    chunks: list[np.ndarray] = []
    try:
        with av.open(io.BytesIO(audio_bytes), mode="r") as container:
            if not container.streams.audio:
                raise AudioDecodeError("The upload contains no audio stream")
            stream = container.streams.audio[0]
            for frame in container.decode(stream):
                for resampled in resampler.resample(frame):
                    chunks.append(resampled.to_ndarray().reshape(-1))
            # Flush the samples the resampler still holds back for filtering.
            for resampled in resampler.resample(None):
                chunks.append(resampled.to_ndarray().reshape(-1))
    except av.FFmpegError as exc:
        raise AudioDecodeError(f"Could not decode the audio upload: {exc}") from exc
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks).astype(np.float32) / 32768.0
