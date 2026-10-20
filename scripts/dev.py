#!/usr/bin/env python3
"""
Local development runner: set up and start every LOLA service with one command.

    python scripts/dev.py setup     # once: venvs, pip/npm installs, database migrations
    python scripts/dev.py           # start backend + frontend + the optional services enabled in .env
    python scripts/dev.py start backend frontend rag   # start only these

Windows: use `py -3.11 scripts/dev.py ...` (any Python >= 3.11). Ctrl+C stops everything.

Written in plain Python (standard library only) rather than as a bash or PowerShell script so the
same file works on Linux, macOS and Windows. Each service keeps its own .venv, exactly as in the
per-service READMEs; this script only saves typing the commands.
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
IS_WINDOWS = os.name == "nt"


@dataclass
class Service:
    name: str
    directory: str
    port: int
    # .env variable that switches an optional service on, and the one holding the URL the backend
    # uses to reach it — the port is read from that URL so the two can't drift apart.
    enabled_var: str | None = None
    url_var: str | None = None
    # Extra environment for the service, as paths relative to its directory. Its own settings
    # default to Docker's /data volume, which on a dev machine would be C:\data or a root-owned dir.
    data_dirs: dict[str, str] = field(default_factory=dict)
    pip_extras: str = "[dev]"


SERVICES = [
    Service("backend", "backend", 8000),
    Service(
        "rag", "rag", 8090, "RAG_ENABLED", "RAG_SERVICE_URL",
        data_dirs={"RAG_DATA_DIR": ".data"},
    ),
    Service(
        "rag-eval", "rag-eval", 8091, "RAG_EVALUATION_ENABLED", "RAG_EVAL_SERVICE_URL",
        data_dirs={"RAG_EVAL_DATA_DIR": ".data"},
    ),
    Service(
        "local-tts", "local-tts", 8080, "LOCAL_TTS_ENABLED", "LOCAL_TTS_URL",
        data_dirs={
            "TTS_MODEL_CACHE_DIR": ".cache",
            "TTS_CUSTOM_VOICES_DIR": ".data/custom-voices",
        },
        pip_extras="",
    ),
    Service("frontend", "frontend", 5173),
]
BY_NAME = {s.name: s for s in SERVICES}
COLORS = {"backend": 36, "rag": 35, "rag-eval": 34, "local-tts": 33, "frontend": 32}


def read_env_file(path: Path) -> dict[str, str]:
    """Minimal .env parser covering what .env.example uses: KEY=value, quotes, trailing # comments."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip().removeprefix("export ").strip()
        value = value.strip()
        if value[:1] in ("'", '"') and value.count(value[0]) >= 2:
            value = value[1 : value.index(value[0], 1)]
        elif " #" in value:
            value = value.split(" #", 1)[0].rstrip()
        values[key] = value
    return values


