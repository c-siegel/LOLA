#!/bin/sh
# Downloads the on-device German text-to-speech (TTS) model — Kyutai's Pocket TTS as an int8 ONNX
# export — verifies it, and stores a gzipped copy of every file next to it. Used by projects that
# let the visitor's device speak (BROWSER_TTS_ENABLED, Project.tts_browser_enabled) and by the
# latency test page. In Docker, the `tts-model` service (Compose profile "browser-tts") runs it.
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

# The preset voices: Kyutai's own voice states for the German model, from the ungated repository.
# They fit the export above because everything that generates speech (flow LM, flow net, Mimi
# decoder) is bit-identical in Kyutai's gated and ungated October weights — only the voice encoder
# differs (zeroed in the ungated one). Shipped as Kyutai publishes them; the browser converts the
# one a project uses to the export's state layout (see "voices" in manifest.json and
# docs/pocket-tts-plan.md). The same allowlist as the server's (local-tts/app/voices.py): no
# non-commercial (cosette, jean) or undocumented (juergen, rafael) voices. Each device downloads
# only its project's voice (4–8 MB); all of them take ~140 MB here.
VOICE_REPO="kyutai/pocket-tts-without-voice-cloning"
VOICE_REVISION="ab248779e0718743c013e20b2d812ace7c8f4bf7"
# What projects without an allowed preset (or with a cloud provider's voice name) speak with.
DEFAULT_VOICE="alba"
# name, sha256
VOICES="
alba e34da39d613abe74a8a255426c816738372f085a83604c0c441845d7ae6c8192
anna 54e036b70a09478ed11a5108c1e651c6d59ddd63db8191ad9b8264c0cc0d677e
vera b3e44170a5a4fa97ecdabaac23706cc06e03bd3e9c0492b2aa43582c6cdc18cc
fantine b77367001a30d0862e83741f19ba9263870d882310e19a0cac3ba8befe406b04
charles 81199ad1a12f63dc2d47df1da3c55ca010cfe9e222ddffbeea056c372450af0b
paul 3b4afcddd3c88a4daa3a19a8e3d84a1c08cb06483442d3623d10bd00bdbe9bf5
eponine 73cddc2c86df453d4a0d8be05814a9e9d8c050be03cf2ce8bd9df0624c4c8a57
azelma 0df5e6ab44f1adccd6717ddac4444a8a82e25bb9b2ef98c73aa20fd033cf87cb
george a6c1a5b927b8481dbac08beada45f00b478716aeaf81f080f98c6ba04e65c12b
mary aa2f706eeed7ff410778b3e23070b8f6dd27c27460b76cdd1baef4f7d7d61da5
jane bd1bc91fd38a724b14f5c9cb4d67327f968568beb28d72fe7bb81f0fba75b685
michael cf14510456e4a0047160f052ccaa35a24b012eddf260b593b64dfa9fa1fe6d73
eve de30237de9ab96251ca7d73746f1ecc8184b91b8f025f8c06096f7a33afe7bbf
daan 1ab0faf983edda0f314db4c843e162858b7f424fe530ec93e7d78379ab3c8b6b
marius 1919d2fed89aa8f894753569fe15079f20abc4c9859ed9da889c8b282b244449
javert c6ed2465f41a015d78c4dee4be1d4a517e25db3cb43e944e27f984e70006822f
bill_boerst 15c4f662fdd05a1d379a589466ce31a4fa34ebdec9a3e9d94ec6ef5d8a1efbd1
peter_yearsley 50360c335454f22bb80353e83070f33ccb08d7223449855f4078a3ebce6ca70d
stuart_bell 60062fc7b9f52bcdf7cfdfc574abb84e476595979283bdadfcd53cf6b5b3e384
caro_davy 1f19753a2d06366db2dc261bf9026f582836cde579fea9857a21e9f28c474362
estelle ea90dd538585661872343983fdf5ccff64e0013d749675d7dff81fcfe4dbe747
giovanni 479b21d57d3fc2d0d6350ecdd265950f7fe7845c1a3da38dbc40414930a1c649
lola 66466f5601e1420824a7f5bb852c8807532d38cd8e29002e936b98bfb0e59a24
"

# Who recorded each voice, and the source recording's licence (see local-tts/app/voices.py).
voice_credit() {
  case "$1" in
    alba) echo "Alba MacKenna (CC BY 4.0)" ;;
    anna) echo "VCTK corpus, speaker p228 (CC BY 4.0)" ;;
    vera) echo "VCTK corpus, speaker p229 (CC BY 4.0)" ;;
    fantine) echo "VCTK corpus, speaker p244 (CC BY 4.0)" ;;
    charles) echo "VCTK corpus, speaker p254 (CC BY 4.0)" ;;
    paul) echo "VCTK corpus, speaker p259 (CC BY 4.0)" ;;
    eponine) echo "VCTK corpus, speaker p262 (CC BY 4.0)" ;;
    azelma) echo "VCTK corpus, speaker p303 (CC BY 4.0)" ;;
    george) echo "VCTK corpus, speaker p315 (CC BY 4.0)" ;;
    mary) echo "VCTK corpus, speaker p333 (CC BY 4.0)" ;;
    jane) echo "VCTK corpus, speaker p339 (CC BY 4.0)" ;;
    michael) echo "VCTK corpus, speaker p360 (CC BY 4.0)" ;;
    eve) echo "VCTK corpus, speaker p361 (CC BY 4.0)" ;;
    daan) echo "CML-TTS corpus (CC BY 4.0)" ;;
    marius|javert) echo "Unmute Voice Donation Project (CC0)" ;;
    bill_boerst|peter_yearsley|stuart_bell|caro_davy) echo "Voice-Zero (CC0)" ;;
    estelle) echo "Kyutai Labs (CC0)" ;;
    giovanni|lola) echo "Mozilla Common Voice (CC0)" ;;
  esac
}
# Kyutai's tokenizer.json, the same vocabulary as the export's tokenizer.model (identical token ids,
# checked on umlauts, quotes and emoji) but readable by a small JavaScript tokenizer, so the browser
# needs no SentencePiece binary. pocket-tts itself uses this file.
TOKENIZER_JSON_SHA="2d77849811e3d1b6ae80e5b11b203f57e1847901104d8a0573b34f46f8dddb53"

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

