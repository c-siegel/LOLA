"""
STT Client Interface

What every speech-to-text integration implements.
"""

from typing import Protocol


class STTClient(Protocol):
    """A speech-to-text engine: the local Parakeet model, or a provider bound to a stored key."""

    def transcribe(self, audio_bytes: bytes, language: str, initial_prompt: str | None = None) -> str:
        """Transcribe audio to text, decoded as `language` (project.spoken_language)."""
        ...
