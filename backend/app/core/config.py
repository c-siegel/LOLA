"""
Configuration Settings for the LOLA Backend

This module defines all configuration settings for the backend. Settings are loaded from
the shared root .env file (or real environment variables), validated automatically by
Pydantic Settings.

What are settings?
Settings are typed values the app needs to run — secrets, URLs, feature toggles, file paths —
that differ between your machine and a real deployment. Instead of hardcoding them, they live
in a .env file and get read into this typed `Settings` object once at startup.

Required (no default — the app won't start without them):
- jwt_secret: signs login tokens
- api_key_encryption_secret: encrypts stored provider API keys

Everything else below has a sensible default for local development.

How to use:
    from app.core.config import settings

    if settings.registration_enabled:
        ...
    db_url = settings.database_url
"""

import os
from pathlib import Path
from typing import Literal

from cryptography.fernet import Fernet
from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Path to the .env-file within the root directory. LOLA_ENV_FILE points elsewhere; set to an
# empty value it reads no file at all, which the test suite relies on so a developer's own .env
# can't change what the tests see.
_ROOT_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"
_ENV_FILE = os.environ.get("LOLA_ENV_FILE", str(_ROOT_ENV_FILE)) or None

# Values people copy-paste from .env.example (or type without thinking) — none of these are
# safe JWT secrets, no matter how they're cased, so this is checked in addition to length.
_WEAK_JWT_SECRETS = frozenset(
    {
        "change-me", "changeme", "change_me", "secret", "password", "insecure",
        "your-secret-key", "test", "changethis", "please-change-me",
    }
)

