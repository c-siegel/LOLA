"""
Speech Text Normalizer

Rewrites assistant reply text into what a TTS (text-to-speech) engine should say: Markdown is
stripped, numbers, units, dates, abbreviations and math become words ("1,5 m/s" -> "eins Komma
fünf Meter pro Sekunde"). The work is done by normt2s; this module only configures it for LOLA.
Only the copy handed to the TTS engine changes — the reply stored and shown in the chat keeps its
formatting.

How to use:
    from app.features.ai.tts.normalizer import normalize_for_speech

    spoken_text = normalize_for_speech(reply_text, language=project.spoken_language)
    # With the project owner's own word list (see pronunciation.py):
    spoken_text = normalize_for_speech(reply_text, project.spoken_language, pronunciation=rules)

    # A streamed LLM reply, chunk by chunk (see features/chat/pipeline.py::stream_turn):
    stream = speech_stream(project.spoken_language, rules)
    for delta in llm_deltas:
        for piece in stream.feed_pieces(delta):
            show(piece.raw)
            speak(piece.spoken)
    last = stream.flush_piece()  # None if nothing was left
"""

import threading

from normt2s import Normalizer, RuleSet, StreamNormalizer

_DEFAULT_LANGUAGE = "de"
# Gesture and conversation-plan markers ("::nod_yes::", "::done_2::"). motion.py::extract_markers
# already cuts them out before speech; this is the safety net for one that slips through, so it is
# silent instead of read out. Same shape as motion.py's catch-all, including not directly following
# a word or colon, so code like "std::vector::size" stays.
_MARKER_PATTERN = r"(?<![\w:])::[ \t]*[A-Za-z][\w -]{0,38}?[ \t]*::"
# normt2s reads "·" only inside $...$, but LLMs write "F = m · a" in plain text too.
_MULTIPLICATION_DOT = {"de": "mal", "en": "times"}

# Chunk sizes for a streamed reply: the first chunk is short so the avatar starts speaking early,
# later ones are longer so the voice doesn't stutter between many tiny clips, and a run without any
# sentence end is still cut eventually so it can't stall the audio.
_FIRST_CHUNK_MIN_CHARS = 25
_MIN_CHUNK_CHARS = 80
_MAX_CHUNK_CHARS = 300

_normalizers: dict[str, Normalizer] = {}
_lock = threading.Lock()


def _normalizer(language: str) -> Normalizer:
    """The shared normalizer for `language` — a Normalizer is thread-safe, so one per language
    serves every request."""
    if language not in ("de", "en"):
        language = _DEFAULT_LANGUAGE
    with _lock:
        if language not in _normalizers:
            # The chat renders Markdown, and LLMs write bare LaTeX ("\frac{1}{2}") without $...$.
            normalizer = Normalizer(language, markdown="strip", latex_bare="speak")
            # The lookbehind hides the literal start a stream needs to never cut inside a marker.
            normalizer.add_protected(_MARKER_PATTERN, spoken="", opener="::")
            normalizer.add_symbol("·", _MULTIPLICATION_DOT[language])
            _normalizers[language] = normalizer
        return _normalizers[language]


def normalize_for_speech(
    text: str,
    language: str,
    pronunciation: RuleSet | None = None,
    applied: list | None = None,
) -> str:
    """The spoken form of `text`.

    `pronunciation` is the teacher's own word list (see pronunciation.py); it runs inside normt2s,
    after Markdown is stripped and before numbers become words. `applied`, if given, collects the
    rules from it that matched (for the dashboard's test box).
    """
    return _normalizer(language).normalize(text, rules=pronunciation or None, applied=applied)


def speech_stream(language: str, pronunciation: RuleSet | None = None) -> StreamNormalizer:
    """A stream that buffers LLM deltas and releases them as Pieces of (raw, spoken) text at
    sentence ends — never inside a number and its unit, an abbreviation or a marker. The raw parts
    join to exactly the input, the spoken parts to normalize_for_speech of it."""
    return _normalizer(language).stream(
        max_buffer=_MAX_CHUNK_CHARS,
        first_min_chars=_FIRST_CHUNK_MIN_CHARS,
        min_chars=_MIN_CHUNK_CHARS,
        rules=pronunciation or None,
    )
