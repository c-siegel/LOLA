"""
Local Parakeet Transcription

An alternative to local faster-whisper (switched on with Settings.stt_engine = "parakeet"): runs
the Parakeet Redux model on the server CPU via ONNX Runtime — the same model files visitors'
browsers load for on-device recognition (scripts/fetch-stt-model.sh downloads them), so the
server needs no extra download. On a typical CPU it is about twice as fast as Whisper "small"
and at least as accurate on German.

What is a TDT decoder?
Parakeet is a "token-and-duration transducer": for each 80 ms step of encoded audio, a small
network predicts the next text piece (or "nothing here") plus how many steps to skip ahead. That
makes decoding a short loop of tiny model calls instead of Whisper's full text generation, which
is where most of the speed comes from.

How to use:
    from app.features.ai.stt.parakeet_local import LocalParakeetClient

    text = LocalParakeetClient().transcribe(audio_bytes, "de")
"""

import json
import logging
import os
import threading
from pathlib import Path

import numpy as np
import onnxruntime as ort

from app.core.config import settings
from app.features.ai.stt.audio import SAMPLE_RATE, decode_audio
from app.features.ai.stt.capacity import transcription_slot
from app.features.ai.stt.whisper_local import LocalWhisperClient

logger = logging.getLogger(__name__)

# One encoder step covers 8 feature frames of 10 ms each (see the model's config.json).
FRAME_SAMPLES = 1280
BLANK_TOKEN = 8192
VOCAB_LOGITS = 8193
TDT_DURATIONS = (0, 1, 2, 3, 4)
MAX_SYMBOLS_PER_FRAME = 10
SPEECH_PROBABILITY = 0.5
# The model was trained on segments of at most 30 s — longer audio is cut into chunks first.
MAX_CHUNK_SECONDS = 25
MIN_AUDIO_SECONDS = 0.1


