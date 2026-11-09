#!/bin/sh
# Downloads the speech-to-text (STT) model (Parakeet Redux) and the voice activity detector (Silero
# VAD), verifies them, and stores a gzipped copy of every file next to it. Visitors' browsers load it for on-device recognition (see
# frontend/src/lib/parakeetStt.ts), and the backend transcribes with the same files (see
# backend/app/features/ai/stt/parakeet_local.py) — required there, there's no fallback engine.
#
# Why self-hosted and pre-compressed: the model is ~380 MB raw, but its weights are ternary
# (-1/0/+1) packed into 4-bit blocks, so gzip shrinks the download to ~170 MB without changing a
# single weight. Hugging Face's CDN only serves the raw files. Caddy serves the .gz variant to
# every browser that accepts gzip (`file_server { precompressed gzip }` in docker/Caddyfile),
# so the compression work happens once here instead of on every request.
#
# Usage:
#   scripts/fetch-stt-model.sh                              # dev: writes ./models (served by Vite)
#   scripts/fetch-stt-model.sh "$LOLA_DATA_DIR/models"  # production, by hand
# In production this normally runs by itself: the frontend image ships this script, and the
# `stt-model` service in docker/docker-compose.yml runs it on every `docker compose up`.
#
# Safe to re-run: files whose checksum already matches are not downloaded again. Plain POSIX sh
# and BusyBox-compatible, since it also runs inside the Alpine-based frontend image.

set -eu
# Caddy reads these files as a different, unprivileged user than whoever runs this script (root in
# the stt-model service, see docker/docker-compose.yml) — a restrictive umask would make every
# download answer 403.
umask 022

TARGET_ROOT="${1:-$(dirname "$0")/../models}"
# Bump VERSION together with REVISION — the version is part of every URL, and Caddy and the
# browser's Cache API both keep a file under the same URL indefinitely.
VERSION="v1"
REPO="mrfakename/parakeet-redux-ONNX"
REVISION="0f1264f3ce3bcdab31eac2f04cdd5b720eb5fda7"
TARGET="$TARGET_ROOT/parakeet-redux/$VERSION"

# file name, sha256
FILES="
preprocessor.onnx 3de5742f5780fc8615c1d883a2a60e28621ff13d2e20d24d76e94b9e4168445f
vad-model.onnx 18dfbe344307f9eda4735d4da1fe1cd6e3be27055a6c25b4a4a2e6fe0bffde50
decoder_joint-model.int8.onnx a3d6e48bddd218743c486e0b2f3263c0e0e2f8d42785dd1e86d18a4bfc5fd79d
encoder-model.onnx ade2c65c188776b3662db620a553ab82ac7e9ca11a7be6b696bcc5190f75382e
vocab.txt d58544679ea4bc6ac563d1f545eb7d474bd6cfa467f0a6e2c1dc1c7d37e3c35d
"

# Silero VAD (MIT, snakers4/silero-vad): speech detection in the browser, which finds the pauses
# the server fallback cuts a recording at (see frontend/src/workers/vadWorker.ts). Chosen over
# Parakeet's own VAD model after a comparison, see docs/stt-plan.md (phase 3, step 0).
VAD_VERSION="v6.2"
VAD_COMMIT="5cd7945676eb32225748052e2e6a0580e4686a08" # tag v6.2.3
VAD_SHA256="1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"
VAD_TARGET="$TARGET_ROOT/silero-vad/$VAD_VERSION"

# BusyBox's sha256sum has no --status, so compare the printed hash instead of using -c.
hash_ok() { [ "$(sha256sum "$1" | cut -d ' ' -f 1)" = "$2" ]; }

# fetch_verified <url> <file> <sha256>: downloads the file unless an intact copy is already there.
fetch_verified() {
  if [ -f "$2" ] && hash_ok "$2" "$3"; then
    echo "ok        $(basename "$2")"
    return
  fi
  echo "download  $(basename "$2")"
  curl -fsSL --retry 3 -o "$2.part" "$1"
  if ! hash_ok "$2.part" "$3"; then
    rm -f "$2.part"
    echo "Checksum mismatch for $(basename "$2") — refusing to install it." >&2
    exit 1
  fi
  mv "$2.part" "$2"
  rm -f "$2.gz"
}

