"""
Concurrency Load Test For The Public Chat

Fires N concurrent "virtual students" at one published project's public chat, the way a real
class would during a lesson, while a separate task polls /health to see whether the server
freezes under that load.

What is this for?
This is the load test named in the concurrency-hardening plan: point it at a published project
before/after a fix and compare the /health latency trace it prints. A trace with multi-second
gaps means the whole server froze (see: transcription running on the event loop,
app/features/chat/public_router.py::transcribe) — /health does no work of its own, so it can only be slow
if the one worker process is stuck doing something else entirely. A trace that stays flat while
chat latency alone rises under load means requests queued instead of freezing everything — that
is the goal after the Phase 0 fixes.

This script is intentionally a black box: it only talks HTTP to a running instance (like a real
browser would), never imports anything from `app`, so it exercises the real deployed behavior
(thread pool, DB pool, rate limits, WAL, ...) instead of calling internal functions directly.

How to use:
    # Text-only chat, 30 students, 60 seconds:
    python scripts/loadtest.py --base-url http://localhost:8000 --slug my-project

    # Include voice input too (needs a short sample recording you provide yourself, in one of
    # the accepted formats — webm/ogg/mp4/wav/mpeg):
    python scripts/loadtest.py --slug my-project --audio-file sample.webm

    # Use the streamed reply endpoint instead of the plain one:
    python scripts/loadtest.py --slug my-project --stream

    # Password-protected project:
    python scripts/loadtest.py --slug my-project --unlock-password 1234
"""

import argparse
import asyncio
import time
from dataclasses import dataclass, field

import httpx

_SAMPLE_MESSAGES = [
    "Can you explain that again?",
    "What is the main idea here?",
    "I don't understand this part.",
    "Can you give me an example?",
    "Why does this happen?",
]

_AUDIO_CONTENT_TYPES = {
    "webm": "audio/webm",
    "ogg": "audio/ogg",
    "mp4": "audio/mp4",
    "wav": "audio/wav",
    "mp3": "audio/mpeg",
    "mpeg": "audio/mpeg",
}


