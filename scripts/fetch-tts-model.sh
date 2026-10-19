#!/bin/sh
# Downloads the on-device German text-to-speech (TTS) model — Kyutai's Pocket TTS as an int8 ONNX
# export — verifies it, and stores a gzipped copy of every file next to it. For now only the
# latency test page uses it (see docs/pocket-tts-plan.md, phase 4).
#
# Why this export: it's built from Kyutai's German weights of 2026-10-01 (the newest), and in our
# benchmark it was the fastest and smallest variant at server-level intelligibility. Its repo
# describes itself as an internal build artifact whose files may move, so the files come from the
# project's own unmodified mirror first, with the original as fallback, both pinned to a commit and
# every file to its checksum: a moved or changed file fails the download instead of installing
# something else, and a server that has the files keeps them.
#
# Why gzip: unlike Parakeet's ternary weights, int8 weights only shrink by about a quarter
# (~126 MB -> ~93 MB per device), but that's still worth it; Caddy serves the .gz to every browser
# that accepts gzip, exactly like the speech recognition model (see fetch-stt-model.sh).
#
# Licence: the weights are CC BY 4.0 (Kyutai Labs). Passing them on requires the attribution in
# NOTICE.md, which this script writes next to the files. The voice-cloning encoder is deliberately
# not downloaded: the browser only needs preset voices, and the encoder comes from Kyutai's gated
# repository.
#
# Usage:
#   scripts/fetch-tts-model.sh                              # dev: writes ./models (served by Vite)
#   scripts/fetch-tts-model.sh "$LOLA_DATA_DIR/models"  # production, by hand
#
# Safe to re-run: files whose checksum already matches are not downloaded again. Plain POSIX sh
# and BusyBox-compatible, like fetch-stt-model.sh.

set -eu
# Caddy reads these files as a different, unprivileged user (see fetch-stt-model.sh).
umask 022

TARGET_ROOT="${1:-$(dirname "$0")/../models}"
# Bump VERSION whenever the checksums change — the version is part of every URL, and Caddy and the
# browser's Cache API both keep a file under the same URL indefinitely.
VERSION="v1"
REPO_PATH="onnx/german_2026-10"
# Tried in order, each pinned to a commit; the checksums below make both deliver the same bytes.
# First the project's own unmodified mirror (it won't move), then the original export.
MIRROR_REPO="c-siegel/pocket-tts-en-de-onnx-int8"
MIRROR_REVISION="53784174f63e20cc1e9290253df770cbfcc8a142"
REPO="Lauro00000/k6-onnx-int8"
REVISION="b79d4c1fa3898a98e43fb6f5412d3dd4cb5cc904"
SOURCES="$MIRROR_REPO@$MIRROR_REVISION $REPO@$REVISION"
TARGET="$TARGET_ROOT/pocket-tts-de/$VERSION"

# file name, sha256
FILES="
bundle.json e023602bfe54b7129ed30f8afba5998a6966476da016f872357aa82f544400d4
bos_before_voice.npy 6f0a678f8584bceb9b872d108a2466587575a834076edefd3d912ef884fa5e9e
tokenizer.model b3d6fb751a0bbc0b18df78687a03c11e4f2fbb0fa4b8f85e83064cd9d80ee34b
text_conditioner.onnx 68a59b1d91e7a07d7eb7c2fed93f8268c3771d77888b1b47580cfaf257dfe623
flow_lm_main_int8.onnx aa5b8ecf61d15df0e2e60da3c3a4257511de024838861a59ab0466458320c064
flow_lm_flow_int8.onnx ad08d7860fe1242cc86d076d9fc310d51e21d5b0ddeb9b9eb156a7c19d1a5258
mimi_decoder_int8.onnx 7a3f8fb364ba0ecc427f9a849ad40fcb8ad8c4ce8e094f2b91ced643bf6f9d3d
"

# BusyBox's sha256sum has no --status, so compare the printed hash instead of using -c.
hash_ok() { [ "$(sha256sum "$1" | cut -d ' ' -f 1)" = "$2" ]; }

for tool in curl sha256sum gzip; do
  command -v "$tool" >/dev/null || { echo "Missing required tool: $tool" >&2; exit 1; }
done

mkdir -p "$TARGET"