class Settings(BaseSettings):
    
    # Pydantic configuration: read from the shared root .env file, ignore extra environment variables
    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    # ==================== DATABASE SETTINGS ====================
    
    database_url: str = "sqlite:///./lola.db"

    # ==================== JWT AUTHENTICATION SETTINGS ====================
    
    jwt_secret: str
    """
    Secret key used to sign JWT (JSON Web Token) authentication tokens.
    
    This is a REQUIRED setting. Generate a strong random string for production.
    You can generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"
    """

    jwt_algorithm: str = "HS256"
    """
    Algorithm used to sign JWT tokens.
    Default: "HS256" (HMAC with SHA-256)
    """

    access_token_expire_minutes: int = 10080  # 7 Tage

    # ==================== API KEY ENCRYPTION ====================
    
    api_key_encryption_secret: str
    """
    Secret key used to encrypt API keys stored in the database.

    This is a REQUIRED setting. Generate a strong random string for production.
    You can generate one with: python -c "import secrets; print(secrets.token_urlsafe(32))"
    """

    @field_validator("jwt_secret")
    @classmethod
    def _validate_jwt_secret(cls, v: str) -> str:
        """Reject placeholder or too-short secrets so a misconfigured deploy fails at
        startup instead of quietly signing every session token with a guessable key."""
        if len(v) < 32 or v.strip().strip("\"'").lower() in _WEAK_JWT_SECRETS:
            raise ValueError(
                "JWT_SECRET looks like a placeholder or is too short (need at least 32 "
                "random characters). Generate one with: "
                'python3 -c "import secrets; print(secrets.token_urlsafe(48))"'
            )
        return v

    @field_validator("api_key_encryption_secret")
    @classmethod
    def _validate_api_key_encryption_secret(cls, v: str) -> str:
        """Construct the real Fernet cipher here so an invalid key fails at startup —
        not the first time a user tries to save a provider API key in production."""
        try:
            Fernet(v.encode())
        except Exception as exc:
            raise ValueError(
                "API_KEY_ENCRYPTION_SECRET is not a valid Fernet key. Generate one with: "
                'python3 -c "from cryptography.fernet import Fernet; '
                'print(Fernet.generate_key().decode())"'
            ) from exc
        return v

    # ==================== CORS SETTINGS ====================
    
    cors_origins: str = "http://localhost:5173"
    """
    Allowed origins for Cross-Origin Resource Sharing (CORS).
    
    Default: "http://localhost:5173" (local development frontend)
    
    This controls which websites can make API requests to your backend.
    Separate multiple origins with commas, e.g.:
    "http://localhost:5173,https://yourdomain.com,https://app.yourdomain.com"
    
    Security: Only add trusted domains. Wildcards like "*" allow any domain (not recommended for production).
    """

    # ==================== ENVIRONMENT SETTINGS ====================
    
    environment: str = "development"
    """
    Current environment mode.
    
    Options:
    - "development": For local development (more verbose logging, debug features)
    - "production": For live deployment (optimized, secure)
    - "testing": For running tests
    
    This affects logging, error messages, and other environment-specific behavior.
    """

    cookie_secure: bool = False
    """
    Whether cookies should only be sent over HTTPS.
    
    Default: False (for development)
    
    Set to True in production (docker-compose.yaml) when using HTTPS. This prevents cookies from being
    sent over unencrypted HTTP connections, improving security.
    """

    # ==================== FILE UPLOAD DIRECTORIES ====================
    
    # Backend-specific directories (not frontend/public/) — Backend and Frontend can be
    # separate deployments/filesystems in institutional hosting environments.
    
    avatar_upload_dir: str = "uploads/avatars"
    """
    Directory where uploaded avatar files are stored.
    This is relative to the backend working directory. Make sure this directory
    exists and is writable by the application.
    """

    profile_picture_upload_dir: str = "uploads/profile-pictures"
    """
    Directory where uploaded user profile pictures are stored.
    This is relative to the backend working directory.
    """

    avatar_thumbnail_upload_dir: str = "uploads/avatar-thumbnails"
    """
    Directory where avatar thumbnail images are stored.
    Thumbnails are smaller versions of avatars for faster loading.
    """

    background_upload_dir: str = "uploads/backgrounds"
    """
    Directory where uploaded background images are stored.
    Background images are used in the chat interface.
    """

    start_audio_upload_dir: str = "uploads/start-audio"
    """
    Directory where a project's once-generated start-prompt audio (see features/projects/start_audio.py's
    start-audio routes) is stored, so it doesn't need to be re-synthesized on every chat load.
    """

    voice_clip_upload_dir: str = "uploads/voice-clips"
    """
    Directory where teachers' voice clips for local voice cloning are stored (see
    features/media/voices_router.py), always as normalized WAV files.
    """

    voice_preview_timeout_seconds: float = 120.0
    """
    How long a voice preview (see POST /voice-clips/{id}/preview) may wait for the local-TTS
    sidecar. Longer than local_tts_request_timeout_seconds because the first request after the
    sidecar starts also loads its model, which takes over a minute on a typical CPU.
    """

    # ==================== PUBLIC URL PATH ====================

    base_path: str = ""
    """
    URL path prefix the whole site is served under, e.g. "/lola" for https://host.example/lola/.
    Empty (the default) when the site has its own domain.

    The backend's own routes stay at /api/v1 (Caddy strips the prefix before proxying, see
    docker/Caddyfile); this only goes into the URLs the API hands out for the browser to load
    (app/core/urls.py), which must carry the prefix to reach this site at all. The web container
    reads the same BASE_PATH from .env, so both always agree.
    """

    @field_validator("base_path")
    @classmethod
    def _normalize_base_path(cls, v: str) -> str:
        """Accept "lola", "/lola" and "/lola/" alike: every URL is built as base_path + "/…"."""
        v = v.strip().strip("/")
        return f"/{v}" if v else ""

    # ==================== EMAIL & PASSWORD RESET SETTINGS ====================

    # For password reset flow (link in email text) and SMTP sending.
    
    frontend_base_url: str = "http://localhost:5173"
    """
    Base URL of the frontend application.
    
    Default: "http://localhost:5173" (local development)
    
    This is used to generate links in emails, such as password reset links.
    Example: If your frontend is at https://app.example.com, set this to that URL.
    """

    # ==================== SMTP-EMAIL Settings ====================
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_username: str = ""
    smtp_password: str = ""
    smtp_from_address: str = "no-reply@lola.example"
    smtp_use_tls: bool = True
    password_reset_token_expire_minutes: int = 30

    # ==================== REGISTRATION SETTINGS ====================
    
    # For initial deployment, the instance should only be internally usable
    # (accounts are created manually) — Login remains unaffected, only
    # self-registration is disabled (see features/auth/router.py).
    
    registration_enabled: bool = True
    """
    Whether new users can register themselves.
    When True: Anyone can create an account via the registration page.

    Only used as the INITIAL value the first time the DB-backed site settings row is created
    (see features/site_settings/service.py) — after that, an admin toggles this from the
    dashboard instead, and this env var no longer has any effect.
    """

    # ==================== ADMIN BOOTSTRAP SETTINGS ====================

    # Set both together to create (or promote) an admin account on startup — see
    # app/cli/bootstrap_admin.py, run from docker/backend-entrypoint.sh and scripts/dev.py. Safe to
    # remove from your .env after the first successful run; the bootstrap is idempotent either way.

    admin_email: str | None = None
    """
    Email of the account to create-or-promote to admin on startup.

    Leave unset to skip admin bootstrapping entirely (e.g. for a second instance that already
    has its admins). Must be set together with admin_password.
    """

    admin_password: str | None = None
    """
    Password for a newly created bootstrap admin account.

    Only used when admin_email doesn't match an existing account yet — promoting an EXISTING
    account to admin never touches their password.
    """

    # ==================== SPEECH-TO-TEXT (STT) SETTINGS ====================
    
    # Speech recognition for voice messages that aren't transcribed on the visitor's device (see
    # the browser settings further down) runs directly in the backend process — no separate
    # container or cloud service needed (see features/ai/stt). A project's own cloud STT key, if
    # set, takes priority over both engines below.

    stt_engine: Literal["whisper", "parakeet"] = "whisper"
    """
    Which local engine transcribes on the server.

    - "whisper": faster-whisper with stt_model below (default).
    - "parakeet": the Parakeet Redux model from stt_parakeet_model_dir — about twice as fast as
      Whisper "small" on a CPU and at least as accurate on German, but it detects the language
      itself (no spoken_language hint) and gets no text context between the segments of one
      recording. Falls back to Whisper while its model files are missing.
    """

    stt_parakeet_model_dir: str = "../models/parakeet-redux/v1"
    """
    Folder with the Parakeet model files (manifest.json and the files it lists), as written by
    scripts/fetch-stt-model.sh — the same files the browsers load. Relative to the backend's
    working directory; docker/docker-compose.yml points it at the data volume.
    """

    # stt_model is either a short name ("small", "medium", …) or a Hugging Face
    # repository (like the default) — both are accepted directly by faster-whisper.
    
    stt_model: str = "Systran/faster-whisper-small"
    """
    Speech-to-Text model to use for transcribing audio.
        
    Options:
    - "Systran/faster-whisper-small": Small model, fast, good accuracy (default)
    - "Systran/faster-whisper-medium": Medium model, slower, better accuracy
    - "Systran/faster-whisper-large-v3": Large model, slowest, best accuracy
    - "tiny", "base", "small", "medium", "large": Short names for standard models
    
    The model will be downloaded automatically on first use.
    Larger models are more accurate but require more CPU/memory and are slower.
    """

    stt_model_cache_dir: str = "whisper-cache"
    """
    Directory where the STT model is cached after download.

    The model is downloaded on first use (several hundred MB). This directory
    stores the downloaded model so it doesn't need to be downloaded again.

    Important: In Docker, this must point to a persistent volume (see
    docker/docker-compose.yml), otherwise the model is re-downloaded on every
    container restart.
    """

    stt_max_concurrent_transcriptions: int = 2
    """
    How many local transcriptions (Whisper or Parakeet) may run at the same time, in this one
    process (see features/ai/stt/capacity.py).

    Both engines are CPU-bound and (with stt_cpu_threads below) each transcription is written to use
    most of the machine's cores — running several at once wouldn't finish any of them faster,
    just make every one slower. Requests beyond this limit wait in line (see
    stt_transcription_queue_timeout_seconds) instead of piling onto the CPU together.
    """

    stt_transcription_queue_timeout_seconds: float = 5.0
    """
    How long a transcription request waits for a free "slot" (see
    stt_max_concurrent_transcriptions above) before giving up.

    A busy 503 lets the visitor retry immediately; queueing indefinitely would instead pile up
    stuck requests behind whichever transcription is currently running.
    """

    stt_cpu_threads: int = 0
    """
    CPU threads the local STT model (Whisper or Parakeet) may use per transcription.

    0 (the default) means "let faster-whisper pick its own default", which uses most of the
    machine's cores per call — fine for a single transcription, but worth capping (e.g. to a
    third of the host's cores) on a shared machine so one transcription doesn't starve the
    request-serving thread pool of CPU while it runs.
    """

    # ==================== LOCAL TEXT-TO-SPEECH (TTS) SETTINGS ====================

    # Unlike local STT (faster-whisper, above), local TTS does NOT run in this process — the
    # model needs more CPU/RAM than fits comfortably alongside the request-serving backend, so it
    # runs in its own optional sidecar container instead (see local-tts/ and
    # docker/local-tts.Dockerfile). This backend only makes an HTTP call to it, and only when
    # local_tts_enabled is set — a deployment that doesn't run that sidecar sees no change at all.

    local_tts_enabled: bool = False
    """
    Whether the local-TTS sidecar (see local-tts/) is reachable and should be used as the
    fallback when a project has TTS enabled but no cloud key configured — the TTS counterpart to
    how a missing STT key falls back to local Whisper (see features/ai/stt).

    False by default: unlike Whisper, this fallback needs a separate container actually running
    (docker/docker-compose.yml's "local-tts" profile), so it's opt-in rather than always-on.
    """

    local_tts_url: str = "http://tts-local:8080"
    """Base URL of the local-TTS sidecar container. Only used when local_tts_enabled is True."""

    local_tts_request_timeout_seconds: float = 30.0
    """How long to wait for the local-TTS sidecar before giving up on one synthesis request."""

    # ==================== BROWSER SPEECH-TO-TEXT (WEBGPU) SETTINGS ====================

    # The default way voice input is transcribed: the Parakeet Redux model runs in the visitor's
    # own browser via WebGPU (a browser API for using the GPU for general-purpose computation, not
    # just graphics) and streams live text while the student speaks — see
    # frontend/src/lib/parakeetStt.ts. Local server Whisper (above) and a cloud STT key remain as
    # the fallback for a browser without WebGPU or a failed model load. The backend computes
    # nothing here; it only tells the public chat where the model files live (see
    # features/api_keys/resolve.py::browser_stt_model_url_for).

    browser_stt_enabled: bool = True
    """
    Whether on-device transcription is offered to visitors at all. Each project can still opt
    out with its own "stt_browser_enabled" checkbox (see features/projects/models.py).

    Needs the model files to be present at browser_stt_model_url (Docker Compose's stt-model
    service downloads them; in development, run scripts/fetch-stt-model.sh). If they're missing,
    every visitor's browser falls back to server transcription after a failed load — set this to
    false instead to skip that wasted attempt.
    """

    browser_stt_model_url: str = "/models/parakeet-redux/v1/"
    """
    Base URL of the model files (manifest.json plus the ONNX files it lists), as written by
    scripts/fetch-stt-model.sh. Same-origin by default: Caddy serves it in production (see
    docker/Caddyfile) and Vite in development (see frontend/vite.config.ts).
    """

    # ==================== PUBLIC CHAT & VOICE INPUT RATE LIMITS ====================

    # Sized for a school class of ~30 students in one 45-minute lesson (see
    # app/core/rate_limit.py). The per-visitor number is the tight, meaningful-per-person
    # limit; the per-IP number is a much higher ceiling since a whole class typically shares
    # one public address behind NAT — it guards against abuse, not normal classroom use.

    chat_window_seconds: int = 600
    """Sliding-window size, in seconds, for the public chat message rate limits below."""

    chat_max_per_visitor: int = 30
    """Public chat messages one visitor (browser tab) may send per chat_window_seconds."""

    chat_max_per_ip: int = 900
    """
    Public chat messages one IP address may send per chat_window_seconds.

    High on purpose: a school class of ~30 students often shares one public IP behind NAT, so
    this has to comfortably cover the whole room's normal traffic, not just one person's.
    """

    transcribe_window_seconds: int = 600
    """Sliding-window size, in seconds, for the voice-transcription rate limits below."""

    transcribe_max_per_visitor: int = 90
    """
    Voice-transcription requests one visitor may send per transcribe_window_seconds.

    The frontend splits one spoken recording into several requests, one per pause it detects
    (see pages/PublicChat/index.tsx) — roughly 2-4 requests per sentence — so this needs to be
    several times the number of sentences a student would actually speak in that window.
    """

    transcribe_max_per_ip: int = 900
    """Voice-transcription requests one IP address may send per transcribe_window_seconds."""

    # ==================== THREAD POOL SIZING ====================

    # Every sync route (almost all of them — see api/*.py) and every streamed chat reply's
    # generator runs in one of these worker threads, not on the event loop itself. Starlette's
    # own default (40) is sized for a handful of simultaneous users, not a school class.

    request_thread_pool_size: int = 100
    """
    How many worker threads FastAPI/Starlette may use at once, across every sync route and
    every /messages/stream response body, in this one process.

    Applied to anyio's default thread limiter on startup (see main.py's lifespan) — anyio's own
    default is 40, shared by literally everything that isn't `async def`, which a class of ~30
    concurrently active students (each holding at least one thread for their streamed reply)
    can exhaust on its own.
    """

    tts_stream_worker_pool_size: int = 16
    """
    How many text-to-speech chunks may be synthesized at once, across every /messages/stream
    request, in this one process.

    Each streamed reply used to get its own single-worker thread pool for this; now they all
    share one pool sized by this setting, so a burst of simultaneously-streaming students shares
    a bounded resource instead of each spawning an unbounded number of threads.
    """

    # ==================== KNOWLEDGE BASE (RAG) SETTINGS ====================

    # Teachers' own documents as the avatar's knowledge, for any LLM provider. Like local TTS, the
    # work happens in an optional sidecar container (see rag/ and docs/rag-plan.md): this backend
    # owns who may see which knowledge base and calls that service over HTTP. Only the on/off
    # switch and how to reach the service are env settings — the upload limits are site settings
    # an admin changes in the dashboard (see features/site_settings/models.py).

    rag_enabled: bool = False
    """
    Whether the knowledge-base feature exists on this instance at all: its routes, the dashboard
    page and the retrieval step in every chat turn. Off by default — it needs the "rag" Compose
    profile actually running.
    """

    rag_service_url: str = "http://rag:8090"
    """Base URL of the knowledge service container. Only used when rag_enabled is True."""

    rag_service_token: str = ""
    """
    Shared secret sent to the knowledge service as a bearer token — the same value as that
    service's RAG_SERVICE_TOKEN. Required when rag_enabled is True.
    """

    rag_query_timeout_ms: int = 1500
    """
    Time budget for the retrieval step of one chat turn. Past it, the turn continues without
    passages (the avatar answers as if no material were attached) instead of keeping the student
    waiting — the student is already waiting for speech recognition, the LLM and TTS.
    """

    rag_request_timeout_seconds: float = 30.0
    """Timeout for the knowledge service's other calls (uploads, status, deletes, key tests)."""

    rag_evaluation_enabled: bool = False
    """
    Whether teachers can measure answer quality with Ragas (test sets on the knowledge page, the
    evaluation page). Needs rag_enabled too, and the "rag-eval" Compose profile running.
    """

    rag_eval_service_url: str = "http://rag-eval:8091"
    """Base URL of the evaluation service container (rag-eval/). Uses the same RAG_SERVICE_TOKEN."""

    rag_eval_request_timeout_seconds: float = 600.0
    """
    Timeout for one call to the evaluation service. Long: scoring a batch of ten answers takes
    dozens of judge LLM calls. Runs happen in the background, so nobody waits on a request.
    """

    @property
    def evaluation_enabled(self) -> bool:
        return self.rag_enabled and self.rag_evaluation_enabled

    @model_validator(mode="after")
    def _require_rag_token(self) -> "Settings":
        if self.rag_enabled and self.rag_service_token.strip().lower() in {"", "change-me", "changeme"}:
            raise ValueError(
                "RAG_ENABLED is true but RAG_SERVICE_TOKEN isn't set — use the same random value as the "
                'knowledge service\'s, e.g. python3 -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        return self

    # ==================== HELPER PROPERTIES ====================
    
    @property
    def smtp_configured(self) -> bool:
        """
        Check if SMTP is properly configured.
        
        Returns:
            True if smtp_host is set (email features enabled), False otherwise.
        """
        return bool(self.smtp_host)

    @property
    def cors_origin_list(self) -> list[str]:
        """
        Parse CORS origins string into a list.
        
        Returns:
            List of allowed CORS origins, with whitespace trimmed.
        Example:
            If cors_origins = "http://localhost:5173, https://example.com" it 
            returns ["http://localhost:5173", "https://example.com"]
        """
        return [origin.strip() for origin in self.cors_origins.split(",")]


settings = Settings()
"""
Global settings instance.
    
Usage in other modules:
    from app.core.config import settings
    
    # Access settings
    db_url = settings.database_url
    jwt_secret = settings.jwt_secret
"""