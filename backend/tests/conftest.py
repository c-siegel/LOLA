"""Shared fixtures for the route-level tests (test_routes_*.py).

Settings validate JWT_SECRET/API_KEY_ENCRYPTION_SECRET at import time, so throwaway values are
set here before anything imports app.core.config — real values from the environment win. The
repo root .env is not read, so a developer's local settings, e.g. a LOCAL_TTS_URL, can't make
tests pass or fail depending on whose machine runs them. It has two ways in: the settings' own
env_file (LOLA_ENV_FILE="") and litellm, which in its default "DEV" mode copies the nearest
.env into os.environ when imported (LITELLM_MODE=PRODUCTION).

The AI providers are faked at their lowest stable seams (litellm.completion/litellm.speech for
LLM/TTS, the local Whisper model loader for STT), so the route tests exercise the whole stack
above them and keep working while the code above those seams is restructured.
"""

import os
import secrets

from cryptography.fernet import Fernet

os.environ["LOLA_ENV_FILE"] = ""
os.environ["LITELLM_MODE"] = "PRODUCTION"
os.environ.setdefault("JWT_SECRET", secrets.token_urlsafe(48))
os.environ.setdefault("API_KEY_ENCRYPTION_SECRET", Fernet.generate_key().decode())

from types import SimpleNamespace  # noqa: E402

import litellm  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402
from sqlmodel import Session, SQLModel, create_engine  # noqa: E402

import app.db.base  # noqa: E402,F401  (registers every model's table on SQLModel.metadata)
from app.core import rate_limit  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.security import create_access_token, hash_password  # noqa: E402
from app.core.urls import API_PREFIX  # noqa: E402
from app.db.session import get_session  # noqa: E402
from app.features.users.models import User  # noqa: E402
from app.main import app  # noqa: E402

# Module attributes holding the process-wide `engine` that code opens its own Session(engine)
# with (outside the request-scoped get_session dependency) — redirected to the test engine.
ENGINE_TARGETS = ["app.features.chat.conversation_store.engine", "app.features.evaluation.runner.engine"]
# Where the local Whisper model is loaded (patched so no model is ever downloaded).
STT_MODEL_TARGET = "app.features.ai.stt.whisper_local._model"

UPLOAD_DIR_SETTINGS = [
    "avatar_upload_dir",
    "avatar_thumbnail_upload_dir",
    "background_upload_dir",
    "profile_picture_upload_dir",
    "start_audio_upload_dir",
    "voice_clip_upload_dir",
]

# Every request path in the route tests is relative to the API prefix, like the frontend's client.
API_BASE_URL = f"http://testserver{API_PREFIX}"

PASSWORD = "correct-horse-1"
LLM_REPLY = "Hallo! Das ist eine Antwort vom Tutor. Sie hat mehrere Sätze, damit sie gestreamt wird."
TTS_BYTES = b"ID3-fake-mp3"


@pytest.fixture
def engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


@pytest.fixture
def client(engine, monkeypatch, tmp_path):
    def _get_session():
        with Session(engine) as session:
            yield session

    app.dependency_overrides[get_session] = _get_session
    for target in ENGINE_TARGETS:
        monkeypatch.setattr(target, engine)
    for name in UPLOAD_DIR_SETTINGS:
        monkeypatch.setattr(settings, name, str(tmp_path / name))
    rate_limit._hits.clear()
    yield new_client()
    app.dependency_overrides.clear()
    rate_limit._hits.clear()


@pytest.fixture
def anon(client):
    """A second, cookie-less client against the same app — an anonymous chat visitor."""
    return new_client()


def new_client() -> TestClient:
    """A fresh client (own cookie jar) whose request paths are relative to the API prefix."""
    return TestClient(app, base_url=API_BASE_URL)


def browser_url(url: str) -> str:
    """An absolute URL the API handed out (e.g. an avatar's fileUrl), fetched as a browser would."""
    return f"http://testserver{url}"