echo "$FILES" | while read -r name sha; do
  [ -n "$name" ] || continue
  file="$TARGET/$name"
  if [ -f "$file" ] && hash_ok "$file" "$sha"; then
    echo "ok        $name"
  else
    installed=""
    for source in $SOURCES; do
      echo "download  $name  (${source%@*})"
      if curl -fsSL --retry 3 -o "$file.part" "https://huggingface.co/${source%@*}/resolve/${source#*@}/$REPO_PATH/$name" \
        && hash_ok "$file.part" "$sha"; then
        mv "$file.part" "$file"
        installed=1
        break
      fi
      rm -f "$file.part"
      echo "          unavailable or checksum mismatch, trying the next source" >&2
    done
    if [ -z "$installed" ]; then
      echo "No source delivered $name with the expected checksum — refusing to install it." >&2
      exit 1
    fi
    rm -f "$file.gz"
  fi
  # Checked by decompressing, not just by existence (see fetch-stt-model.sh).
  if [ ! -f "$file.gz" ] || ! gzip -t "$file.gz" 2>/dev/null; then
    echo "gzip      $name"
    gzip -9 -c "$file" > "$file.gz.part"
    mv "$file.gz.part" "$file.gz"
  fi
done

# CC BY 4.0, section 3(a): creator and copyright notice, licence, source link, and what was
# changed. Kept next to the files so it travels with every copy of them.
cat > "$TARGET/NOTICE.md" <<EOF
# Pocket TTS (German), ONNX int8

These model files are **© Kyutai Labs**, licensed under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). They are not covered by LOLA's MIT licence.

- **Model:** Pocket TTS by Kyutai Labs (Manu Orsini, Simon Rouard, Gabriel De Marmiesse, Václav
  Volhejn, Neil Zeghidour, Alexandre Défossez), German weights of 2026-10-01 —
  https://huggingface.co/kyutai/pocket-tts (revision 3e82814a68665eec246ff649b14c71331f955c06).
- **Changes:** converted to ONNX and quantized to int8 (dynamic, per channel) by Lauro00000 with
  lookbe/pocket-tts-onnx-export (MIT) — https://huggingface.co/$REPO/tree/$REVISION/$REPO_PATH.
  Unmodified mirror: https://huggingface.co/$MIRROR_REPO/tree/$MIRROR_REVISION/$REPO_PATH.
  LOLA distributes these files unchanged; the voice-cloning encoder is not included.
- **No warranty:** the licensor offers the material as-is (CC BY 4.0, section 5).

Kyutai asks that the model is not used to imitate or clone anyone's voice without their explicit
and lawful consent, nor to present what it says as a genuine recording of a real person or event.
EOF

# Written last, and a loader should load nothing without it: until every file above is in place,
# it's simply missing. Raw sizes for progress bars, as in fetch-stt-model.sh.
size() { wc -c < "$TARGET/$1" | tr -d ' '; }
cat > "$TARGET/manifest.json.part" <<EOF
{
  "version": "$VERSION",
  "source": "https://huggingface.co/$REPO/tree/$REVISION/$REPO_PATH",
  "mirror": "https://huggingface.co/$MIRROR_REPO/tree/$MIRROR_REVISION/$REPO_PATH",
  "license": "CC-BY-4.0",
  "notice": "NOTICE.md",
  "sampleRate": 24000,
  "files": {
    "bundle": { "path": "bundle.json", "bytes": $(size bundle.json) },
    "bosBeforeVoice": { "path": "bos_before_voice.npy", "bytes": $(size bos_before_voice.npy) },
    "tokenizer": { "path": "tokenizer.model", "bytes": $(size tokenizer.model) },
    "textConditioner": { "path": "text_conditioner.onnx", "bytes": $(size text_conditioner.onnx) },
    "flowLmMain": { "path": "flow_lm_main_int8.onnx", "bytes": $(size flow_lm_main_int8.onnx) },
    "flowLmFlow": { "path": "flow_lm_flow_int8.onnx", "bytes": $(size flow_lm_flow_int8.onnx) },
    "mimiDecoder": { "path": "mimi_decoder_int8.onnx", "bytes": $(size mimi_decoder_int8.onnx) }
  }
}
EOF
gzip -9 -c "$TARGET/manifest.json.part" > "$TARGET/manifest.json.gz.part"
mv "$TARGET/manifest.json.gz.part" "$TARGET/manifest.json.gz"
mv "$TARGET/manifest.json.part" "$TARGET/manifest.json"

chmod -R a+rX "$TARGET_ROOT" 2>/dev/null || echo "Warning: could not make $TARGET_ROOT world-readable — the web server may answer 403." >&2

echo "Done: $TARGET"
echo "Transfer size per device (gzip): $(cat "$TARGET"/*.gz | wc -c | awk '{printf "%.0f MB", $1 / 1000000}')"
