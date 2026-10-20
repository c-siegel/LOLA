"""Route tests for the voice library (/voice-clips) and how a project's local TTS uses a clip:
uploads are checked by their content and length and need consent, clips stay private to their
owner, and the local-TTS sidecar receives a clip the first time it's needed."""

import io
import json
import math
import struct
import wave
from pathlib import Path

import av
import httpx
import pytest
from sqlmodel import Session, select

from app.core.config import settings
from app.features.ai.tts import local
from app.features.media.models import VoiceClip
from app.features.media import voices_router
from conftest import browser_url, create_project, login_as, make_user, new_client, publish

SIDECAR_WAV = b"RIFF-fake-synthesized-wav"


def tone_wav(seconds: float, rate: int = 48000, channels: int = 2) -> bytes:
    """A WAV file with a plain sine tone — real, decodable audio of an exact length."""
    frames = int(seconds * rate)
    samples = (int(8000 * math.sin(2 * math.pi * 220 * i / rate)) for i in range(frames))
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"".join(struct.pack("<h", s) * channels for s in samples))
    return buffer.getvalue()


def tone_webm(seconds: float) -> bytes:
    """The same tone as WebM/Opus — the format Chrome's MediaRecorder records in."""
    buffer = io.BytesIO()
    with av.open(buffer, mode="w", format="webm") as container:
        stream = container.add_stream("libopus", rate=48000, layout="mono")
        source = av.open(io.BytesIO(tone_wav(seconds, channels=1)))
        for frame in source.decode(audio=0):
            frame.pts = None
            for packet in stream.encode(frame):
                container.mux(packet)
        for packet in stream.encode(None):
            container.mux(packet)
    return buffer.getvalue()


def upload(client, content: bytes, *, name: str = "Meine Stimme", consent: bool = True, filename: str = "clip.wav"):
    return client.post(
        "/voice-clips",
        files={"file": (filename, content, "application/octet-stream")},
        data={"name": name, "consent": "true" if consent else "false"},
    )


@pytest.fixture
def sidecar(monkeypatch):
    """A fake local-TTS sidecar that only knows clips it was sent via PUT /voices/{sha}.
    Returns the list of (method, path, body) requests it received."""
    monkeypatch.setattr(settings, "local_tts_enabled", True)
    requests: list[tuple[str, str, bytes]] = []
    stored: set[str] = set()

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        requests.append((request.method, path, request.content))
        if request.method == "PUT":
            stored.add(path.rsplit("/", 1)[1])
            return httpx.Response(204)
        if request.method == "DELETE":
            stored.discard(path.rsplit("/", 1)[1])
            return httpx.Response(204)
        voice = json.loads(request.content).get("voice_sha256")
        if voice and voice not in stored:
            return httpx.Response(404, json={"detail": "VOICE_NOT_STORED"})
        return httpx.Response(200, content=SIDECAR_WAV)

    monkeypatch.setattr(local, "_client", httpx.Client(transport=httpx.MockTransport(handler)))
    return requests


def synth_bodies(requests) -> list[dict]:
    return [json.loads(body) for method, path, body in requests if path == "/synthesize"]


def test_upload_is_stored_as_normalized_wav(client, teacher) -> None:
    response = upload(client, tone_wav(5))
    assert response.status_code == 200, response.text
    clip = response.json()
    assert clip["name"] == "Meine Stimme"
    assert clip["durationSeconds"] == pytest.approx(5, abs=0.05)
    assert clip["consentConfirmedAt"]

    stored = client.get(browser_url(clip["fileUrl"]))
    assert stored.headers["content-type"] == "audio/wav"
    with wave.open(io.BytesIO(stored.content)) as wav:
        assert (wav.getnchannels(), wav.getframerate(), wav.getsampwidth()) == (1, 24000, 2)
    assert [c["id"] for c in client.get("/voice-clips").json()] == [clip["id"]]


def test_a_browser_recording_is_accepted(client, teacher) -> None:
    response = upload(client, tone_webm(4), filename="recording.webm")
    assert response.status_code == 200, response.text
    assert response.json()["durationSeconds"] == pytest.approx(4, abs=0.1)


@pytest.mark.parametrize("seconds", [30.4, 45])
def test_clips_over_the_limit_are_cut_to_30_seconds(client, teacher, seconds) -> None:
    # The in-browser recorder ends a few hundred ms over "30 s"; longer uploads are trimmed too.
    response = upload(client, tone_wav(seconds, channels=1))
    assert response.status_code == 200, response.text
    assert response.json()["durationSeconds"] == pytest.approx(30, abs=0.01)


@pytest.mark.parametrize(
    ("content", "consent", "name", "code"),
    [
        (tone_wav(5), False, "Stimme", "VOICE_CLIP_CONSENT_REQUIRED"),
        (tone_wav(5), True, "   ", "VOICE_CLIP_NAME_REQUIRED"),
        (b"just some text, not audio at all", True, "Stimme", "VOICE_CLIP_INVALID_AUDIO"),
        (b"RIFF\x00\x00\x00\x00WAVEgarbage", True, "Stimme", "VOICE_CLIP_INVALID_AUDIO"),
        (tone_wav(1), True, "Stimme", "VOICE_CLIP_TOO_SHORT"),
    ],
    # Readable ids: the raw bytes would otherwise become the test id, which overflows the
    # PYTEST_CURRENT_TEST environment variable on Windows.
    ids=["no-consent", "blank-name", "text-file", "broken-wav", "too-short"],
)
def test_rejected_uploads(client, teacher, content, consent, name, code) -> None:
    response = upload(client, content, name=name, consent=consent)
    assert response.status_code == 400
    assert response.json() == {"detail": code}
    assert client.get("/voice-clips").json() == []