@dataclass
class RequestStats:
    """Latencies (ms) collected for one kind of request across every virtual student."""

    label: str
    samples: list[float] = field(default_factory=list)
    errors: int = 0

    def record(self, ms: float) -> None:
        self.samples.append(ms)

    def summary(self) -> str:
        if not self.samples:
            return f"{self.label}: no successful requests ({self.errors} errors)"
        s = sorted(self.samples)
        p50 = s[len(s) // 2]
        p95 = s[min(int(len(s) * 0.95), len(s) - 1)]
        return (
            f"{self.label}: n={len(s)} errors={self.errors} "
            f"min={s[0]:.0f}ms p50={p50:.0f}ms p95={p95:.0f}ms max={s[-1]:.0f}ms"
        )


async def poll_health(
    base_url: str, stop: asyncio.Event, interval: float, gap_threshold_ms: float
) -> tuple[list[float], list[tuple[float, float]]]:
    """Poll /health every `interval` seconds until `stop` is set.

    Returns (every poll's own latency in ms, and a list of (elapsed_seconds, gap_ms) for every
    pair of consecutive polls that were further apart than gap_threshold_ms) — a gap means the
    single worker process didn't get around to answering /health at all for that long, which
    only happens if it's stuck doing something else for the whole process, not just handling one
    slow request among others.
    """
    latencies: list[float] = []
    gaps: list[tuple[float, float]] = []
    run_start = time.monotonic()
    last_poll = run_start
    async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
        while not stop.is_set():
            poll_start = time.monotonic()
            gap_ms = (poll_start - last_poll) * 1000
            if latencies and gap_ms > gap_threshold_ms:
                gaps.append((poll_start - run_start, gap_ms))
            last_poll = poll_start
            try:
                await client.get("/health")
                latencies.append((time.monotonic() - poll_start) * 1000)
            except httpx.HTTPError:
                pass
            await asyncio.sleep(interval)
    return latencies, gaps


async def run_student(
    student_id: int,
    base_url: str,
    slug: str,
    duration: float,
    message_interval: float,
    audio_bytes: bytes | None,
    audio_content_type: str,
    stream: bool,
    unlock_password: str | None,
    chat_stats: RequestStats,
    transcribe_stats: RequestStats,
) -> None:
    """Simulate one student: load the page once, then repeatedly (optionally) transcribe a
    voice clip and send a chat message, until `duration` seconds have passed.

    Uses its own AsyncClient (and therefore its own cookie jar) per student, the same way each
    real student is a separate browser with its own ah_visitor_id cookie.
    """
    deadline = time.monotonic() + duration
    async with httpx.AsyncClient(base_url=base_url, timeout=60.0) as client:
        try:
            await client.get(f"/api/v1/public/{slug}")
        except httpx.HTTPError as exc:
            print(f"[student {student_id}] failed to load page: {exc}")
            return

        headers: dict[str, str] = {}
        if unlock_password:
            try:
                resp = await client.post(f"/api/v1/public/{slug}/unlock", json={"password": unlock_password})
                resp.raise_for_status()
                headers["X-Chat-Unlock-Token"] = resp.json()["unlockToken"]
            except httpx.HTTPError as exc:
                print(f"[student {student_id}] failed to unlock: {exc}")
                return

        history: list[dict] = []
        turn = 0
        while time.monotonic() < deadline:
            message = _SAMPLE_MESSAGES[turn % len(_SAMPLE_MESSAGES)]
            turn += 1

            if audio_bytes is not None:
                start = time.monotonic()
                try:
                    resp = await client.post(
                        f"/api/v1/public/{slug}/transcriptions",
                        headers=headers,
                        files={"audio": ("sample.webm", audio_bytes, audio_content_type)},
                    )
                    resp.raise_for_status()
                    transcribe_stats.record((time.monotonic() - start) * 1000)
                except httpx.HTTPError:
                    transcribe_stats.errors += 1

            start = time.monotonic()
            try:
                if stream:
                    async with client.stream(
                        "POST",
                        f"/api/v1/public/{slug}/messages/stream",
                        headers=headers,
                        json={"message": message, "history": history},
                    ) as resp:
                        resp.raise_for_status()
                        async for _ in resp.aiter_lines():
                            pass  # draining the stream is enough to measure total duration
                else:
                    resp = await client.post(
                        f"/api/v1/public/{slug}/messages", headers=headers, json={"message": message, "history": history}
                    )
                    resp.raise_for_status()
                chat_stats.record((time.monotonic() - start) * 1000)
            except httpx.HTTPError:
                chat_stats.errors += 1

            history.append({"role": "user", "content": message})
            history.append({"role": "assistant", "content": "(reply)"})
            await asyncio.sleep(message_interval)


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default="http://localhost:8000", help="Backend base URL.")
    parser.add_argument("--slug", required=True, help="Slug of a published project to hit.")
    parser.add_argument("--students", type=int, default=30, help="Number of concurrent virtual students.")
    parser.add_argument("--duration", type=float, default=60.0, help="Seconds each student keeps chatting.")
    parser.add_argument(
        "--message-interval", type=float, default=15.0, help="Seconds between one student's own messages."
    )
    parser.add_argument("--stream", action="store_true", help="Use /messages/stream instead of /messages.")
    parser.add_argument(
        "--audio-file",
        help="Path to a short sample recording (webm/ogg/mp4/wav/mpeg) to also load-test /transcriptions. "
        "Provide your own — none is bundled with this script.",
    )
    parser.add_argument("--unlock-password", help="Chat password, if the project is password-protected.")
    parser.add_argument("--health-interval", type=float, default=0.1, help="Seconds between /health polls.")
    parser.add_argument(
        "--freeze-threshold-ms",
        type=float,
        default=500.0,
        help="Gap between consecutive /health polls that counts as a server-wide freeze.",
    )
    args = parser.parse_args()

    audio_bytes: bytes | None = None
    audio_content_type = "audio/webm"
    if args.audio_file:
        with open(args.audio_file, "rb") as f:
            audio_bytes = f.read()
        ext = args.audio_file.rsplit(".", 1)[-1].lower()
        audio_content_type = _AUDIO_CONTENT_TYPES.get(ext, "audio/webm")

    chat_stats = RequestStats(label="/messages/stream" if args.stream else "/messages")
    transcribe_stats = RequestStats(label="/transcriptions")

    stop = asyncio.Event()
    health_task = asyncio.create_task(poll_health(args.base_url, stop, args.health_interval, args.freeze_threshold_ms))

    print(f"Starting {args.students} virtual students for {args.duration:.0f}s against {args.base_url}/api/v1/public/{args.slug} ...")
    run_start = time.monotonic()
    await asyncio.gather(
        *(
            run_student(
                i,
                args.base_url,
                args.slug,
                args.duration,
                args.message_interval,
                audio_bytes,
                audio_content_type,
                args.stream,
                args.unlock_password,
                chat_stats,
                transcribe_stats,
            )
            for i in range(args.students)
        )
    )
    elapsed = time.monotonic() - run_start

    stop.set()
    health_latencies, gaps = await health_task

    print(f"\nDone in {elapsed:.1f}s.\n")
    print(chat_stats.summary())
    if audio_bytes is not None:
        print(transcribe_stats.summary())

    print(f"\n/health: n={len(health_latencies)}", end=" ")
    if health_latencies:
        s = sorted(health_latencies)
        print(f"min={s[0]:.0f}ms p50={s[len(s) // 2]:.0f}ms max={s[-1]:.0f}ms")
    else:
        print()

    if gaps:
        print(
            f"\n{len(gaps)} freeze(s) detected — a gap between /health polls over "
            f"{args.freeze_threshold_ms:.0f}ms means the server stopped responding to ANYTHING "
            f"for that long, not just one slow request:"
        )
        for elapsed_s, gap_ms in gaps:
            print(f"  at +{elapsed_s:.1f}s: {gap_ms:.0f}ms gap")
    else:
        print("\nNo freezes detected — /health stayed responsive throughout the run.")


if __name__ == "__main__":
    asyncio.run(main())
