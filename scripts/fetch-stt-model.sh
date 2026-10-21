#!/bin/sh
# Downloads the on-device speech-to-text (STT) model that visitors' browsers load in the public
# chat (Parakeet Redux, see frontend/src/lib/parakeetStt.ts), verifies it, and stores a gzipped
# copy of every file next to it.
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
    echo "download  $name"
    curl -fsSL --retry 3 -o "$file.part" "https://huggingface.co/$REPO/resolve/$REVISION/$name"
    if ! hash_ok "$file.part" "$sha"; then
      rm -f "$file.part"
      echo "Checksum mismatch for $name — refusing to install it." >&2
      exit 1
    fi
    mv "$file.part" "$file"
    rm -f "$file.gz"
  fi
  # Checked by decompressing, not just by existence: an interrupted earlier run can leave a
  # truncated .gz behind, and Caddy would keep serving it — browsers then stall partway through.
  if [ ! -f "$file.gz" ] || ! gzip -t "$file.gz" 2>/dev/null; then
    echo "gzip      $name"
    # Written to .part first and renamed when complete, so an interruption never leaves a
    # broken .gz under the name Caddy serves.
    gzip -9 -c "$file" > "$file.gz.part"
    mv "$file.gz.part" "$file.gz"
  fi
done

# Written last, and the frontend loads nothing without it: until every file above is in place,
# browsers just fall back to server transcription instead of loading a half-downloaded model.
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

# Also repairs files from an earlier run under a stricter umask. On storage with its own ACLs
# (e.g. a TrueNAS dataset) chmod can be refused — then grant read access in the ACL instead.
chmod -R a+rX "$TARGET_ROOT" 2>/dev/null || echo "Warning: could not make $TARGET_ROOT world-readable — the web server may answer 403." >&2

echo "Done: $TARGET"
echo "Transfer size per device (gzip): $(cat "$TARGET"/*.gz | wc -c | awk '{printf "%.0f MB", $1 / 1000000}')"