class FakeAI:
    """Records calls and returns canned answers for the faked provider seams."""

    def __init__(self) -> None:
        self.llm_reply: str | None = LLM_REPLY
        self.llm_error: Exception | None = None
        self.tts_error: Exception | None = None
        self.stt_text = " hallo welt "
        self.completion_calls: list[dict] = []
        self.speech_calls: list[dict] = []
        self.transcribe_calls: list[dict] = []

    def completion(self, **kwargs):
        self.completion_calls.append(kwargs)
        if self.llm_error is not None:
            raise self.llm_error
        if kwargs.get("stream"):
            words = self.llm_reply.split(" ")
            deltas = [w + (" " if i < len(words) - 1 else "") for i, w in enumerate(words)]
            return iter(
                SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=d))]) for d in deltas
            )
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.llm_reply))])

    def speech(self, **kwargs):
        self.speech_calls.append(kwargs)
        if self.tts_error is not None:
            raise self.tts_error
        return SimpleNamespace(content=TTS_BYTES)

    def whisper_model(self):
        fake = self

        class _Model:
            def transcribe(self, audio, **kwargs):
                fake.transcribe_calls.append(kwargs)
                return [SimpleNamespace(text=fake.stt_text)], None

        return _Model()


@pytest.fixture
def fake_ai(monkeypatch):
    fake = FakeAI()
    monkeypatch.setattr(litellm, "completion", fake.completion)
    monkeypatch.setattr(litellm, "speech", fake.speech)
    monkeypatch.setattr(STT_MODEL_TARGET, fake.whisper_model)
    return fake


def make_user(engine, *, email: str = "teacher@example.com", name: str = "Teacher", is_admin: bool = False) -> User:
    with Session(engine) as session:
        user = User(name=name, email=email, password_hash=hash_password(PASSWORD), is_admin=is_admin)
        session.add(user)
        session.commit()
        session.refresh(user)
        return user


def login_as(client: TestClient, user: User) -> TestClient:
    client.cookies.set("access_token", create_access_token(user.id, user.token_version))
    return client


@pytest.fixture
def teacher(client, engine):
    """A logged-in (non-admin) teacher; `client` carries their auth cookie."""
    user = make_user(engine)
    login_as(client, user)
    return user


def create_key(client: TestClient, *, key_type: str = "llm", provider: str = "openai", **extra) -> dict:
    body = {"provider": provider, "keyType": key_type, "apiKey": "sk-test-secret-1234", **extra}
    if key_type == "llm" and "modelId" not in body:
        body["modelId"] = "gpt-4o-mini"
    response = client.post("/api-keys", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def create_project(client: TestClient, **fields) -> dict:
    response = client.post("/projects", json={"title": "Mathe-Tutor", **fields})
    assert response.status_code == 200, response.text
    return response.json()


def publish(client: TestClient, project_id: str) -> str:
    response = client.put(f"/projects/{project_id}/publication")
    assert response.status_code == 200, response.text
    return response.json()["shareSlug"]


@pytest.fixture
def chat_project(client, teacher, fake_ai):
    """A published project with an LLM key, TTS enabled, and conversation saving on."""
    llm_key = create_key(client)
    tts_key = create_key(client, key_type="tts")
    project = create_project(
        client,
        llmApiKeyId=llm_key["id"],
        ttsApiKeyId=tts_key["id"],
        ttsEnabled=True,
        saveConversations=True,
        preprompt="Du bist ein Tutor.",
        startPrompt="Willkommen!",
    )
    project["shareSlug"] = publish(client, project["id"])
    return project


def parse_sse(text: str) -> list[tuple[str, dict]]:
    import json

    events = []
    for block in text.strip().split("\n\n"):
        lines = dict(line.split(": ", 1) for line in block.split("\n"))
        events.append((lines["event"], json.loads(lines["data"])))
    return events
