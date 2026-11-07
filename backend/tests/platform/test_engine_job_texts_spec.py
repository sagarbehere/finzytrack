"""The engine-jobs spec cites what the user reads, and never restates it (S0079, BL-0192; INV-19; spec §11).

`backend/scripts/gen_engine_job_texts.py` renders every text of `app.platform.engine_jobs.text`, `save_text`,
`recovery_text` and the registry's engine-job codes into `design/generated/engine-job-texts.md`, each under a key
(`msg:…`); `scripts/check_generated.py` keeps that file equal to the code. `design/specs/engine-jobs.md` says when
each text shows and cites it by its key. Here, both ways: every key the spec cites exists, every rendered text is
cited, and no sentence of the reference is also quoted in the spec (one home: the code).
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SPEC = REPO / "design" / "specs" / "engine-jobs.md"
TEXTS = REPO / "design" / "generated" / "engine-job-texts.md"

KEY = re.compile(r"`(msg:[A-Za-z0-9_.]+)`")
ROW = re.compile(r"^\| `(msg:[A-Za-z0-9_.]+)` \| (?:[^|\\]|\\.)* \| (.*) \|$")
SPLIT = re.compile(r"<[^<>]+>|\*\*(?:Message|Next step):\*\*")
MIN_FRAGMENT = 40
"""Fragments shorter than this (a phase, "Cancelled.") are not compared: they recur as plain words."""


def _texts() -> dict[str, str]:
    rows: dict[str, str] = {}
    for line in TEXTS.read_text(encoding="utf-8").splitlines():
        m = ROW.match(line)
        if m:
            rows[m.group(1)] = m.group(2).replace("\\|", "|")
    return rows


def test_every_key_the_spec_cites_exists_and_every_rendered_text_is_cited() -> None:
    texts = _texts()
    assert len(texts) >= 80, "the reference's rows were not found"
    cited = set(KEY.findall(SPEC.read_text(encoding="utf-8")))
    assert sorted(cited - set(texts)) == [], "keys the spec cites that the reference does not have"
    assert sorted(set(texts) - cited) == [], "texts in the reference that the spec never cites"


def test_no_sentence_of_the_reference_is_also_quoted_in_the_spec() -> None:
    spec = " ".join(SPEC.read_text(encoding="utf-8").split())
    doubled = sorted(
        f"{key}: {fragment}"
        for key, text in _texts().items()
        for fragment in (f.strip(" .,;:") for f in SPLIT.split(text))
        if len(fragment) >= MIN_FRAGMENT and fragment in spec
    )
    assert doubled == [], "words with two homes: cite the key instead"