# install <path under $TARGET> <sha256> <url>... — the first URL that delivers the expected bytes
# wins; then a gzipped copy is made for Caddy.
install() {
  name="$1"; sha="$2"; shift 2
  file="$TARGET/$name"
  mkdir -p "$(dirname "$file")"
  if [ -f "$file" ] && hash_ok "$file" "$sha"; then
    echo "ok        $name"
  else
    installed=""
    for url in "$@"; do
      echo "download  $name  ($(echo "$url" | cut -d / -f 4-5))"
      if curl -fsSL --retry 3 -o "$file.part" "$url" && hash_ok "$file.part" "$sha"; then
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
}

mkdir -p "$TARGET"

echo "$FILES" | while read -r name sha; do
  [ -n "$name" ] || continue
  urls=""
  for source in $SOURCES; do
    urls="$urls https://huggingface.co/${source%@*}/resolve/${source#*@}/$REPO_PATH/$name"
  done
  # shellcheck disable=SC2086
  install "$name" "$sha" $urls
done

echo "$VOICES" | while read -r name sha; do
  [ -n "$name" ] || continue
  install "voices/$name.safetensors" "$sha" \
    "https://huggingface.co/$VOICE_REPO/resolve/$VOICE_REVISION/languages/german/embeddings/$name.safetensors"
done
install "tokenizer.json" "$TOKENIZER_JSON_SHA" \
  "https://huggingface.co/$VOICE_REPO/resolve/$VOICE_REVISION/languages/german/tokenizer.json"

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
  LOLA distributes these files unchanged; the voice-cloning encoder is not included. The graphs'
  weights are bit-identical to Kyutai's ungated release
  (https://huggingface.co/$VOICE_REPO, revision $VOICE_REVISION).
- **Tokenizer \`tokenizer.json\`:** Kyutai's, unchanged, from
  https://huggingface.co/$VOICE_REPO/tree/$VOICE_REVISION/languages/german (CC BY 4.0).
- **Voices \`voices/*.safetensors\`:** Kyutai's predefined German voice states, unchanged, from
  https://huggingface.co/$VOICE_REPO/tree/$VOICE_REVISION/languages/german/embeddings (CC BY 4.0),
  computed from these recordings (https://huggingface.co/kyutai/tts-voices and the sources named
  in pocket-tts' \`pocket_tts/utils/utils.py\`):
$(echo "$VOICES" | while read -r name sha; do [ -n "$name" ] && echo "  - $name: $(voice_credit "$name")"; done)
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
    "tokenizerJson": { "path": "tokenizer.json", "bytes": $(size tokenizer.json) },
    "textConditioner": { "path": "text_conditioner.onnx", "bytes": $(size text_conditioner.onnx) },
    "flowLmMain": { "path": "flow_lm_main_int8.onnx", "bytes": $(size flow_lm_main_int8.onnx) },
    "flowLmFlow": { "path": "flow_lm_flow_int8.onnx", "bytes": $(size flow_lm_flow_int8.onnx) },
    "mimiDecoder": { "path": "mimi_decoder_int8.onnx", "bytes": $(size mimi_decoder_int8.onnx) }
  },
  "voiceFormat": "kyutai: per layer <layer>/cache float32 [2,1,T,16,64] (keys, values) and <layer>/offset = T; the export wants <layer>/cache_k and cache_v as float16 [1,capacity,16,64] with rows past T zero, and step = T",
  "defaultVoice": "$DEFAULT_VOICE",
  "voices": {
$(echo "$VOICES" | while read -r name sha; do
  [ -n "$name" ] || continue
  printf '    "%s": { "path": "voices/%s.safetensors", "bytes": %s, "credit": "%s" },\n' \
    "$name" "$name" "$(size "voices/$name.safetensors")" "$(voice_credit "$name")"
done | sed '$ s/,$//')
  }
}
EOF
gzip -9 -c "$TARGET/manifest.json.part" > "$TARGET/manifest.json.gz.part"
mv "$TARGET/manifest.json.gz.part" "$TARGET/manifest.json.gz"
mv "$TARGET/manifest.json.part" "$TARGET/manifest.json"

chmod -R a+rX "$TARGET_ROOT" 2>/dev/null || echo "Warning: could not make $TARGET_ROOT world-readable — the web server may answer 403." >&2

echo "Done: $TARGET"
echo "Transfer size per device (gzip, with the default voice): $(cat "$TARGET"/*.gz "$TARGET/voices/$DEFAULT_VOICE.safetensors.gz" | wc -c | awk '{printf "%.0f MB", $1 / 1000000}')"
