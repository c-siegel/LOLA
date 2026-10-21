#!/bin/sh
# Licence gate: fails if any installed Python package isn't under one of the permissive licences
# below. Run it inside an environment that has exactly one component installed (backend/, rag/,
# rag-eval/ or local-tts/)
# plus pip-licenses — CI does this for every release (see .github/workflows/docker-publish.yml):
#
#   pip install pip-licenses && sh scripts/check-licences.sh
#
# Allowed: MIT, BSD, Apache-2.0, ISC, PSF, MPL-2.0 (file-level copyleft; we use those packages
# unmodified), Unlicense/public domain, HPND/MIT-CMU, Zlib. Everything else — GPL, LGPL, AGPL,
# SSPL, Commons Clause, CC-BY-NC, or no licence metadata at all — fails the build until someone has
# looked at it and either replaced the package or added it to REVIEWED below with a reason.
set -eu

ALLOWED="MIT;BSD;Apache;ISC;PSF;Python Software Foundation;Mozilla Public License 2.0;MPL-2.0;MPL 2.0;Unlicense;Public Domain;HPND;CMU;Zlib"

# Reviewed by hand. Each of these publishes its licence only as full text (no SPDX identifier or
# classifier), which pip-licenses can't match:
#   tiktoken         MIT (licence text in the package metadata)
#   py_rust_stemmers MIT (github.com/qdrant/py-rust-stemmers; a fastembed dependency)
#   pocket-tts       MIT (licence file in the wheel, no metadata; local-tts/'s engine — its model
#                    weights are CC BY 4.0, credited on the Credits page)
# Plus our own packages and the CI-only tooling that runs this check.
REVIEWED="tiktoken py_rust_stemmers py-rust-stemmers pocket-tts pocket_tts lola-backend lola-rag lola-rag-eval lola-local-tts pip-licenses prettytable wcwidth tomli"

# shellcheck disable=SC2086
pip-licenses --partial-match --allow-only="$ALLOWED" --ignore-packages $REVIEWED >/dev/null
echo "Licence check passed."
