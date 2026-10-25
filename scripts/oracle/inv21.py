"""The INV-21 goldens' second derivation and checks (S0022; spec design/specs/mirror/inv21-goldens.md).

`corpora/ledgers/inv21/goldens.json` was produced by running the released v0.2.2 code (the oracle, EP-29). This module
derives the same values a second way, from the written spec only (`design/specs/mirror/README.md`, "Ids" and
"Metadata JSON"): the TransactionId, the content-hash input and hash, and the metadata JSON. The two derivations must
agree; when they disagree, the spec or the golden is wrong. It also checks the corpus labels (each declared feature is
really there), the v0.2.2-equality promise (a v0.4 id differs from v0.2.2's only for an `import-key` entry or an ordinal
it frees) and the metadata-key inventory (`keys.toml`). Every check returns a list of problems; empty means it passes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import tomllib
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from beancount import loader
from beancount.core import account_types, amount, data

REPO = Path(__file__).resolve().parents[2]
CORPUS = REPO / "corpora" / "ledgers" / "inv21"
LEGACY_TAG = "v0.2.2"
BEANCOUNT = "3.2.0"
SOURCE_PREFIXES = ("Assets:", "Liabilities:")  # the literal text v0.2.2 matches (README "Ids")


# ---------------------------------------------------------------------------------------------------------------------
# The spec derivation (README "Ids" and "Metadata JSON"), written from the spec text.

def meta_text(value: Any) -> str:
    """A metadata value as the hash and the id read it: its Python text."""
    return value if isinstance(value, str) else str(value)


def chosen_account(txn: data.Transaction) -> tuple[Any, str]:
    """(the value the amount search compares, the account text before trimming)."""
    meta = txn.meta or {}
    if "source_account" in meta:
        value = meta["source_account"]
    else:
        value = next((p.account for p in txn.postings if p.account.startswith(SOURCE_PREFIXES)), None)
    if not value:  # absent, empty text, or a value Python treats as false
        value = txn.postings[0].account if txn.postings else ""
    return value, meta_text(value)


def hash_input(txn: data.Transaction) -> str:
    value, text = chosen_account(txn)
    amount = "0"
    for p in txn.postings:
        if isinstance(value, str) and p.account == value and p.units is not None:  # text against text
            amount = f"{p.units.number} {p.units.currency}"
            break
    return f"{txn.date.isoformat()}|{txn.payee or ''}|{txn.narration or ''}|{amount}|{text.strip()}"


def content_hash(txn: data.Transaction) -> str:
    return hashlib.sha256(hash_input(txn).encode("utf-8")).hexdigest()


def first_import_key(txn: data.Transaction) -> str | None:
    """The first element of the first posting `import-key` that has one; transaction-level keys do not count."""
    for p in txn.postings:
        value = (p.meta or {}).get("import-key")
        if isinstance(value, str) and value.split():
            return value.split()[0]
    return None


def id_rule(txn: data.Transaction) -> str:
    """Which TransactionId rule settles the entry: "id", "transaction_id", "k" or "fallback"."""
    meta = txn.meta or {}
    if "id" in meta:
        return "id"
    if "transaction_id" in meta:
        return "transaction_id"
    return "k" if first_import_key(txn) is not None else "fallback"


def transaction_ids(txns: list[data.Transaction]) -> list[str]:
    seen: Counter[str] = Counter()
    out = []
    for t in txns:
        meta = t.meta or {}
        rule = id_rule(t)
        if rule in ("id", "transaction_id"):
            out.append(meta_text(meta[rule]))
        elif rule == "k":
            out.append("k:" + str(first_import_key(t)))
        else:
            h = content_hash(t)
            out.append(h if seen[h] == 0 else f"{h}-{seen[h]}")
            seen[h] += 1
    return out


def json_value(value: Any) -> Any:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, dict):
        return {k if isinstance(k, str) else str(k): json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [json_value(v) for v in value]
    return str(value)


def metadata_json(meta: dict[str, Any] | None, root: str) -> str | None:
    """README "Metadata JSON"; `filename` made relative to the case folder (inv21-goldens.md, the one normalization)."""
    if not meta:
        return None
    out = {k if isinstance(k, str) else str(k): json_value(v) for k, v in meta.items()}
    fn = out.get("filename")
    if isinstance(fn, str) and fn.startswith(root + os.sep):
        out["filename"] = fn[len(root) + 1:].replace(os.sep, "/")
    return json.dumps(out)


# ---------------------------------------------------------------------------------------------------------------------
# Loading a case (copied to a temp folder so no cache or bytecode lands in the repo).

@dataclass
class Case:
    name: str
    root: str
    entries: list[data.Directive]
    errors: list[Any]
    options: dict[str, Any]
    files: dict[str, str] = field(default_factory=dict)  # relative path -> text

    @property
    def txns(self) -> list[data.Transaction]:
        return [e for e in self.entries if isinstance(e, data.Transaction)]

    def at(self, meta: dict[str, Any]) -> str:
        return f"{os.path.relpath(meta['filename'], self.root).replace(os.sep, '/')}:{meta['lineno']}"

    def own(self, entry: data.Directive) -> bool:
        return str(entry.meta.get("filename", "")).startswith(self.root + os.sep)


def load_case(folder: Path, tmp: Path) -> Case:
    dst = tmp / folder.name
    shutil.copytree(folder, dst)
    root = os.path.abspath(dst)
    entries, errors, options = loader.load_file(os.path.join(root, "main.beancount"))
    files = {p.relative_to(dst).as_posix(): p.read_text(encoding="utf-8") for p in sorted(dst.rglob("*.beancount"))}
    return Case(folder.name, root, entries, errors, options, files)


def derive(case: Case, directive_metadata: bool) -> dict[str, Any]:
    """Everything the goldens pin for a case, derived from the spec."""
    ids = transaction_ids(case.txns)
    rows = [{
        "at": case.at(t.meta), "flag": t.flag, "transaction_id": tid,
        "sha256": content_hash(t), "hash_input": hash_input(t),
        "metadata_json": metadata_json(t.meta, case.root),
        "posting_metadata_json": [metadata_json(p.meta, case.root) for p in t.postings],
    } for t, tid in zip(case.txns, ids)]
    out: dict[str, Any] = {"transactions": rows}
    if directive_metadata:
        out["directives"] = [{"at": case.at(e.meta), "type": type(e).__name__,
                              "metadata_json": metadata_json(e.meta, case.root)}
                             for e in case.entries if not isinstance(e, data.Transaction) and case.own(e)]
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Feature detectors: each says whether a case really shows a feature, from Beancount's output and the goldens.

Detector = Callable[[Case, list[dict[str, Any]], dict[str, "Case"], dict[str, Any]], bool]
HEX64 = re.compile(r"[0-9a-f]{64}")


def _fallback(c: Case, i: int) -> bool:
    """Settled by the content fallback (by rule, not by the id's shape: a legacy id can look like a hash)."""
    return id_rule(c.txns[i]) == "fallback"


def _accounts(t: data.Transaction) -> tuple[str, ...]:
    return tuple(p.account for p in t.postings)


def _same_hash_groups(rows: list[dict[str, Any]]) -> list[list[int]]:
    groups: dict[str, list[int]] = {}
    for i, r in enumerate(rows):
        groups.setdefault(r["sha256"], []).append(i)
    return [g for g in groups.values() if len(g) > 1]


def _src(t: data.Transaction) -> Any:
    return (t.meta or {}).get("source_account")


def _first_literal(t: data.Transaction) -> data.Posting | None:
    return next((p for p in t.postings if p.account.startswith(SOURCE_PREFIXES)), None)


def _variant_changed(case: Case, rows: list[dict[str, Any]], cases: dict[str, Case], goldens: dict[str, Any],
                     cfg: dict[str, Any], moved: bool) -> bool:
    """The same transactions as `variant_of` (by hash input and legs), at least one with another id; `moved`: some
    transaction is in another file than before (else every one stays in its file)."""
    base = cfg.get("variant_of")
    if not base or base not in cases or base not in goldens:
        return False
    other, orows = cases[base], goldens[base]["transactions"]

    def labels(c: Case, rs: list[dict[str, Any]], *extra: str) -> Counter:
        return Counter((r["hash_input"], _accounts(t)) + tuple(
            r["transaction_id"] if x == "id" else r["at"].rsplit(":", 1)[0] for x in extra)
            for t, r in zip(c.txns, rs))

    if len(rows) != len(case.txns) or len(orows) != len(other.txns) or labels(case, rows) != labels(other, orows):
        return False
    files_differ = labels(case, rows, "file") != labels(other, orows, "file")
    return files_differ == moved and labels(case, rows, "id") != labels(other, orows, "id")


def d_twins(c, rows, cases, goldens, cfg=None):
    return any(sum(_fallback(c, i) for i in g) >= 2 for g in _same_hash_groups(rows))


def d_twins_differ(c, rows, cases, goldens, cfg=None):
    return any(len({_accounts(c.txns[i]) for i in g if _fallback(c, i)}) >= 2 for g in _same_hash_groups(rows))


def d_pipe(c, rows, cases, goldens, cfg=None):
    return any(len({(c.txns[i].payee, c.txns[i].narration) for i in g}) >= 2 for g in _same_hash_groups(rows))


def d_lineno_tie(c, rows, cases, goldens, cfg=None):
    files: dict[tuple[Any, int], set[str]] = {}
    for t in c.txns:
        files.setdefault((t.date, t.meta["lineno"]), set()).add(t.meta["filename"])
    return any(len(f) > 1 for f in files.values())


def d_identified_twin(c, rows, cases, goldens, cfg=None):
    """A same-hash group where an entry with a legacy id or import key sits between two fallback entries."""
    for g in _same_hash_groups(rows):
        kinds = [_fallback(c, i) for i in g]
        if any(not kinds[j] and any(kinds[:j]) and any(kinds[j + 1:]) for j in range(len(kinds))):
            return True
    return False


def _texts(t: data.Transaction) -> list[str]:
    """The payee and narration texts of a transaction (absent ones left out)."""
    return [x for x in (t.payee, t.narration) if isinstance(x, str)]


def d_whitespace_only_difference(c, rows, cases, goldens, cfg=None):
    """Two transactions whose hash inputs differ, but not once whitespace is trimmed and collapsed in every field."""
    def squeeze(text: str) -> str:
        return "|".join(" ".join(f.split()) for f in text.split("|"))
    seen: dict[str, set[str]] = {}
    for x in rows:
        seen.setdefault(squeeze(x["hash_input"]), set()).add(x["hash_input"])
    return any(len(v) > 1 for v in seen.values())


FEATURES: dict[str, tuple[str, Detector]] = {
    # TransactionId rule 1 and 2: the legacy keys.
    "legacy-id": ("README Ids: the legacy `id` meta",
                  lambda c, r, cs, g, cfg=None: any("id" in t.meta for t in c.txns)),
    "legacy-transaction-id": ("README Ids: the legacy `transaction_id` meta",
                              lambda c, r, cs, g, cfg=None: any("transaction_id" in t.meta and "id" not in t.meta
                                                                for t in c.txns)),
    "id-over-transaction-id": ("README Ids: `id` before `transaction_id`",
                               lambda c, r, cs, g, cfg=None: any("transaction_id" in t.meta and "id" in t.meta
                                                                 for t in c.txns)),
    "non-text-id": ("README Ids: a legacy id that is not text is read as its Python text",
                    lambda c, r, cs, g, cfg=None: any("id" in t.meta and not isinstance(t.meta["id"], str)
                                                      for t in c.txns)),
    "shared-legacy-id": ("README Ids: a repeated legacy id is used as is (BL-0059)",
                         lambda c, r, cs, g, cfg=None: any(n > 1 for n in Counter(
                             meta_text(t.meta["id"]) for t in c.txns if "id" in t.meta).values())),
    "id-over-import-key": ("README Ids: a legacy id before `k:`",
                           lambda c, r, cs, g, cfg=None: any("id" in t.meta and first_import_key(t) for t in c.txns)),
    "identified-twin-takes-no-ordinal": ("README Ids: only entries that reach the fallback count for the ordinal",
                                         d_identified_twin),
    # The content fallback and its ordinals.
    "twins": ("README Ids: the n-th repeat of a hash gets `-n`", d_twins),
    "twins-differ-outside-hash": ("README Ids: the hash sees one leg; twins can differ elsewhere", d_twins_differ),
    "pipe-collision": ("README Ids: fields joined by `|` unescaped", d_pipe),
    "no-payee": ("README Ids: payee empty when absent", lambda c, r, cs, g, cfg=None: any(t.payee is None
                                                                                          for t in c.txns)),
    "non-ascii": ("README Ids: UTF-8 text", lambda c, r, cs, g, cfg=None: any(not x["hash_input"].isascii() for x in r)),
    # Payee and narration as written (S0032, seeded plant C04): nothing trimmed or collapsed.
    "padded-text": ("README Ids: a payee or narration with spaces around it is hashed with them",
                    lambda c, r, cs, g, cfg=None: any(x != x.strip() and x.strip() for t in c.txns for x in _texts(t))),
    "inner-whitespace": ("README Ids: two spaces, a tab or a line break inside a payee or narration are kept",
                         lambda c, r, cs, g, cfg=None: any(re.search(r"\s\s|[^\S ]", x.strip()) for t in c.txns
                                                           for x in _texts(t))),
    "blank-text": ("README Ids: a payee or narration of spaces only is not empty",
                   lambda c, r, cs, g, cfg=None: any(x and not x.strip() for t in c.txns for x in _texts(t))),
    "empty-text-payee": ("README Ids: an empty payee (empty text, not absent) hashes as empty",
                         lambda c, r, cs, g, cfg=None: any(t.payee == "" for t in c.txns)),
    "unicode-space": ("README Ids: a space outside ASCII (no-break space) is kept",
                      lambda c, r, cs, g, cfg=None: any(any(ch.isspace() and not ch.isascii() for ch in x)
                                                        for t in c.txns for x in _texts(t))),
    "multi-line-text": ("README Ids: a line break inside a narration is kept",
                        lambda c, r, cs, g, cfg=None: any("\n" in x for t in c.txns for x in _texts(t))),
    "whitespace-only-difference": ("README Ids: entries that differ only in whitespace get different hashes",
                                   d_whitespace_only_difference),
    "padding-transaction": ("README Ids: padding transactions are in the order too",
                            lambda c, r, cs, g, cfg=None: any(t.flag == "P" for t in c.txns)),
    "reorder-variant": ("README Ids: swapping twins in a file swaps their ids",
                        lambda c, r, cs, g, cfg=None: _variant_changed(c, r, cs, g, cfg or {}, moved=False)),
    "move-variant": ("README Ids: moving an entry to another file can change ordinals",
                     lambda c, r, cs, g, cfg=None: _variant_changed(c, r, cs, g, cfg or {}, moved=True)),
    "lineno-tie": ("README Ids: equal line numbers in two files keep Beancount's read order", d_lineno_tie),
    # Which account and amount the hash reads.
    "source-account-key": ("README Ids: the `source_account` value wins",
                           lambda c, r, cs, g, cfg=None: any(
                               isinstance(_src(t), str) and _src(t).strip() and
                               _src(t) != getattr(_first_literal(t), "account", None) and
                               any(p.account == _src(t) for p in t.postings) for t in c.txns)),
    "empty-source-account": ("README Ids: an empty value counts as absent, then the first posting",
                             lambda c, r, cs, g, cfg=None: any(
                                 _src(t) == "" and not t.postings[0].account.startswith(SOURCE_PREFIXES)
                                 and _first_literal(t) is not None for t in c.txns)),
    "padded-source-account": ("README Ids: the amount search uses the untrimmed value; the text is trimmed",
                              lambda c, r, cs, g, cfg=None: any(
                                  isinstance(_src(t), str) and _src(t) != _src(t).strip() and _src(t).strip()
                                  for t in c.txns)),
    "unmatched-source-account": ("README Ids: no posting has the account, so the amount is `0`",
                                 lambda c, r, cs, g, cfg=None: any(
                                     isinstance(_src(t), str) and _src(t).strip() == _src(t) and _src(t) and
                                     all(p.account != _src(t) for p in t.postings) for t in c.txns)),
    "falsy-non-text-source-account": ("README Ids: a value Python treats as false counts as absent",
                                      lambda c, r, cs, g, cfg=None: any(
                                          "source_account" in t.meta and not isinstance(_src(t), str) and not _src(t)
                                          for t in c.txns)),
    "liabilities-source": ("README Ids: the literal `Liabilities:` prefix",
                           lambda c, r, cs, g, cfg=None: any(
                               "source_account" not in t.meta and not t.postings[0].account.startswith(SOURCE_PREFIXES)
                               and getattr(_first_literal(t), "account", "").startswith("Liabilities:")
                               for t in c.txns)),
    "no-balance-sheet-posting": ("README Ids: no literal Assets/Liabilities posting, so the first posting",
                                 lambda c, r, cs, g, cfg=None: any(
                                     "source_account" not in t.meta and _first_literal(t) is None
                                     and len({p.account for p in t.postings}) > 1 for t in c.txns)),
    "repeated-source-posting": ("README Ids: the first posting of the chosen account",
                                lambda c, r, cs, g, cfg=None: any(
                                    sum(p.account == chosen_account(t)[0] for p in t.postings) > 1 and
                                    len({p.units for p in t.postings if p.account == chosen_account(t)[0]}) > 1
                                    for t in c.txns)),
    "interpolated-source-amount": ("README Ids: the number Beancount filled in",
                                   lambda c, r, cs, g, cfg=None: any(
                                       any(p.account == chosen_account(t)[0] and (p.meta or {}).get("__automatic__")
                                           for p in t.postings[:next((i for i, p in enumerate(t.postings)
                                                                      if p.account == chosen_account(t)[0]), 0) + 1])
                                       for t in c.txns)),
    "thousands-separator": ("README Ids: `str(Decimal)` of the parsed number",
                            lambda c, r, cs, g, cfg=None: any(
                                re.search(r"^\s+[A-Z][^;\"]*\s-?\d{1,3}(,\d{3})+(\.\d+)?\s", text, re.M)
                                for text in c.files.values())),
    "custom-root-names": ("README Ids: literal prefixes, not ADR-0024's kind",
                          lambda c, r, cs, g, cfg=None: c.options.get("name_assets") != "Assets" and any(
                              "source_account" not in t.meta and _first_literal(t) is None and any(
                                  account_types.get_account_type(p.account) == c.options["name_assets"]
                                  for p in t.postings[1:]) for t in c.txns)),
    # v0.4's `k:` ids.
    "import-key-id": ("README Ids: `k:` + the first `import-key` element",
                      lambda c, r, cs, g, cfg=None: any(x["transaction_id"].startswith("k:") for x in r)),
    "import-key-second-posting": ("README Ids: the first posting carrying an `import-key`",
                                  lambda c, r, cs, g, cfg=None: any(
                                      "import-key" not in (t.postings[0].meta or {}) and first_import_key(t)
                                      and x["transaction_id"].startswith("k:") for t, x in zip(c.txns, r))),
    "import-key-several-postings": ("README Ids: posting order decides",
                                    lambda c, r, cs, g, cfg=None: any(
                                        sum("import-key" in (p.meta or {}) for p in t.postings) > 1 and
                                        len({(p.meta or {}).get("import-key") for p in t.postings
                                             if "import-key" in (p.meta or {})}) > 1 for t in c.txns)),
    "import-key-several-elements": ("README Ids: the first element",
                                    lambda c, r, cs, g, cfg=None: any(
                                        isinstance((p.meta or {}).get("import-key"), str)
                                        and len(p.meta["import-key"].split()) > 1
                                        for t in c.txns for p in t.postings)),
    "import-key-on-transaction": ("README Ids: a transaction-level `import-key` is no identity",
                                  lambda c, r, cs, g, cfg=None: any(
                                      "import-key" in t.meta and not x["transaction_id"].startswith("k:")
                                      for t, x in zip(c.txns, r))),
    "import-key-empty": ("README Ids: an `import-key` with no element is skipped",
                         lambda c, r, cs, g, cfg=None: any(
                             any((p.meta or {}).get("import-key") == "" for p in t.postings)
                             and not x["transaction_id"].startswith("k:") for t, x in zip(c.txns, r))),
    "exponent-amount": ("README Ids: the amount is `str(Decimal)`, not plain notation",
                        lambda c, r, cs, g, cfg=None: any("E" in x["hash_input"].rsplit("|", 2)[1] for x in r)),
    "empty-text-id": ("README Ids: `id: \"\"` is used as is",
                      lambda c, r, cs, g, cfg=None: any((t.meta or {}).get("id") == "" for t in c.txns)),
    "truthy-non-text-source-account": ("README Ids: a non-text value's Python text, matching no posting",
                                       lambda c, r, cs, g, cfg=None: len({type(_src(t)) for t in c.txns
                                           if "source_account" in t.meta and not isinstance(_src(t), str)
                                           and _src(t)}) >= 3),
    "amount-source-account": ("README Ids: text against text, a v0.4 difference (v0.2.2 failed the export)",
                              lambda c, r, cs, g, cfg=None: any(isinstance(_src(t), amount.Amount) for t in c.txns)),
    "hash-shaped-legacy-id": ("README Ids: a legacy id equal to another entry's fallback id (BL-0059)",
                              lambda c, r, cs, g, cfg=None: any(
                                  id_rule(t) in ("id", "transaction_id") and HEX64.fullmatch(x["transaction_id"])
                                  and any(id_rule(u) == "fallback" and y["transaction_id"] == x["transaction_id"]
                                          for u, y in zip(c.txns, r)) for t, x in zip(c.txns, r))),
    # Metadata.
    "every-released-key": ("inv21-goldens.md: keys.toml, each key on its directive",
                           lambda c, r, cs, g, cfg=None: bool((cfg or {}).get("directive_metadata"))),
}


# ---------------------------------------------------------------------------------------------------------------------
# Checks.

def check_header(doc: dict[str, Any]) -> list[str]:
    problems = []
    if doc.get("legacy_tag") != LEGACY_TAG:
        problems.append(f"goldens: legacy_tag is {doc.get('legacy_tag')!r}, not {LEGACY_TAG}")
    if doc.get("beancount") != BEANCOUNT:
        problems.append(f"goldens: produced with Beancount {doc.get('beancount')!r}, not {BEANCOUNT}")
    if set(doc.get("legacy_code", {})) != {"backend/app/libs/content_hash.py", "backend/app/core/constants.py",
                                           "backend/app/core/beancount_engine.py",
                                           "backend/app/services/sqlite_exporter.py"}:
        problems.append("goldens: legacy_code does not name the four v0.2.2 files the values came from")
    return problems


def check_cases(cases_cfg: dict[str, Any], folders: list[str], goldens: dict[str, Any]) -> list[str]:
    problems = []
    for name in sorted(set(folders) ^ set(cases_cfg)):
        problems.append(f"{name}: a case folder without a cases.toml table, or the reverse")
    for name in sorted(set(cases_cfg) ^ set(goldens)):
        problems.append(f"{name}: in cases.toml or goldens.json but not both")
    allowed = {"features", "variant_of", "v04_overrides", "directive_metadata", "user_keys", "v022_fails", "v04_expected"}
    for name, cfg in cases_cfg.items():
        if extra := set(cfg) - allowed:
            problems.append(f"{name}: unknown fields {sorted(extra)}")
        if not cfg.get("features"):
            problems.append(f"{name}: no features declared")
        if unknown := set(cfg.get("features", [])) - set(FEATURES):
            problems.append(f"{name}: unknown features {sorted(unknown)}")
        if "variant_of" in cfg and cfg["variant_of"] not in cases_cfg:
            problems.append(f"{name}: variant_of names no case")
        if ("v022_fails" in cfg) != ("v04_expected" in cfg):
            problems.append(f"{name}: v022_fails (the reason) and v04_expected (the spec's values) go together")
        if "v022_fails" in cfg and not str(cfg["v022_fails"]).strip():
            problems.append(f"{name}: v022_fails must say why v0.2.2 has no value")
        if "v022_fails" in cfg and "v04_overrides" in cfg:
            problems.append(f"{name}: a case v0.2.2 cannot read has no v0.2.2 ids to override")
    declared = {f for cfg in cases_cfg.values() for f in cfg.get("features", [])}
    for f in sorted(set(FEATURES) - declared):
        problems.append(f"coverage: no case declares {f!r} ({FEATURES[f][0]})")
    return problems


def check_case(case: Case, cfg: dict[str, Any], golden: dict[str, Any], cases: dict[str, Case],
               goldens: dict[str, Any]) -> list[str]:
    """One case: Beancount loads it cleanly, the spec derivation equals the golden, the labels and overrides hold."""
    n = case.name
    problems = [f"{n}: Beancount reports {type(e).__name__}: {e.message}" for e in case.errors]
    rows = golden.get("transactions", [])
    want = derive(case, bool(cfg.get("directive_metadata")))
    if [(r.get("at"), r.get("flag")) for r in rows] != [(r["at"], r["flag"]) for r in want["transactions"]]:
        problems.append(f"{n}: the goldens' transactions are not exactly Beancount's, in Beancount's order")
    else:
        for g, w in zip(rows, want["transactions"]):
            for k, v in w.items():
                if g.get(k) != v:
                    problems.append(f"{n} {w['at']}: {k} golden {g.get(k)!r} != spec {v!r}")
            if hashlib.sha256(g.get("hash_input", "").encode("utf-8")).hexdigest() != g.get("sha256"):
                problems.append(f"{n} {w['at']}: sha256 is not the SHA-256 of hash_input")
    if cfg.get("directive_metadata"):
        if golden.get("directives") != want["directives"]:
            problems.append(f"{n}: directive metadata JSON differs from the spec, or a directive is missing")
    elif "directives" in golden:
        problems.append(f"{n}: directives pinned without directive_metadata")
    # The v0.2.2-equality promise (ADR-0048, surface.toml `transaction_id`): only import-key entries, and the
    # ordinals they free, may differ, each listed in v04_overrides.
    if "v022_fails" in cfg:
        # v0.2.2 failed on this ledger (no released value): every expected value comes from the spec, written by hand
        # in v04_expected (hash input per transaction), and no v0.2.2 id may be claimed.
        expected = cfg.get("v04_expected", {})
        if set(expected) != {r.get("at") for r in rows}:
            problems.append(f"{n}: v04_expected must give the hash input of exactly every transaction")
        for r in rows:
            if r.get("v022_transaction_id") is not None:
                problems.append(f"{n} {r.get('at')}: a v0.2.2 id on a ledger v0.2.2 could not read")
            if r.get("at") in expected and expected[r.get("at")] != r.get("hash_input"):
                problems.append(f"{n} {r.get('at')}: hash_input is not its v04_expected value")
        rows_for_v022: list[dict[str, Any]] = []
    else:
        rows_for_v022 = rows
    overrides = cfg.get("v04_overrides", {})
    has_key = any(first_import_key(t) for t in case.txns)
    if overrides and not has_key:
        problems.append(f"{n}: v04_overrides on a case without a posting import-key")
    v022_hashes = {r.get("sha256") for r in rows}
    for r in rows_for_v022:
        at, tid, old = r.get("at"), r.get("transaction_id"), r.get("v022_transaction_id")
        if at in overrides:
            if overrides[at] != tid:
                problems.append(f"{n} {at}: transaction_id is not its v04_overrides value")
            if overrides[at] == old:
                problems.append(f"{n} {at}: an override equal to v0.2.2's id (remove it)")
            if not (tid.startswith("k:") or (tid.rsplit("-", 1)[0] in v022_hashes and HEX64.fullmatch(
                    tid.rsplit("-", 1)[0]))):
                problems.append(f"{n} {at}: an override that is neither `k:` nor a content hash of this case")
        elif tid != old:
            problems.append(f"{n} {at}: v0.4 id {tid!r} differs from v0.2.2's {old!r} without an override")
    for at in sorted(set(overrides) - {r.get("at") for r in rows}):
        problems.append(f"{n}: v04_overrides names {at}, which is no transaction")
    if len(rows) == len(case.txns):
        for f in cfg.get("features", []):
            if f in FEATURES and not FEATURES[f][1](case, rows, cases, goldens, cfg):
                problems.append(f"{n}: declares {f!r} but its detector does not find it")
    return problems


KEY_FIELDS = {"key", "on", "class", "writers", "reader", "note"}
ON = {"transaction", "posting", "open", "commodity", "close"}
CLASSES = {"read", "pass"}
CITE = re.compile(r"\b(ADR-\d{4}|CAP-[A-Z0-9-]+|INV-\d{2}|BL-\d{4})\b")
# INV-21 (a) names these keys; each must be in the inventory as `read` (INVARIANTS.md is the owner's text).
INV21_NAMED = {("id", "transaction"), ("transaction_id", "transaction"), ("external_id", "transaction"),
               ("external_id_type", "transaction"), ("ofx_id", "transaction"), ("source_account", "transaction"),
               ("ofx_memo", "transaction"), ("memo", "transaction"), ("import-key", "posting")}


def check_inventory(keys: list[dict[str, Any]], invariant_text: str) -> list[str]:
    problems = []
    seen = set()
    for k in keys:
        name = f"keys.toml {k.get('key')!r} on {k.get('on')!r}"
        if set(k) - KEY_FIELDS or not {"key", "on", "class", "writers"} <= set(k):
            problems.append(f"{name}: fields must be key, on, class, writers (+ reader, note)")
            continue
        if (k["key"], k["on"]) in seen:
            problems.append(f"{name}: listed twice")
        seen.add((k["key"], k["on"]))
        if k["on"] not in ON:
            problems.append(f"{name}: `on` must be one of {sorted(ON)}")
        if k["class"] not in CLASSES:
            problems.append(f"{name}: class must be read or pass")
        if not isinstance(k["writers"], list) or not k["writers"] or not all(
                isinstance(w, str) and w.strip() for w in k["writers"]):
            problems.append(f"{name}: writers must name at least one writer (or why there is none)")
        if k["class"] == "read" and not CITE.search(str(k.get("reader", ""))):
            problems.append(f"{name}: a read key needs its v0.4 reader, citing an ADR, CAP, INV or BL ID")
    for key, on in sorted(INV21_NAMED - {(k.get("key"), k.get("on")) for k in keys if k.get("class") == "read"}):
        problems.append(f"keys.toml: INV-21 names {key!r} ({on}), which must be listed as read")
    m = re.search(r"### INV-21:.*?\*\*Rule:\*\*(.*?)\n", invariant_text, re.S)
    if not m:
        problems.append("INVARIANTS.md: INV-21's rule not found")
    else:
        named = set(re.findall(r"`([a-z_-]+)`", m.group(1)))
        if missing := named - {key for key, _ in INV21_NAMED}:
            problems.append(f"INV-21 now names keys this check does not know: {sorted(missing)}")
    return problems


def check_key_coverage(case: Case, cfg: dict[str, Any], keys: list[dict[str, Any]]) -> list[str]:
    """The metadata-keys case carries every inventory key on its directive, and nothing un-inventoried."""
    internal = {"filename", "lineno", "__tolerances__", "__automatic__"}
    present: set[tuple[str, str]] = set()
    kinds = {data.Open: "open", data.Commodity: "commodity", data.Close: "close", data.Transaction: "transaction"}
    for e in case.entries:
        if not case.own(e) or type(e) not in kinds:
            continue
        present |= {(k, kinds[type(e)]) for k in e.meta if k not in internal}
        for p in getattr(e, "postings", []) or []:
            present |= {(k, "posting") for k in (p.meta or {}) if k not in internal}
    inventory = {(k.get("key"), k.get("on")) for k in keys}
    user = set(cfg.get("user_keys", []))
    problems = [f"{case.name}: user_keys hides the inventory key {k!r}" for k in sorted(user & {i[0] for i in inventory})]
    for key, on in sorted(inventory - present):
        problems.append(f"{case.name}: inventory key {key!r} ({on}) is not on the golden ledger")
    for key, on in sorted(present - inventory):
        if key not in user:
            problems.append(f"{case.name}: key {key!r} ({on}) is neither in keys.toml nor a declared user key")
    for key in sorted(user - {k for k, _ in present}):
        problems.append(f"{case.name}: user key {key!r} is not on the ledger")
    return problems


def run_all(corpus: Path = CORPUS, invariants: Path = REPO / "design" / "INVARIANTS.md") -> list[str]:
    cases_cfg = tomllib.loads((corpus / "cases.toml").read_text(encoding="utf-8"))
    keys = tomllib.loads((corpus / "keys.toml").read_text(encoding="utf-8")).get("key", [])
    doc = json.loads((corpus / "goldens.json").read_text(encoding="utf-8"))
    goldens = doc.get("cases", {})
    folders = [p.name for p in corpus.iterdir() if p.is_dir()]
    problems = check_header(doc) + check_cases(cases_cfg, folders, goldens)
    problems += check_inventory(keys, invariants.read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as tmp:
        cases = {n: load_case(corpus / n, Path(tmp)) for n in sorted(set(folders) & set(cases_cfg))}
        for n, case in cases.items():
            if n in goldens:
                problems += check_case(case, cases_cfg[n], goldens[n], cases, goldens)
            if cases_cfg[n].get("directive_metadata"):
                problems += check_key_coverage(case, cases_cfg[n], keys)
    return problems
