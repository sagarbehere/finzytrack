"""Structural checks on the per-mutator intended-change table (design/specs/write-path/intent-table.toml, S0004)."""

from __future__ import annotations

import ast
import re
import tomllib
import unittest
from pathlib import Path
from typing import ClassVar

ROOT = Path(__file__).resolve().parents[2]
TABLE = ROOT / "design/specs/write-path/intent-table.toml"
COLUMNS = ("id", "caps", "kind", "old", "edits", "files", "intent", "meta", "ack", "refuses", "oracle", "status",
           "milestone", "adrs")
KINDS = {"add", "change-one", "many"}  # ADR-0014's clearance kinds; "no old-app comparison" is old = "n/a"
# ADR-0009's primitives, plus `replace_file` for the demo only (INV-25, ADR-0022). Adding, removing or changing a
# metadata line is a spliced whole-entry `replace` (ADR-0009 amendment of 2026-11-28, S0167): no `insert_meta` or
# `delete_meta`.
EDITS = {"insert", "replace", "delete", "replace_token", "option_line", "new_file", "replace_file"}
# Error classes Beancount 3.2.0 reports (listed from the pinned package, S0004).
BEANCOUNT_ERRORS = {"BalanceError", "PadError", "ValidationError", "DocumentError", "BookingError", "ReductionError",
                    "AmbiguousMatchError", "InterpolationError", "CategorizationError", "SelfReduxError", "LexerError",
                    "ParserError", "ParserSyntaxError", "LoadError", "DeprecatedError", "ImplicitPriceError",
                    "UniquePricesError", "CheckCommodityError", "CoherentCostError", "CommodityError", "LeafOnlyError",
                    "OneCommodityError", "SellGainsError", "UnusedAccountError", "MatchBasisError", "ConfigError"}
# Parser errors in Finzytrack's own text are a job 1 failure (ADR-0067 decision 2): they block, so they are never a
# preview hint. Every other class may be listed by the preview and saved anyway; none is a refusal reason.
PARSE_ERRORS = {"LexerError", "ParserError", "ParserSyntaxError"}
# ADR-0026 §4.1 app-written keys and review:28 §4.2 user vocabulary: the only keys a row may write.
APP_KEYS = {"import-key", "txn-date", "original", "statement-amount", "memo"}
VOCABULARY = {"name", "asset-class", "exposure", "fetch-symbol", "isin", "issue-date", "denomination", "account-type",
              "interest-rate", "maturity-date", "interest-account", "account_number", "ofx-bank-id", "ifsc_code",
              "swift_bic", "display-name", "description", "income-type", "dividend-of", "reason", "document*", "note",
              "user keys typed in the drawer"}
NOT_WRITTEN = {"id", "content_hash", "source_account", "external_id", "external_id_type", "source_rule", "status",
               "import-batch", "import-key-override", "advisor"}
ORACLE = re.compile(r"^(I2|I3|I10|M28)\b|^FIX-(?P<finding>[A-Z][A-Za-z0-9.]*(?:-\d+)?)\b|^none: no old endpoint|^non-comparable: ")


def load() -> dict:
    with TABLE.open("rb") as f:
        return tomllib.load(f)