def is_true(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def default_services(env: dict[str, str]) -> list[str]:
    return [s.name for s in SERVICES if s.enabled_var is None or is_true(env.get(s.enabled_var))]


def venv_python(service: Service) -> Path:
    venv = ROOT / service.directory / ".venv"
    return venv / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")


def npm() -> str:
    # On Windows npm is npm.cmd, which subprocess only finds through its full path.
    found = shutil.which("npm")
    if not found:
        sys.exit("npm not found. Install Node.js (see frontend/README.md) and open a new terminal.")
    return found


def port_for(service: Service, env: dict[str, str]) -> int:
    if service.url_var and env.get(service.url_var):
        parsed = urlparse(env[service.url_var])
        if parsed.port:
            return parsed.port
    return service.port


def run(cmd: list[str], cwd: Path) -> None:
    print(f"\n$ {' '.join(str(c) for c in cmd)}   (in {cwd.relative_to(ROOT)})", flush=True)
    result = subprocess.run(cmd, cwd=cwd)
    if result.returncode != 0:
        sys.exit(f"Command failed with exit code {result.returncode}.")


# ---------------------------------------------------------------- setup


def setup(names: list[str]) -> None:
    if sys.version_info < (3, 11):
        sys.exit(
            f"Python >= 3.11 is needed, this is {sys.version.split()[0]}. "
            "On Windows run `py -3.11 scripts/dev.py setup` (`py --list` shows what's installed)."
        )
    if not (ROOT / ".env").exists():
        print("Note: no .env yet. Copy .env.example to .env and fill in the two secrets "
              "(see README.md) before starting the backend.")

    for name in names:
        service = BY_NAME[name]
        cwd = ROOT / service.directory
        print(f"\n=== {name} ===", flush=True)
        if name == "frontend":
            run([npm(), "ci"], cwd)
            continue
        python = venv_python(service)
        if not python.exists():
            run([sys.executable, "-m", "venv", ".venv"], cwd)
        run([str(python), "-m", "pip", "install", "--upgrade", "pip"], cwd)
        if name == "local-tts":
            # CPU-only build — the default PyPI torch wheel pulls several GB of unused CUDA libraries.
            run([str(python), "-m", "pip", "install", "torch",
                 "--index-url", "https://download.pytorch.org/whl/cpu"], cwd)
        run([str(python), "-m", "pip", "install", "-e", f".{service.pip_extras}"], cwd)
        if name == "backend" and (ROOT / ".env").exists():
            run([str(python), "-m", "alembic", "upgrade", "head"], cwd)

    print("\nSetup done. Start everything with: python scripts/dev.py")


# ---------------------------------------------------------------- start


def command_for(service: Service, port: int) -> list[str]:
    if service.name == "frontend":
        return [npm(), "run", "dev"]
    cmd = [str(venv_python(service)), "-m", "uvicorn", "app.main:app",
           "--host", "127.0.0.1", "--port", str(port)]
    if service.name == "backend":
        cmd.append("--reload")
    if service.name == "local-tts":
        cmd += ["--app-dir", "."]
    return cmd


def pump_output(name: str, proc: subprocess.Popen, width: int, color: bool) -> None:
    label = name.ljust(width)
    prefix = f"\033[{COLORS[name]}m{label} |\033[0m " if color else f"{label} | "
    assert proc.stdout is not None
    for line in proc.stdout:
        sys.stdout.write(prefix + line)
        sys.stdout.flush()


def stop(processes: dict[str, subprocess.Popen]) -> None:
    for proc in processes.values():
        if proc.poll() is not None:
            continue
        # uvicorn --reload and vite run child processes; stop the whole tree, not just the parent.
        if IS_WINDOWS:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
    deadline = time.monotonic() + 10
    for proc in processes.values():
        try:
            proc.wait(timeout=max(0.1, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            proc.kill()


def start(names: list[str]) -> None:
    file_env = read_env_file(ROOT / ".env")
    if not file_env:
        sys.exit("No .env found. Copy .env.example to .env and fill in the two secrets (see README.md).")
    # Real environment variables win over .env, the same precedence the backend's settings use.
    # The knowledge and TTS services don't read .env themselves, so they get it this way.
    # Unbuffered and UTF-8 so lines show up live and umlauts survive the pipe on Windows consoles.
    env = {**file_env, **os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}

    missing = [n for n in names if n != "frontend" and not venv_python(BY_NAME[n]).exists()]
    if "frontend" in names and not (ROOT / "frontend" / "node_modules").exists():
        missing.append("frontend")
    if missing:
        sys.exit(f"Not set up yet: {', '.join(missing)}. Run: python scripts/dev.py setup {' '.join(missing)}")

    if "backend" in names:
        run([str(venv_python(BY_NAME["backend"])), "-m", "alembic", "upgrade", "head"], ROOT / "backend")

    color = sys.stdout.isatty()
    if color and IS_WINDOWS:
        os.system("")  # switches the Windows console into ANSI mode so the colored prefixes render
    width = max(len(n) for n in names)
    processes: dict[str, subprocess.Popen] = {}
    threads = []
    print()
    for name in names:
        service = BY_NAME[name]
        cwd = ROOT / service.directory
        port = port_for(service, env)
        service_env = dict(env)
        for var, rel in service.data_dirs.items():
            if not env.get(var):
                (cwd / rel).mkdir(parents=True, exist_ok=True)
                service_env[var] = str(cwd / rel)
        # Vite always serves HTTPS (self-signed, see vite.config.ts) — the microphone needs it.
        scheme = "https" if name == "frontend" else "http"
        print(f"Starting {name} on {scheme}://localhost:{port}", flush=True)
        processes[name] = subprocess.Popen(
            command_for(service, port), cwd=cwd, env=service_env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", bufsize=1,
            start_new_session=not IS_WINDOWS,
        )
        thread = threading.Thread(target=pump_output, args=(name, processes[name], width, color), daemon=True)
        thread.start()
        threads.append(thread)
    print("\nPress Ctrl+C to stop all services.\n", flush=True)

    reported: set[str] = set()
    try:
        while len(reported) < len(processes):
            for name, proc in processes.items():
                if name not in reported and proc.poll() is not None:
                    reported.add(name)
                    print(f"*** {name} exited with code {proc.returncode} — the others keep running.", flush=True)
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopping...", flush=True)
    finally:
        stop(processes)
        for thread in threads:
            thread.join(timeout=2)


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up and run LOLA locally.")
    parser.add_argument("command", nargs="?", choices=["setup", "start"], default="start")
    parser.add_argument(
        "services", nargs="*", metavar="service",
        help=f"any of: {', '.join(BY_NAME)}. Default: backend, frontend and the optional services "
        "switched on in .env (RAG_ENABLED, RAG_EVALUATION_ENABLED, LOCAL_TTS_ENABLED).",
    )
    args = parser.parse_args()
    unknown = [n for n in args.services if n not in BY_NAME]
    if unknown:
        parser.error(f"unknown service {', '.join(unknown)} (choose from {', '.join(BY_NAME)})")

    env = {**read_env_file(ROOT / ".env"), **os.environ}
    names = args.services or default_services(env)
    if args.command == "setup":
        setup(names)
    else:
        start(names)


if __name__ == "__main__":
    main()