def chunk_boundaries(probabilities: np.ndarray, total_samples: int, max_samples: int) -> list[int]:
    """Sample positions to cut audio at so no chunk exceeds max_samples, each cut placed at the
    quietest moment (lowest speech probability) in the last 40% of the allowed chunk length."""
    cuts: list[int] = []
    start = 0
    while total_samples - start > max_samples:
        first_frame = (start + int(max_samples * 0.6)) // FRAME_SAMPLES
        last_frame = min((start + max_samples) // FRAME_SAMPLES, len(probabilities))
        window = probabilities[first_frame:last_frame]
        frame = first_frame + int(np.argmin(window)) if len(window) else (start + max_samples) // FRAME_SAMPLES
        cut = max(frame * FRAME_SAMPLES, start + 1)
        cuts.append(cut)
        start = cut
    return cuts


def detokenize(token_ids: list[int], vocab: list[str]) -> str:
    """Join SentencePiece pieces into text, dropping special tokens like <unk>."""
    pieces = (vocab[i] for i in token_ids if i < len(vocab))
    text = "".join(p for p in pieces if not (p.startswith("<") and p.endswith(">")))
    return text.replace("▁", " ").strip()  # "▁" marks the start of a word


def _parse_vocab(text: str) -> list[str]:
    entries = {}
    for line in text.splitlines():
        token, _, index = line.rpartition(" ")
        if token:
            entries[int(index)] = token
    return [entries.get(i, "") for i in range(max(entries) + 1)]


class ParakeetModel:
    """The four ONNX models plus vocabulary, loaded from a folder written by fetch-stt-model.sh."""

    def __init__(self, model_dir: Path, threads: int):
        files = json.loads((model_dir / "manifest.json").read_text())["files"]
        options = ort.SessionOptions()
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1

        def session(name: str) -> ort.InferenceSession:
            return ort.InferenceSession(
                str(model_dir / files[name]["path"]), options, providers=["CPUExecutionProvider"]
            )

        self.preprocessor = session("preprocessor")
        self.vad = session("vad")
        self.encoder = session("encoder")
        self.decoder = session("decoder")
        self.vocab = _parse_vocab((model_dir / files["vocab"]["path"]).read_text(encoding="utf-8"))

    def _features(self, audio: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        features, lengths = self.preprocessor.run(
            None, {"waveforms": audio[None, :], "waveforms_lens": np.array([len(audio)], dtype=np.int64)}
        )
        return features, lengths

    def _speech_probabilities(self, features: np.ndarray, lengths: np.ndarray) -> np.ndarray:
        """One speech probability per 80 ms encoder step."""
        return self.vad.run(None, {"audio_signal": features, "length": lengths})[0][0]

    def _decode(self, features: np.ndarray, lengths: np.ndarray) -> str:
        """Encoder, then greedy TDT decoding (see the module docstring)."""
        encoded, encoded_lengths = self.encoder.run(None, {"audio_signal": features, "length": lengths})
        frames = int(encoded_lengths[0])
        steps = np.ascontiguousarray(encoded[0].T)  # [frames, hidden], one row per step
        state1 = np.zeros((2, 1, 640), dtype=np.float32)
        state2 = np.zeros_like(state1)
        last_token = BLANK_TOKEN
        tokens: list[int] = []
        t = 0
        symbols_this_frame = 0
        while t < frames:
            logits, _, new_state1, new_state2 = self.decoder.run(
                None,
                {
                    "encoder_outputs": steps[t][None, :, None],
                    "targets": np.array([[last_token]], dtype=np.int32),
                    "target_length": np.array([1], dtype=np.int32),
                    "input_states_1": state1,
                    "input_states_2": state2,
                },
            )
            logits = logits[0, 0, 0]
            token = int(np.argmax(logits[:VOCAB_LOGITS]))
            duration = TDT_DURATIONS[int(np.argmax(logits[VOCAB_LOGITS:]))]
            if token != BLANK_TOKEN:
                tokens.append(token)
                last_token = token
                # The prediction network only advances on a real token, not on a blank.
                state1, state2 = new_state1, new_state2
                symbols_this_frame += 1
            if duration > 0:
                t += duration
                symbols_this_frame = 0
            elif token == BLANK_TOKEN or symbols_this_frame >= MAX_SYMBOLS_PER_FRAME:
                t += 1
                symbols_this_frame = 0
        return detokenize(tokens, self.vocab)

    def transcribe(self, audio: np.ndarray) -> str:
        """Transcribe 16 kHz mono audio; "" if it contains no speech."""
        if len(audio) < MIN_AUDIO_SECONDS * SAMPLE_RATE:
            return ""
        features, lengths = self._features(audio)
        probabilities = self._speech_probabilities(features, lengths)
        # Same job as faster-whisper's vad_filter: never run the encoder on pure silence.
        if not (probabilities > SPEECH_PROBABILITY).any():
            return ""
        max_samples = MAX_CHUNK_SECONDS * SAMPLE_RATE
        if len(audio) <= max_samples:
            return self._decode(features, lengths)
        bounds = [0, *chunk_boundaries(probabilities, len(audio), max_samples), len(audio)]
        texts = [self._decode(*self._features(audio[a:b])) for a, b in zip(bounds, bounds[1:])]
        return " ".join(t for t in texts if t)


_model_lock = threading.Lock()
_loaded_model: ParakeetModel | None = None
_missing_model_logged = False


def model_available() -> bool:
    """Whether the model files are there (cheap check, loads nothing) — the Configurator only
    offers Parakeet once they are, see features/api_keys/providers_router.py::server_stt_status."""
    return (Path(settings.stt_parakeet_model_dir) / "manifest.json").is_file()


def _model() -> ParakeetModel:
    """Load (once per process) and cache the model. Raises FileNotFoundError while its files
    aren't there yet — retried on the next call, so it starts working as soon as they appear."""
    global _loaded_model
    with _model_lock:
        if _loaded_model is None:
            model_dir = Path(settings.stt_parakeet_model_dir)
            if not (model_dir / "manifest.json").is_file():
                raise FileNotFoundError(f"No Parakeet model at {model_dir} (manifest.json missing)")
            _loaded_model = ParakeetModel(model_dir, settings.stt_cpu_threads or (os.cpu_count() or 4))
        return _loaded_model


class LocalParakeetClient:
    """Transcribes with the process-wide Parakeet model, or with local Whisper while the model
    files aren't downloaded yet (e.g. right after a fresh deployment — see the stt-model service
    in docker/docker-compose.yml)."""

    def transcribe(self, audio_bytes: bytes, language: str, initial_prompt: str | None = None) -> str:
        """Unlike Whisper, Parakeet detects the language itself and takes no text context, so
        `language` and `initial_prompt` are accepted only to fit the STTClient interface.

        Raises TimeoutError if the server is at capacity (see capacity.py::transcription_slot).
        """
        global _missing_model_logged
        try:
            model = _model()
        except FileNotFoundError as exc:
            if not _missing_model_logged:
                _missing_model_logged = True
                logger.warning("%s — using local Whisper until it's downloaded.", exc)
            return LocalWhisperClient().transcribe(audio_bytes, language, initial_prompt)
        with transcription_slot():
            audio = decode_audio(audio_bytes)
            return model.transcribe(audio)