def harness_rows() -> dict[str, str]:
    """`model.INTENT_ROWS`, read as code (a comment cannot satisfy the check)."""
    tree = ast.parse((ROOT / "scripts/oracle/model.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "INTENT_ROWS" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("scripts/oracle/model.py has no INTENT_ROWS")


# The kinds of refusal ADR-0067 keeps (job 1, consent steps and policy), each written `<kind>: <reason>`.
REFUSAL_KINDS = {
    "target",  # the entry or directive to change is not found exactly once, or changed since it was read
    "grammar",  # our own text would not parse into the intended entries (account grammar, codes, typed values)
    "not-editable",  # plugin-generated, in an encrypted file, read-only, or declared in a file the user edits
    "identity",  # the write would break a read-forever identity key or a duplicate rule (INV-14)
    "user-choice",  # a name, account or choice the user did not make (INV-15; Finzytrack never guesses)
    "consent",  # a confirmation step of the flow is not done yet (the re-booking preview, holdings at cost)
    "policy",  # an invariant or ADR about folders, files and accounts (INV-25, INV-26, INV-29, ADR-0049, ...)
    "integrity",  # the result would not be what was asked (a copy that loads differently, a source that moved)
}
# Words of an accounting check (a balance, a date against open/close, booking); a refusal never uses them.
ACCOUNTING_WORDS = re.compile(
    r"would fail|would follow|before the open|after the close|already closed|postings? (?:after|before)|"
    r"does not balance|not open\b|\bAVERAGE\b|\bresidual\b|never acknowledgeable",
    re.IGNORECASE,
)


def refusal_ok(ref: str, refers_to_a_row: bool) -> bool:
    """A refusal of the current design: `<kind>: <reason>` with a kind of `REFUSAL_KINDS` (or `as <row>`), naming no
    Beancount error class but a parser error and no accounting words."""
    kind = ref.split(":", 1)[0] if ": " in ref else ""
    if not (kind in REFUSAL_KINDS or refers_to_a_row):
        return False
    if any(cls in ref for cls in BEANCOUNT_ERRORS - PARSE_ERRORS):
        return False
    return not ACCOUNTING_WORDS.search(ref)


# BL-0049 (ADR-0057 decision 3, its mechanical form): every capability row that writes the ledger and will be built is
# cited by a mutator row or named in `[ledger_writers]` with why it has none. A line is one of:
#   "not built: ADR-nnnn (why)"   the capability is not built as a ledger writer in v0.4 (the ADR decided it);
#   "machinery: ADR-nnnn (why)"   its writes are the contract's or the lifecycle's own (a save, a backup, a rollback,
#                                 recovery, the restore of a run's backup), not a use case's intended change;
#   "pending: BL-nnnn (why)"      a ledger writer whose mutator row is not written yet; the open backlog row says when.
# Cut and deferred rows need neither (check_capabilities.py makes them cite the ADR that decided them). Stated limits:
# the check trusts each row's `writes` value (filled by behaviour, S0157) and proves that every ledger writer is named,
# not that a pending row's mutator will match it.
LIVE = {"planned", "partial", "built"}
LINE = re.compile(r"^(?:(?P<kind>not built|machinery): (?P<adr>ADR-\d{4})|pending: (?P<bl>BL-\d{4})) \(.+\)$")


def open_backlog_ids(backlog: str) -> set[str]:
    """The IDs of the backlog rows whose status column is `open`."""
    out = set()
    for line in backlog.splitlines():
        cells = [c.strip() for c in re.split(r"(?<!\\)\|", line.strip())]
        if len(cells) == 8 and re.fullmatch(r"BL-\d{4}", cells[1]) and cells[5] == "open":
            out.add(cells[1])
    return out


def ledger_writer_problems(caps: list[dict], table: dict, adrs: set[str], backlog: str) -> list[str]:
    """What breaks BL-0049's rule in `table` (the intent table) against `caps` (the capability rows)."""
    by_id = {c["id"]: c for c in caps}
    cited: dict[str, list[str]] = {}
    problems: list[str] = []
    for r in table["mutator"]:
        for cap in r["caps"]:
            cited.setdefault(cap, []).append(r["id"])
            if cap in by_id and by_id[cap].get("writes") != "ledger":
                problems.append(f"{r['id']}: cites {cap}, whose writes is {by_id[cap].get('writes')!r}, not 'ledger'")
    lines = table.get("ledger_writers", {})
    open_ids = open_backlog_ids(backlog)
    for cap, line in lines.items():
        c = by_id.get(cap)
        if c is None:
            problems.append(f"[ledger_writers] {cap}: no such capability row")
            continue
        if c.get("writes") != "ledger":
            problems.append(f"[ledger_writers] {cap}: its writes is {c.get('writes')!r}, not 'ledger'")
        if c.get("status") not in LIVE:
            problems.append(f"[ledger_writers] {cap}: a {c.get('status')} row needs no line")
        if cap in cited:
            problems.append(f"[ledger_writers] {cap}: already cited by {', '.join(cited[cap])}; drop the line")
        m = LINE.match(line) if isinstance(line, str) else None
        if m is None:
            problems.append(f"[ledger_writers] {cap}: {line!r} is not 'not built: ADR-nnnn (why)', "
                            "'machinery: ADR-nnnn (why)' or 'pending: BL-nnnn (why)'")
        elif m.group("adr") and m.group("adr") not in adrs:
            problems.append(f"[ledger_writers] {cap}: {m.group('adr')} does not exist")
        elif m.group("bl") and m.group("bl") not in open_ids:
            problems.append(f"[ledger_writers] {cap}: {m.group('bl')} is not an open backlog row")
    for c in caps:
        if c.get("writes") == "ledger" and c.get("status") in LIVE and c["id"] not in cited and c["id"] not in lines:
            problems.append(f"{c['id']}: writes the ledger, but no mutator row cites it and [ledger_writers] has no line")
    return problems


class LedgerWriters(unittest.TestCase):
    """BL-0049's check, on small tables beside the real one (EP-29: each passing case beside one that must not pass)."""

    ADRS: ClassVar[set[str]] = {"ADR-0012", "ADR-0043"}
    BACKLOG = ("| BL-0001 | a row | WS-X | M3 | open | — |\n"
               "| BL-0002 | a closed row | WS-X | M3 | done | — |\n")

    @staticmethod
    def cap(cid: str, writes: str = "ledger", status: str = "planned") -> dict:
        return {"id": cid, "writes": writes, "status": status}

    def problems(self, caps: list[dict], cites: list[str], lines: dict | None = None) -> list[str]:
        table = {"mutator": [{"id": "x.write", "caps": cites}], "ledger_writers": lines or {}}
        return ledger_writer_problems(caps, table, self.ADRS, self.BACKLOG)

    def test_what_passes(self) -> None:
        caps = [self.cap("CAP-A"), self.cap("CAP-B"), self.cap("CAP-C"), self.cap("CAP-D"), self.cap("CAP-E"),
                self.cap("CAP-F", "config"), self.cap("CAP-G", "none"), self.cap("CAP-H", status="cut"),
                self.cap("CAP-I", status="deferred"), self.cap("CAP-J", status="partial")]
        lines = {"CAP-B": "not built: ADR-0043 (deferred)", "CAP-C": "machinery: ADR-0012 (recovery)",
                 "CAP-D": "pending: BL-0001 (no row yet)"}
        self.assertEqual(self.problems(caps, ["CAP-A", "CAP-E", "CAP-J"], lines), [])

    def test_what_must_not_pass(self) -> None:
        cases = {
            "a planned ledger row nobody names": ([self.cap("CAP-A")], [], {}),
            "a partial ledger row nobody names": ([self.cap("CAP-A", status="partial")], [], {}),
            "a built ledger row nobody names": ([self.cap("CAP-A", status="built")], [], {}),
            "a mutator citing a config row": ([self.cap("CAP-A", "config")], ["CAP-A"], {}),
            "a mutator citing a none row": ([self.cap("CAP-A", "none")], ["CAP-A"], {}),
            "a mutator citing a row with no writes": ([{"id": "CAP-A", "status": "planned"}], ["CAP-A"], {}),
            "a line for a config row": ([self.cap("CAP-A", "config")], [], {"CAP-A": "machinery: ADR-0012 (x)"}),
            "a line for an unknown row": ([], [], {"CAP-Z": "machinery: ADR-0012 (x)"}),
            "a line for a cut row": ([self.cap("CAP-A", status="cut")], [], {"CAP-A": "not built: ADR-0043 (x)"}),
            "a line for a cited row": ([self.cap("CAP-A")], ["CAP-A"], {"CAP-A": "machinery: ADR-0012 (x)"}),
            "a line with no reason": ([self.cap("CAP-A")], [], {"CAP-A": "machinery: ADR-0012"}),
            "a line with another word": ([self.cap("CAP-A")], [], {"CAP-A": "later: ADR-0012 (x)"}),
            "a line with no ADR": ([self.cap("CAP-A")], [], {"CAP-A": "not built: (x)"}),
            "a line citing a missing ADR": ([self.cap("CAP-A")], [], {"CAP-A": "not built: ADR-9999 (x)"}),
            "a pending line citing an ADR": ([self.cap("CAP-A")], [], {"CAP-A": "pending: ADR-0012 (x)"}),
            "a pending line citing a missing row": ([self.cap("CAP-A")], [], {"CAP-A": "pending: BL-0003 (x)"}),
            "a pending line citing a closed row": ([self.cap("CAP-A")], [], {"CAP-A": "pending: BL-0002 (x)"}),
            "a not-built line citing a backlog row": ([self.cap("CAP-A")], [], {"CAP-A": "not built: BL-0001 (x)"}),
            "an empty line": ([self.cap("CAP-A")], [], {"CAP-A": ""}),
            "a line that is a list": ([self.cap("CAP-A")], [], {"CAP-A": ["machinery: ADR-0012 (x)"]}),
        }
        for name, (caps, cites, lines) in cases.items():
            with self.subTest(case=name):
                self.assertNotEqual(self.problems(caps, cites, lines), [])

    def test_a_backlog_row_counts_as_open_only_by_its_status_column(self) -> None:
        self.assertEqual(open_backlog_ids(self.BACKLOG), {"BL-0001"})
        self.assertEqual(open_backlog_ids("| BL-0004 | a \\| b | WS-X | M3 | open | — |\n"), {"BL-0004"})  # an escaped pipe

    def test_every_ledger_writer_of_the_capability_map_is_named(self) -> None:
        with (ROOT / "design/capabilities.toml").open("rb") as f:
            caps = tomllib.load(f)["capability"]
        adrs = {"-".join(p.name.split("-")[:2]) for p in (ROOT / "design/decisions").glob("ADR-*.md")}
        backlog = (ROOT / "design/backlog.md").read_text(encoding="utf-8")
        self.assertEqual(ledger_writer_problems(caps, load(), adrs, backlog), [])


class IntentTableTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.table = load()
        cls.rows = cls.table["mutator"]
        cls.ids = [r["id"] for r in cls.rows]
        with (ROOT / "design/capabilities.toml").open("rb") as f:
            caps = tomllib.load(f)
        cls.caps = {c["id"] for c in next(v for v in caps.values() if isinstance(v, list))}
        cls.adrs = {"-".join(p.name.split("-")[:2]) for p in (ROOT / "design/decisions").glob("ADR-*.md")}
        cls.backlog = (ROOT / "design/backlog.md").read_text(encoding="utf-8")

    def refers_to_a_row(self, text: str, row: str = "") -> bool:
        m = re.match(r"^as ([a-z][a-z.-]*[a-z])\b", text)
        return bool(m and m.group(1) in self.ids) or (text == "as the undone write's row" and row == "data.undo")

    def test_every_row_has_every_column(self) -> None:
        for r in self.rows:
            with self.subTest(row=r.get("id")):
                self.assertEqual(set(r), set(COLUMNS))
                for col in ("id", "kind", "old", "files", "intent", "status", "milestone"):
                    self.assertTrue(str(r[col]).strip(), col)
                for col in ("caps", "edits", "oracle", "adrs"):
                    self.assertTrue(r[col], col)

    def test_ids_are_unique(self) -> None:
        self.assertEqual(len(self.ids), len(set(self.ids)))

    def test_kinds_edits_and_milestones(self) -> None:
        for r in self.rows:
            with self.subTest(row=r["id"]):
                self.assertIn(r["kind"], KINDS)
                self.assertLessEqual(set(r["edits"]), EDITS)
                self.assertRegex(r["milestone"], r"^M\d{1,2}[ab]?$")
                if "replace_file" in r["edits"]:
                    self.assertEqual(r["id"], "ws.refresh-demo")  # the one replacement in the system (INV-25)

    def test_preview_hints_are_beancount_errors_or_warnings_never_parser_errors(self) -> None:
        """ADR-0067: `ack` is what the preview may list; booking errors included, parser errors never (they block)."""
        for r in self.rows:
            for ack in r["ack"]:
                with self.subTest(row=r["id"], ack=ack):
                    if ack.startswith("as "):
                        self.assertTrue(self.refers_to_a_row(ack, r["id"]), ack)
                        continue
                    if ack.startswith("Warning: "):
                        continue
                    first = ack.split()[0]
                    self.assertIn(first, BEANCOUNT_ERRORS - PARSE_ERRORS, ack)
                    self.assertIn(" on ", ack)

    def test_no_refusal_is_an_accounting_error(self) -> None:
        """ADR-0067 decision 2 (S0068): job 2 advises and never refuses; a refusal reason that is a Beancount error
        (other than a parser error in our own text) belongs in the preview hints. Every refusal names its kind from
        `REFUSAL_KINDS` (S0068 verifier r1 F3: a check of class names alone let an accounting refusal through when
        worded without one), or refers to another row's refusals (`as <row>`); and none uses accounting words."""
        for r in self.rows:
            for ref in r["refuses"]:
                with self.subTest(row=r["id"], refuses=ref):
                    self.assertTrue(refusal_ok(ref, self.refers_to_a_row(ref, r["id"])), ref)

    def test_metadata_written_is_on_the_contract_list(self) -> None:
        for r in self.rows:
            for key in r["meta"]:
                with self.subTest(row=r["id"], key=key):
                    self.assertNotIn(key, NOT_WRITTEN)
                    if key == "user keys typed in the drawer":  # client-chosen keys only where the user edits an entry
                        self.assertTrue(r["id"].startswith("txn."), r["id"])
                    m = re.match(r"^the vocabulary keys of ([a-z][a-z.-]*[a-z])$", key)
                    self.assertTrue(key in APP_KEYS | VOCABULARY or self.refers_to_a_row(key)
                                    or bool(m and m.group(1) in self.ids), key)

    def test_oracle_expectations_name_known_classes(self) -> None:
        for r in self.rows:
            for o in r["oracle"]:
                with self.subTest(row=r["id"], oracle=o):
                    m = ORACLE.match(o)
                    self.assertTrue(m or self.refers_to_a_row(o), o)
                    if m and m.group("finding"):
                        self.assertRegex(self.backlog, rf"(?m)^\| {re.escape(m.group('finding'))} \|", o)

    def test_capabilities_and_adrs_exist(self) -> None:
        for r in self.rows:
            with self.subTest(row=r["id"]):
                self.assertLessEqual(set(r["caps"]), self.caps)
                self.assertLessEqual(set(r["adrs"]), self.adrs)

    def test_status_is_decided_draft_or_an_open_inbox_question(self) -> None:
        for r in self.rows:
            with self.subTest(row=r["id"]):
                status = r["status"]
                if status.startswith("owner:"):
                    name = status.removeprefix("owner:")
                    self.assertTrue((ROOT / "design/inbox" / name).is_file() or (ROOT / "design/inbox/answered" / name).is_file(), name)
                    continue
                self.assertIn(status, ("decided", "draft"))
                text = " ".join(str(v) for v in r.values()).lower()
                for word in ("inbox", "owner"):
                    self.assertNotIn(word, text, "a row that still points at an open question cannot be decided or draft")

    def test_every_legacy_mutator_maps_to_a_row_or_a_cut(self) -> None:
        # The list is the `@_serialized_write` set of the legacy LedgerManager at legacy-final (26, checked against
        # the legacy worktree in S0004; the worktree is not in CI).
        legacy = self.table["legacy"]
        self.assertEqual(len(legacy["mutators"]), 26)
        self.assertEqual(set(legacy["mutators"]), set(legacy["map"]))
        for name, targets in legacy["map"].items():
            for t in targets:
                with self.subTest(legacy=name, target=t):
                    cut = re.match(r"^not built: (ADR-\d{4}) ", t)
                    self.assertTrue(t in self.ids or (cut and cut.group(1) in self.adrs), t)

    def test_a_mutator_with_an_old_endpoint_names_its_oracle_classes(self) -> None:
        for r in self.rows:
            with self.subTest(row=r["id"]):
                no_old = r["old"].startswith("n/a")
                self.assertEqual(no_old, any(o.startswith("none: no old endpoint") for o in r["oracle"]))

    def test_the_harness_models_name_existing_rows(self) -> None:
        rows = harness_rows()
        self.assertEqual(set(rows), {"update_transaction", "rename_account", "import_append"})
        self.assertLessEqual(set(rows.values()), set(self.ids))


if __name__ == "__main__":
    unittest.main()


class AccountingRefusalMutant(unittest.TestCase):
    """S0068 verifier r1 F3's mutant: account.close's refusals of cc0b27db, put back without a class name, fail."""

    def test_account_closes_old_refusals_are_caught(self) -> None:
        old = ["the close date is before the open date", "A has postings after the close date", "A is already closed"]
        for ref in old:
            with self.subTest(ref=ref):
                self.assertFalse(refusal_ok(ref, refers_to_a_row=False))
                self.assertFalse(refusal_ok(f"target: {ref}", refers_to_a_row=False))
        self.assertFalse(refusal_ok("target: the inserted assertion would fail", refers_to_a_row=False))
        self.assertFalse(refusal_ok("policy: AVERAGE (refused by Beancount 3.2)", refers_to_a_row=False))
        self.assertFalse(refusal_ok("the targeted assertion is not found exactly once", refers_to_a_row=False))
        self.assertTrue(refusal_ok("target: the targeted assertion is not found exactly once", refers_to_a_row=False))
