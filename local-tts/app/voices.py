"""
Voice Catalog

Which voices this service may speak with, and where each one comes from. Two kinds:

- **Presets:** Kyutai's precomputed voice states, published for every language model in the ungated
  `kyutai/pocket-tts-without-voice-cloning` repo. They work without voice cloning.
- **Donated voices:** recordings from Kyutai's Unmute Voice Donation Project
  (`kyutai/tts-voices/voice-donations`, CC0). Plain audio, so they need voice cloning (the gated
  weights, see Settings.hf_token) to become a voice state.

Only voices whose source licence allows LOLA's use are listed. Left out on purpose:
cosette (Expresso) and jean (EARS) are non-commercial; juergen and rafael have no documented
source (pocket-tts only names a file) — add them once Kyutai documents their licence.

How to use:
    from app.voices import PRESETS, default_voice, voice_catalog

    voice_catalog(cloning=True)  # [{"id": "alba", "kind": "preset", ...}, ...]
"""

import json
from pathlib import Path

# LOLA's language codes -> Pocket TTS model names (pocket_tts/config/<name>.yaml).
LANGUAGES = {
    "de": "german",
    "en": "english",
    "fr": "french",
    "it": "italian",
    "es": "spanish",
    "pt": "portuguese",
    "nl": "dutch",
}

# Preset name -> source licence (pocket_tts/utils/utils.py::_ORIGINS_OF_PREDEFINED_VOICES, then
# the README of kyutai/tts-voices for the dataset's licence).
PRESETS = {
    "alba": "CC BY 4.0",  # Alba MacKenna, recorded for Kyutai
    "anna": "CC BY 4.0",  # VCTK p228
    "vera": "CC BY 4.0",  # VCTK p229
    "fantine": "CC BY 4.0",  # VCTK p244
    "charles": "CC BY 4.0",  # VCTK p254
    "paul": "CC BY 4.0",  # VCTK p259
    "eponine": "CC BY 4.0",  # VCTK p262
    "azelma": "CC BY 4.0",  # VCTK p303
    "george": "CC BY 4.0",  # VCTK p315
    "mary": "CC BY 4.0",  # VCTK p333
    "jane": "CC BY 4.0",  # VCTK p339
    "michael": "CC BY 4.0",  # VCTK p360
    "eve": "CC BY 4.0",  # VCTK p361
    "daan": "CC BY 4.0",  # CML-TTS, Dutch
    "marius": "CC0",  # voice donation "Selfie"
    "javert": "CC0",  # voice donation "Butter"
    "bill_boerst": "CC0",  # Voice-Zero
    "peter_yearsley": "CC0",  # Voice-Zero
    "stuart_bell": "CC0",  # Voice-Zero
    "caro_davy": "CC0",  # Voice-Zero
    "estelle": "CC0",  # Kyutai's own recording, French
    "giovanni": "CC0",  # Mozilla Common Voice, Italian
    "lola": "CC0",  # Mozilla Common Voice, Spanish
}

# A native speaker where a preset is allowed (Kyutai's own defaults are juergen and rafael for
# German and Portuguese, both not allowed yet, see above). German stays on alba, which the browser
# engine also uses (scripts/fetch-tts-model.sh), so both speak alike.
DEFAULT_VOICES = {
    "de": "alba",
    "en": "alba",
    "fr": "estelle",
    "it": "giovanni",
    "es": "lola",
    "pt": "alba",
    "nl": "daan",
}

DONATION_PREFIX = "donation:"

# Names of the donated recordings at the revision pinned in synthesis.py (VOICES_REV), each
# available as "<name>.wav" and a cleaned "<name>_enhanced.wav". Regenerate with the Hugging Face
# API: GET /api/models/kyutai/tts-voices/tree/<rev>/voice-donations.
DONATED_VOICES: list[str] = json.loads((Path(__file__).parent / "donated_voices.json").read_text(encoding="utf-8"))


def default_voice(language: str) -> str:
    return DEFAULT_VOICES[language]


def donated_name(voice: str) -> str | None:
    """The donated recording `voice` stands for ("donation:<name>"), or None if it isn't one."""
    if not voice.startswith(DONATION_PREFIX):
        return None
    name = voice.removeprefix(DONATION_PREFIX)
    return name if name in DONATED_VOICES else None


def voice_catalog(cloning: bool, donated: list[str] | None = None) -> list[dict]:
    """Every voice a request may name, for the backend's voice picker. Donated voices only with
    `cloning`, and only those in `donated` (Settings.donated_voices; None means all)."""
    voices = [{"id": name, "label": name.replace("_", " ").title(), "kind": "preset", "licence": licence} for name, licence in PRESETS.items()]
    if cloning:
        allowed = DONATED_VOICES if donated is None else [name for name in DONATED_VOICES if name in donated]
        voices += [
            {"id": DONATION_PREFIX + name, "label": name.replace("_", " "), "kind": "donation", "licence": "CC0"}
            for name in allowed
        ]
    return voices