# ensure_gzip <file>: (re)writes the .gz copy Caddy serves.
ensure_gzip() {
  # Checked by decompressing, not just by existence: an interrupted earlier run can leave a
  # truncated .gz behind, and Caddy would keep serving it — browsers then stall partway through.
  if [ ! -f "$1.gz" ] || ! gzip -t "$1.gz" 2>/dev/null; then
    echo "gzip      $(basename "$1")"
    # Written to .part first and renamed when complete, so an interruption never leaves a
    # broken .gz under the name Caddy serves.
    gzip -9 -c "$1" > "$1.gz.part"
    mv "$1.gz.part" "$1.gz"
  fi
}

for tool in curl sha256sum gzip; do
  command -v "$tool" >/dev/null || { echo "Missing required tool: $tool" >&2; exit 1; }
done

mkdir -p "$TARGET"

echo "$FILES" | while read -r name sha; do
  [ -n "$name" ] || continue
  fetch_verified "https://huggingface.co/$REPO/resolve/$REVISION/$name" "$TARGET/$name" "$sha"
  ensure_gzip "$TARGET/$name"
done

# Written last, and neither the frontend nor the backend loads anything without it: a
# half-downloaded model is never used.
# The frontend also reads raw (uncompressed) sizes from here for its progress bar: with gzip
# transfer encoding, the Content-Length header carries the *compressed* size while the browser
# hands the page decompressed bytes, so the header alone can't say how far along it is.
size() { wc -c < "$TARGET/$1" | tr -d ' '; }
cat > "$TARGET/manifest.json.part" <<EOF
{
  "version": "$VERSION",
  "source": "https://huggingface.co/$REPO/tree/$REVISION",
  "sampleRate": 16000,
  "files": {
    "preprocessor": { "path": "preprocessor.onnx", "bytes": $(size preprocessor.onnx) },
    "vad": { "path": "vad-model.onnx", "bytes": $(size vad-model.onnx) },
    "decoder": { "path": "decoder_joint-model.int8.onnx", "bytes": $(size decoder_joint-model.int8.onnx) },
    "encoder": { "path": "encoder-model.onnx", "bytes": $(size encoder-model.onnx) },
    "vocab": { "path": "vocab.txt", "bytes": $(size vocab.txt) }
  }
}
EOF
gzip -9 -c "$TARGET/manifest.json.part" > "$TARGET/manifest.json.gz.part"
mv "$TARGET/manifest.json.gz.part" "$TARGET/manifest.json.gz"
mv "$TARGET/manifest.json.part" "$TARGET/manifest.json"

mkdir -p "$VAD_TARGET"
fetch_verified "https://raw.githubusercontent.com/snakers4/silero-vad/$VAD_COMMIT/src/silero_vad/data/silero_vad.onnx" \
  "$VAD_TARGET/silero_vad.onnx" "$VAD_SHA256"
ensure_gzip "$VAD_TARGET/silero_vad.onnx"
# Same manifest format as Parakeet's (read by frontend/src/workers/vadWorker.ts), written last.
cat > "$VAD_TARGET/manifest.json.part" <<EOF
{
  "version": "$VAD_VERSION",
  "source": "https://github.com/snakers4/silero-vad/tree/$VAD_COMMIT",
  "sampleRate": 16000,
  "files": {
    "vad": { "path": "silero_vad.onnx", "bytes": $(wc -c < "$VAD_TARGET/silero_vad.onnx" | tr -d ' ') }
  }
}
EOF
gzip -9 -c "$VAD_TARGET/manifest.json.part" > "$VAD_TARGET/manifest.json.gz.part"
mv "$VAD_TARGET/manifest.json.gz.part" "$VAD_TARGET/manifest.json.gz"
mv "$VAD_TARGET/manifest.json.part" "$VAD_TARGET/manifest.json"

# Also repairs files from an earlier run under a stricter umask. On storage with its own ACLs
# (e.g. a TrueNAS dataset) chmod can be refused — then grant read access in the ACL instead.
chmod -R a+rX "$TARGET_ROOT" 2>/dev/null || echo "Warning: could not make $TARGET_ROOT world-readable — the web server may answer 403." >&2

echo "Done: $TARGET and $VAD_TARGET"
echo "Transfer size per device (gzip): $(cat "$TARGET"/*.gz | wc -c | awk '{printf "%.0f MB", $1 / 1000000}')"
