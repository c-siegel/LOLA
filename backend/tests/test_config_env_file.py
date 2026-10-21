"""Which .env file the settings read — checked in a subprocess, because `settings` is built once
when app.core.config is first imported."""

import os
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PRINT_TTS_URL = "from app.core.config import settings; print(settings.local_tts_url)"


def _local_tts_url(env_file: str) -> str:
    env = {k: v for k, v in os.environ.items() if k != "LOCAL_TTS_URL"}
    env["LOLA_ENV_FILE"] = env_file
    result = subprocess.run(
        [sys.executable, "-c", PRINT_TTS_URL], cwd=BACKEND_DIR, env=env, capture_output=True, text=True, check=True
    )
    return result.stdout.strip()


def test_env_file_is_read_from_the_configured_path(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("LOCAL_TTS_URL=http://from-env-file:9999\n")
    assert _local_tts_url(str(env_file)) == "http://from-env-file:9999"


def test_empty_env_file_setting_reads_no_file_so_defaults_apply():
    assert _local_tts_url("") == "http://tts-local:8080"


def test_the_test_suite_itself_does_not_read_the_repo_env_file():
    from app.core.config import settings

    assert settings.model_config["env_file"] is None


def test_litellm_does_not_copy_the_repo_env_file_into_the_environment():
    # litellm runs load_dotenv() on import unless LITELLM_MODE is PRODUCTION (see conftest.py).
    assert os.environ["LITELLM_MODE"] == "PRODUCTION"
