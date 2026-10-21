"""
LOLA Evaluation Service

Ragas sends anonymous usage analytics unless RAGAS_DO_NOT_TRACK is "true". It's forced here,
before anything can import ragas, so no deployment can switch it on by accident: this service
handles teachers' material and their keys, and has no business reporting anything elsewhere.
"""

import os

os.environ["RAGAS_DO_NOT_TRACK"] = "true"

import logging  # noqa: E402

# Instructor logs every failed parse at ERROR with the judge's raw reply and the validation error,
# which quotes it — fragments of teachers' material and answers. app/scoring.py and
# app/testset.py already log each failure, by type only.
logging.getLogger("instructor").setLevel(logging.CRITICAL)
