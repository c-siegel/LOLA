"""
Configuration Settings for the Local TTS Sidecar

Typed settings for this service, read from environment variables (all prefixed "TTS_", set in
docker/docker-compose.yml). Kept deliberately small — unlike the main backend, this service has
no database. Its one secret is the optional Hugging Face token for voice cloning.

How to use:
    from app.config import settings

    settings.language_codes  # ["de", "en"]
"""

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.voices import LANGUAGES


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TTS_", extra="ignore")

    languages: str = "de,en"
    """
    Comma-separated languages to speak (de, en, fr, it, es, pt, nl). Each is its own Pocket TTS
    model, ~220 MB download and ~0.5–0.7 GB RAM once loaded; all listed ones load at startup. A
    request for any other language gets a 400.
    """

    hf_token: SecretStr | None = None
    """
    Hugging Face token of an account that accepted the terms of the gated `kyutai/pocket-tts`
    repo. Turns on voice cloning: teachers' own voice clips and the donated voices. Without it
    the service speaks with the preset voices only, and a cloning request gets a 409 (the backend
    then falls back to the preset voice).
    """

    model_cache_dir: str = "/data/model-cache"
    """
    Where downloaded model weights and voice states are cached (Hugging Face cache layout).

    Must point at a persistent volume in Docker (see docker/docker-compose.yml) — otherwise each
    language's ~220 MB model is re-downloaded from Hugging Face on every container restart.
    """

    custom_voices_dir: str = "/data/custom-voices"
    """
    Where reference clips sent by the backend are kept (see PUT /voices in app/main.py), named by
    their SHA-256 hash, and the voice states cloned from them and from donated voices. The backend
    owns the clips (teachers upload them in the dashboard) and re-sends one whenever it's missing
    here, so this directory is only a cache — safe to clear.
    """

    donated_voices: str = "all"
    """
    Which donated voices (see app/voices.py) to offer when cloning is on: "all", "none", or a
    comma-separated list of their names (e.g. "Selfie,oldNerd") to keep the picker short.
    """

    max_voice_bytes: int = 10 * 1024 * 1024
    """Upper bound for one reference clip; the backend already limits clips to 30 s of WAV."""

    torch_threads: int = 1
    """
    PyTorch threads for the whole process. Importing pocket_tts sets 1, and more threads barely
    speed up one request (measured: the same real-time factor with 1, 2 and 4) — parallel requests
    (max_concurrent_synthesis) use the cores better.
    """

    max_concurrent_synthesis: int = 2
    """
    How many synthesis requests may run at the same time, in this one process.

    A model is shared by parallel requests (each works on its own copy of the voice state), but
    synthesis is CPU-bound: running more requests than there are cores makes each one slower
    rather than finishing more work sooner.
    """

    synthesis_queue_timeout_seconds: float = 30.0
    """
    How long a synthesis request waits for a free slot (see max_concurrent_synthesis) before giving up.

    A streamed chat reply sends all of its sentences at once, so the later ones legitimately wait
    for the earlier ones. Matches the backend's local_tts_request_timeout_seconds — it stops
    waiting after that anyway.
    """

    @property
    def language_codes(self) -> list[str]:
        codes = [code.strip() for code in self.languages.split(",") if code.strip()]
        unknown = [code for code in codes if code not in LANGUAGES]
        if unknown:
            raise ValueError(f"TTS_LANGUAGES: unsupported language(s) {unknown}; choose from {sorted(LANGUAGES)}")
        return codes

    @property
    def donated_voice_names(self) -> list[str] | None:
        """None for all donated voices, otherwise the allowed names."""
        value = self.donated_voices.strip()
        if value.lower() == "all":
            return None
        if value.lower() == "none":
            return []
        return [name.strip() for name in value.split(",") if name.strip()]


settings = Settings()