def test_clips_are_private_to_their_owner(client, teacher, engine, sidecar) -> None:
    clip = upload(client, tone_wav(5)).json()

    other = login_as(new_client(), make_user(engine, email="other@example.com"))
    assert other.get("/voice-clips").json() == []
    assert other.get(browser_url(clip["fileUrl"])).status_code == 404
    assert other.post(f"/voice-clips/{clip['id']}/preview", json={"text": "Hallo"}).status_code == 404
    assert other.delete(f"/voice-clips/{clip['id']}").status_code == 404
    assert new_client().get(browser_url(clip["fileUrl"])).status_code == 401
    assert client.get("/voice-clips").json()[0]["id"] == clip["id"]


def test_a_clip_stays_private_when_a_published_project_speaks_with_it(client, teacher, sidecar) -> None:
    # Unlike avatars and backgrounds, students only ever hear the cloned voice, never the file.
    clip = upload(client, tone_wav(5)).json()
    project = create_project(client, ttsEnabled=True, ttsVoiceClipId=clip["id"])
    publish(client, project["id"])

    response = new_client().get(browser_url(clip["fileUrl"]))
    assert response.status_code == 401
    assert client.get(browser_url(clip["fileUrl"])).status_code == 200


def test_oversized_uploads_are_refused(client, teacher, monkeypatch) -> None:
    monkeypatch.setattr(voices_router, "_MAX_UPLOAD_BYTES", 1024)
    response = upload(client, tone_wav(5))
    assert response.status_code == 400
    assert response.json() == {"detail": "VOICE_CLIP_TOO_LARGE"}


def test_project_speaks_with_its_clip_and_sends_it_to_the_sidecar_once(client, teacher, sidecar) -> None:
    clip = upload(client, tone_wav(5)).json()
    project = create_project(client, ttsEnabled=True, startPrompt="Hallo", ttsVoiceClipId=clip["id"])
    assert project["ttsVoiceClipId"] == clip["id"]

    assert client.post(f"/projects/{project['id']}/start-audio").status_code == 200
    assert client.post(f"/projects/{project['id']}/start-audio").status_code == 200

    puts = [(path, body) for method, path, body in sidecar if method == "PUT"]
    assert len(puts) == 1  # sent once, then the sidecar has it
    stored_wav = client.get(browser_url(clip["fileUrl"])).content
    assert puts[0][1] == stored_wav
    sha = puts[0][0].rsplit("/", 1)[1]
    assert all(body.get("voice_sha256") == sha for body in synth_bodies(sidecar))


def test_someone_elses_clip_is_never_used(client, teacher, engine, sidecar) -> None:
    other = login_as(new_client(), make_user(engine, email="other@example.com"))
    foreign_clip = upload(other, tone_wav(5)).json()
    project = create_project(client, ttsEnabled=True, startPrompt="Hallo", ttsVoiceClipId=foreign_clip["id"])

    assert client.post(f"/projects/{project['id']}/start-audio").status_code == 200
    assert "voice_sha256" not in synth_bodies(sidecar)[-1]
    assert not any(method == "PUT" for method, _, _ in sidecar)


def test_deleting_a_clip_resets_projects_and_their_start_audio(client, teacher, sidecar) -> None:
    clip = upload(client, tone_wav(5)).json()
    project = create_project(client, ttsEnabled=True, startPrompt="Hallo", ttsVoiceClipId=clip["id"])
    client.post(f"/projects/{project['id']}/start-audio")
    assert client.get(f"/projects/{project['id']}").json()["startAudioUrl"]

    assert client.delete(f"/voice-clips/{clip['id']}").status_code == 204
    after = client.get(f"/projects/{project['id']}").json()
    assert after["ttsVoiceClipId"] is None
    assert after["startAudioUrl"] is None
    assert client.get("/voice-clips").json() == []
    assert any(method == "DELETE" for method, _, _ in sidecar)


def test_preview_speaks_in_the_cloned_voice(client, teacher, sidecar) -> None:
    clip = upload(client, tone_wav(5)).json()
    response = client.post(f"/voice-clips/{clip['id']}/preview", json={"text": "Hello there", "language": "en"})
    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == SIDECAR_WAV
    assert synth_bodies(sidecar)[-1]["language"] == "en"


def test_preview_needs_local_tts(client, teacher, monkeypatch) -> None:
    monkeypatch.setattr(settings, "local_tts_enabled", False)
    clip = upload(client, tone_wav(5)).json()
    response = client.post(f"/voice-clips/{clip['id']}/preview", json={"text": "Hallo"})
    assert response.status_code == 400
    assert response.json() == {"detail": "TTS_NOT_CONFIGURED"}



def test_deleting_the_account_deletes_its_voice_clips(client, teacher, engine, sidecar) -> None:
    upload(client, tone_wav(5))
    with Session(engine) as session:
        file_path = Path(session.exec(select(VoiceClip)).one().file_path)
    assert file_path.is_file()

    assert client.delete("/me").status_code == 200
    with Session(engine) as session:
        assert session.exec(select(VoiceClip)).all() == []
    assert not file_path.exists()
    assert any(method == "DELETE" for method, _, _ in sidecar)
