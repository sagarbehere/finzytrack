"""INV-03, Beancount parity: the parity corpus (BL-0003; S0094) through the overlay load (ADR-0010 guardrail 1).

Every `load` case, hand-written (`corpora/ledgers/parity/`) or generated at test time (Beancount's example ledger and
its variants, `tests/ledger/parity.py`), loads through `overlay.load` with the result Beancount's own loader gives
(S0049's comparison, `parity.same`) three ways: the empty overlay, every file's disk bytes as candidates, and a
candidate for the root that equals Beancount's load after the same bytes are written (the round trip). The mirror's
rows build from each (Finzytrack handles what Beancount loads). Every `refuse` case holds a file Beancount itself
would decrypt and is refused with that file by the overlay and by the rebuild's real load, nothing decrypted, never
partly loaded (ADR-0059; the root included).

The populations are read from Beancount at run time, so an upgrade that adds an option, a built-in plugin or a
directive type fails here until the corpus covers it: every public option is set by a case, every module of
`beancount.plugins` runs in a case, every directive type is loaded by a case; every feature of `parity.FEATURES` is
claimed by a case and found there by its detector. The must-not-pass cases seed divergences into the overlay and
check that the corpus catches each. Cases loop instead of parametrize (`scripts/check_invariants.py`: no list that
could come out empty switches a case off).
"""

from __future__ import annotations

import glob
import hashlib
import pkgutil
import re
from collections import Counter
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from beancount import plugins as beancount_plugins
from beancount.core import data
from beancount.parser import options as beancount_options
from beancount.utils import encryption

from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import overlay as overlay_module
from ledger_engine.ledger.overlay import Loaded, LoadRefused
from ledger_engine.ledger.policy import DESKTOP
from ledger_engine.mirror import build
from ledger_engine.mirror.core.rows import Stamp
from tests.ledger import parity
from tests.ledger.parity import Case, facts, fresh_plugins, load_file, same

pytestmark = pytest.mark.inv("INV-03")

ROUND_TRIP = b'\n2020-01-01 event "parity" "round trip"\n'
STAMP = Stamp("1/parity", "parity", "2026-11-10T00:00:00+00:00", "parity", "open")


def every_case(tmp: Path) -> list[Case]:
    return parity.corpus_cases(tmp / "corpus") + parity.generated_cases(tmp / "generated")


def beancount(case: Case) -> parity.Result:
    with fresh_plugins(case.folder):
        return load_file(case.root)


def overlay(case: Case, candidates: dict[str, bytes]) -> Loaded | LoadRefused:
    with fresh_plugins(case.folder):
        return overlay_module.load(str(case.root), candidates, DESKTOP)


# ---- the three checks of a load case, and the check of a refuse case ----------------------------------------------


def empty_overlay(case: Case) -> None:
    same(overlay(case, {}), beancount(case))


def disk_bytes_as_candidates(case: Case) -> None:
    expected = beancount(case)
    # Every file as a candidate, except a `.asc` one: a candidate for it is refused (ADR-0010 guardrail 4).
    candidates = {f: Path(f).read_bytes() for f in expected[2]["include"] if not f.endswith(".asc")}
    assert candidates or not case.root.exists(), case.name
    same(overlay(case, candidates), expected)


def round_trip(case: Case) -> None:
    """A candidate for the root (an added `event`) predicts Beancount's load after the same bytes are written."""
    new = (case.root.read_bytes() if case.root.exists() else b"") + ROUND_TRIP
    predicted = overlay(case, {str(case.root): new})
    assert isinstance(predicted, Loaded), predicted
    assert any(isinstance(e, port.Event) and e.description == "round trip" for e in predicted.entries), case.name
    case.root.write_bytes(new)
    same(predicted, beancount(case), written_after=True)


CHECKS: dict[str, Callable[[Case], None]] = {
    "empty overlay": empty_overlay,
    "disk bytes as candidates": disk_bytes_as_candidates,
    "round trip": round_trip,
}


@contextmanager
def no_decryption() -> Generator[None]:
    with pytest.MonkeyPatch.context() as mp:

        def refuse(*args: object, **kwargs: object) -> str:
            pytest.fail("the load tried to decrypt a file")

        mp.setattr(encryption, "read_encrypted_file", refuse)
        yield


def refused(case: Case) -> None:
    assert case.refused is not None, case.name
    assert parity.beancount_decrypts(case.refused), f"{case.name}: Beancount would not decrypt {case.refused}"
    with no_decryption():
        assert overlay(case, {}) == LoadRefused("encrypted", str(case.refused))
        plain = {str(p): p.read_bytes() for p in case.folder.rglob("*") if p.is_file() and p != case.root}
        plain = {k: v for k, v in plain.items() if not k.lower().endswith((".gpg", ".asc"))}
        assert overlay(case, plain) == LoadRefused("encrypted", str(case.refused)), "with the plain files as candidates"
    with pytest.raises(port.EncryptedFileMet) as met, fresh_plugins(case.folder):
        port.real_load(str(case.root))
    assert met.value.path == str(case.refused)


def run(check: Callable[[Case], None], cases: list[Case]) -> list[str]:
    """Each case, each failure named (a failure in one case does not hide another's)."""
    failures: list[str] = []
    for case in cases:
        try:
            check(case)
        except AssertionError as e:
            failures.append(f"{case.name}: {str(e)[:2000]}")
    return failures


def load_cases(tmp: Path) -> list[Case]:
    return [c for c in every_case(tmp) if c.expect == "load"]


# ---- equality with Beancount's loader ----------------------------------------------------------------------------


def test_every_load_case_equals_beancounts_loader_three_ways(tmp_path: Path) -> None:
    failures: list[str] = []
    counted = 0
    for label, check in CHECKS.items():
        cases = load_cases(tmp_path / label.replace(" ", "-"))
        counted += len(cases)
        failures += [f"{label}: {f}" for f in run(check, cases)]
    assert failures == []
    assert counted == 3 * 14  # 8 hand-written and 6 generated load cases, three checks each


def test_every_refuse_case_is_refused_with_its_file_and_nothing_is_read(tmp_path: Path) -> None:
    cases = [c for c in every_case(tmp_path) if c.expect == "refuse"]
    assert sorted(c.name for c in cases) == [
        "encrypted-glob",
        "encrypted-nested",
        "encrypted-root-asc",
        "encrypted-root-gpg",
    ]
    assert run(refused, cases) == []


def test_the_mirror_rows_build_from_every_load_case(tmp_path: Path) -> None:
    failures: list[str] = []
    for case in load_cases(tmp_path):
        loaded = overlay(case, {})
        assert isinstance(loaded, Loaded), case.name
        made = build.tables(loaded.entries, loaded.errors, loaded.options, date(2026, 11, 10), STAMP)
        if isinstance(made, build.BuildFailed):
            failures.append(f"{case.name}: {made}")
        elif not made["postings"] and case.name not in NO_TRANSACTIONS:
            failures.append(f"{case.name}: no posting rows")
    assert failures == []


NO_TRANSACTIONS = {"absolute-paths", "documents", "not-utf8", "root-missing"}


# ---- the populations ---------------------------------------------------------------------------------------------


def test_the_corpus_folders_and_its_rows_agree() -> None:
    folders = sorted(p.name for p in parity.CORPUS.iterdir() if p.is_dir())
    assert folders == sorted(parity.corpus_rows())
    for name, row in parity.corpus_rows().items():
        assert row["expect"] in {"load", "refuse"}, name
        assert ("refused" in row) == (row["expect"] == "refuse"), name
        assert ("errors" in row) == (row["expect"] == "load"), name


def test_beancount_gives_each_load_case_the_errors_it_is_written_for(tmp_path: Path) -> None:
    """The corpus itself is pinned: Beancount's errors, by type, are what each case was written to give (none for the
    example ledgers), so a case that stops loading as written, or a Beancount that reads it differently, is seen."""
    wrong: list[str] = []
    for case in load_cases(tmp_path):
        got = Counter(type(e).__name__ for e in beancount(case)[1])
        if dict(got) != case.errors:
            wrong.append(f"{case.name}: {dict(got)} != {case.errors}")
    assert wrong == []


def claim_problems(cases: list[Case]) -> list[str]:
    """Every case claims exactly the features its detectors find (S0094 round 1, F3): a feature claimed that is not in
    the inventory or not found, a feature found that the case does not claim, and a feature no case claims. A
    detector that is too loose ("any include", "any plugin") finds its feature in a case that does not claim it."""
    claimed: set[str] = set()
    problems: list[str] = []
    for case in cases:
        seen = facts(case, beancount(case) if case.expect == "load" else None)
        found = {f for f, detector in parity.FEATURES.items() if parity.applies(f, case) and detector(seen)}
        for feature in case.features:
            claimed.add(feature)
            if feature not in parity.FEATURES:
                problems.append(f"{case.name}: {feature} is not in the inventory")
            elif feature not in found:
                problems.append(f"{case.name}: {feature} claimed, not found")
        problems += [f"{case.name}: {f} found, not claimed" for f in sorted(found - set(case.features))]
    problems += [f"{feature}: claimed by no case" for feature in sorted(set(parity.FEATURES) - claimed)]
    return problems


def test_every_feature_is_claimed_and_found_where_it_is_claimed(tmp_path: Path) -> None:
    assert claim_problems(every_case(tmp_path)) == []
    assert len(parity.FEATURES) == 37


def test_a_false_claim_an_unknown_feature_and_an_unclaimed_one_are_found(tmp_path: Path) -> None:
    cases = {c.name: c for c in every_case(tmp_path)}
    fava = replace(cases["fava-features"], features=("plugin-raw-mode", "no-such-feature"))
    others = [c for name, c in cases.items() if name not in {"fava-features", "includes"}]
    assert claim_problems([*others, fava]) == [
        "fava-features: plugin-raw-mode claimed, not found",
        "fava-features: no-such-feature is not in the inventory",
        "include-cycle: claimed by no case",
        "include-duplicate: claimed by no case",
        "include-glob: claimed by no case",
        "include-nested: claimed by no case",
        "include-no-match: claimed by no case",
        "include-parent-folder: claimed by no case",
        "plain-asc: claimed by no case",
    ]


def test_a_feature_a_case_has_but_does_not_claim_is_found(tmp_path: Path) -> None:
    """The near miss of each loose detector the verifier tried (round 1, F3): a case that has the feature without
    claiming it, so a detector that fires on more than its feature is seen."""
    cases = {c.name: c for c in every_case(tmp_path)}
    includes = replace(cases["includes"], features=tuple(f for f in cases["includes"].features if f != "include-glob"))
    example = replace(cases["example"], features=())
    assert claim_problems([includes, example])[:2] == [
        "includes: include-glob found, not claimed",
        "example: example-ledger found, not claimed",
    ]
    loose: dict[str, Callable[[parity.Facts], bool]] = {
        "include-plain": lambda x: bool(list(x.includes())),
        "plugin-config": lambda x: bool(x.options["plugin"]),
        "example-ledger": lambda x: x.case.generated,
        "include-parent-folder": lambda x: bool(list(x.includes())),
    }
    for feature, detector in loose.items():
        with pytest.MonkeyPatch.context() as mp:
            mp.setitem(parity.FEATURES, feature, detector)
            assert any(p.endswith(f"{feature} found, not claimed") for p in claim_problems(list(cases.values()))), (
                feature
            )


BEANCOUNT_OPTIONS = {o.name: o for group in beancount_options.PUBLIC_OPTION_GROUPS for o in group.options}


def options_missing(cases: list[Case]) -> list[str]:
    """Each public option of Beancount that no load case ends with a value other than its default, in Beancount's own
    result (S0094 round 1, F2: a line that sets it is not enough; a later line or a refused value can leave the
    default). An alias (`inferred_tolerance_multiplier`, renamed `tolerance_multiplier`) never holds a value of its
    own: it counts when a case's `option` line names it and its target ends non-default in that case."""
    changed: set[str] = set()
    for case in cases:
        if case.expect != "load":
            continue
        options = beancount(case)[2]
        lines = {n for text in case.texts().values() for n in re.findall(r'^option "([a-z_]+)"', text, re.M)}
        for name, opt in BEANCOUNT_OPTIONS.items():
            target = opt.alias or name
            if (opt.alias is None or name in lines) and options[target] != beancount_options.OPTIONS_DEFAULTS[target]:
                changed.add(name)
    return sorted(set(BEANCOUNT_OPTIONS) - changed)


def test_every_option_beancount_has_is_set_by_a_load_case(tmp_path: Path) -> None:
    assert len(BEANCOUNT_OPTIONS) == 28  # Beancount 3.2.0; an upgrade that adds one fails below until a case sets it
    assert options_missing(every_case(tmp_path)) == []


def test_an_option_no_case_sets_is_found(tmp_path: Path) -> None:
    cases = [c for c in every_case(tmp_path) if c.name != "options"]
    assert options_missing(cases) == [
        "allow_deprecated_none_for_tags_and_links",
        "allow_pipe_separator",
        "booking_method",
        "display_precision",
        "infer_tolerance_from_cost",
        "inferred_tolerance_default",
        "inferred_tolerance_multiplier",
        "long_string_maxlines",
        "render_commas",
        "tolerance_multiplier",
    ]
    # A line that sets an option is not enough: a later line puts render_commas back to its default (the verifier's
    # round 1 case: "SOMETIMES" reads as false), and the alias's target set elsewhere does not count for the alias.
    every = {c.name: c for c in every_case(tmp_path / "again")}
    options = every["options"].root
    text = options.read_text(encoding="utf-8")
    options.write_text(
        text.replace('option "tolerance_multiplier" "0.6"\n', "").replace(
            'option "inferred_tolerance_multiplier" "0.7"\n', ""
        )
        + 'option "render_commas" "SOMETIMES"\n',
        encoding="utf-8",
    )
    assert options_missing(list(every.values())) == [
        "inferred_tolerance_multiplier",
        "render_commas",
        "tolerance_multiplier",
    ]


BUILTIN_PLUGINS = sorted(
    f"beancount.plugins.{m.name}"
    for m in pkgutil.iter_modules(beancount_plugins.__path__)
    if not m.name.endswith("_test")
)


def plugins_missing(cases: list[Case]) -> list[str]:
    """Each module of `beancount.plugins` that no load case runs (listed, imported and applied without an error)."""
    ran: set[str] = set()
    for case in cases:
        if case.expect != "load":
            continue
        _, errors, options = beancount(case)
        failed = {m for e in errors for m in re.findall(r'^Error (?:importing|applying plugin) "([^"]+)"', e.message)}
        ran |= {name for name, _ in options["plugin"]} - failed
    return sorted(set(BUILTIN_PLUGINS) - ran)


def test_every_builtin_plugin_runs_in_a_load_case(tmp_path: Path) -> None:
    assert len(BUILTIN_PLUGINS) == 18  # Beancount 3.2.0
    assert plugins_missing(every_case(tmp_path)) == []


def test_a_builtin_plugin_no_case_runs_is_found(tmp_path: Path) -> None:
    cases = {c.name: c for c in every_case(tmp_path)}
    broken = cases["plugins-builtin"].root
    text = broken.read_text(encoding="utf-8")
    broken.write_text(text.replace('"beancount.plugins.sellgains"', '"beancount.plugins.sellgains" "not a config"'))
    assert plugins_missing(list(cases.values())) == ["beancount.plugins.sellgains"]  # applied with an error
    others = [c for name, c in cases.items() if name != "plugins-builtin"]
    assert len(plugins_missing(others)) == 17  # implicit_prices also runs in plugins-raw


DIRECTIVES = {d.__name__ for d in data.ALL_DIRECTIVES}


def directives_missing(cases: list[Case]) -> list[str]:
    loaded = {type(e).__name__ for case in cases if case.expect == "load" for e in beancount(case)[0]}
    return sorted(DIRECTIVES - loaded)


def test_every_directive_type_is_loaded_by_a_load_case(tmp_path: Path) -> None:
    assert len(DIRECTIVES) == 12  # Beancount 3.2.0
    assert directives_missing(every_case(tmp_path)) == []


def test_a_directive_type_no_case_loads_is_found(tmp_path: Path) -> None:
    cases = [c for c in every_case(tmp_path) if c.name not in {"documents", "absolute-paths"}]
    assert directives_missing(cases) == ["Document"]


def test_the_example_generator_is_pinned() -> None:
    text = parity.example_ledger()
    assert parity.example_ledger() == text  # the same text twice in one process
    assert hashlib.sha256(text.encode()).hexdigest() == parity.EXAMPLE_PIN["sha256"]
    begin, end = parity.EXAMPLE_DATES[1:]
    assert f";; Dates: {begin} - {end}" in text


def test_a_refuse_case_whose_file_beancount_would_not_decrypt_is_found(tmp_path: Path) -> None:
    """A refusal is only parity when Beancount itself would decrypt the file: an `.asc` file without a PGP header is
    a plain ledger file for Beancount, so a corpus case that claims it must be refused is wrong."""
    case = {c.name: c for c in every_case(tmp_path)}["encrypted-root-asc"]
    case.root.write_text("2020-01-01 open Assets:Cash USD\n", encoding="utf-8")
    (failure,) = run(refused, [case])
    assert failure.startswith(f"encrypted-root-asc: encrypted-root-asc: Beancount would not decrypt {case.root}")


def test_a_recursive_glob_that_matches_one_depth_is_not_counted_as_recursion(tmp_path: Path) -> None:
    root = tmp_path / "main.beancount"
    root.write_text('include "d/**/*.beancount"\n', encoding="utf-8")
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "x.beancount").write_text("2020-01-01 open Assets:Cash\n", encoding="utf-8")
    case = Case("planted", root, "load", ("include-recursive-glob",))
    assert not parity.FEATURES["include-recursive-glob"](facts(case, beancount(case)))
    (tmp_path / "d" / "e").mkdir()
    (tmp_path / "d" / "e" / "y.beancount").write_text("2020-01-01 open Assets:Bank\n", encoding="utf-8")
    assert parity.FEATURES["include-recursive-glob"](facts(case, beancount(case)))


# ---- must-not-pass: seeded divergences in the overlay are caught --------------------------------------------------


def _drop_last_entry(mp: pytest.MonkeyPatch) -> None:
    real = port.parse_string

    def parse_string(data: bytes, report_filename: str | None = None) -> port.ParseResult:
        entries, errors, options = real(data, report_filename)
        return entries[:-1], errors, options

    mp.setattr(port, "parse_string", parse_string)


def _no_plugins(mp: pytest.MonkeyPatch) -> None:
    def run_transformations(
        entries: list[port.Directive], errors: list[port.BeancountError], options: port.OptionsMap
    ) -> tuple[list[port.Directive], list[port.BeancountError]]:
        return entries, list(errors)

    mp.setattr(port, "run_transformations", run_transformations)


def _glob_not_recursive(mp: pytest.MonkeyPatch) -> None:
    mp.setattr(overlay_module.fp, "matches", glob.glob)


def _includes_not_merged(mp: pytest.MonkeyPatch) -> None:
    def aggregate_options(top: port.OptionsMap, others: list[port.OptionsMap]) -> port.OptionsMap:
        return {**top, "pythonpath": [], "operating_currency": list(top["operating_currency"])}

    mp.setattr(port, "aggregate_options", aggregate_options)


def _validation_errors_dropped(mp: pytest.MonkeyPatch) -> None:
    def validate(entries: list[port.Directive], options: port.OptionsMap) -> list[port.BeancountError]:
        return []

    mp.setattr(port, "validate", validate)


def _an_option_changed(mp: pytest.MonkeyPatch) -> None:
    real = port.aggregate_options

    def aggregate_options(top: port.OptionsMap, others: list[port.OptionsMap]) -> port.OptionsMap:
        return {**real(top, others), "render_commas": True}

    mp.setattr(port, "aggregate_options", aggregate_options)


def _error_message_changed(mp: pytest.MonkeyPatch) -> None:
    real = port.load_error

    def load_error(message: str) -> port.BeancountError:
        return real(message.replace("Duplicate", "Repeated"))

    mp.setattr(port, "load_error", load_error)


def _encrypted_read(mp: pytest.MonkeyPatch) -> None:
    def never(filename: str) -> bool:
        return False

    mp.setattr(overlay_module, "_beancount_decrypts", never)


def _candidates_ignored(mp: pytest.MonkeyPatch) -> None:
    real = overlay_module.load

    def ignore(root: str, overlay: Any, policy: Any) -> Loaded | LoadRefused:
        return real(root, {k: Path(k).read_bytes() for k in overlay if Path(k).exists()}, policy)

    mp.setattr(overlay_module, "load", ignore)


def _entries_reordered(mp: pytest.MonkeyPatch) -> None:
    real = port.validate

    def validate(entries: list[port.Directive], options: port.OptionsMap) -> list[port.BeancountError]:
        entries.reverse()  # the same entries, in another order
        return real(entries, options)

    mp.setattr(port, "validate", validate)


def _a_flag_changed(mp: pytest.MonkeyPatch) -> None:
    real = port.parse_string

    def parse_string(data: bytes, report_filename: str | None = None) -> port.ParseResult:
        entries, errors, options = real(data, report_filename)
        return [e._replace(flag="!") if isinstance(e, port.Transaction) else e for e in entries], errors, options

    mp.setattr(port, "parse_string", parse_string)


def _an_extra_option(mp: pytest.MonkeyPatch) -> None:
    real = port.aggregate_options

    def aggregate_options(top: port.OptionsMap, others: list[port.OptionsMap]) -> port.OptionsMap:
        return {**real(top, others), "parity_extra": True}

    mp.setattr(port, "aggregate_options", aggregate_options)


def _precision_not_merged(mp: pytest.MonkeyPatch) -> None:
    real = port.aggregate_options

    def aggregate_options(top: port.OptionsMap, others: list[port.OptionsMap]) -> port.OptionsMap:
        return real(top, [{**o, "dcontext": type(o["dcontext"])()} for o in others])

    mp.setattr(port, "aggregate_options", aggregate_options)


def _input_hash_wrong(mp: pytest.MonkeyPatch) -> None:
    def input_hash(paths: list[str]) -> str:
        return "parity"

    mp.setattr(port, "input_hash", input_hash)


def _refusal_names_the_root(mp: pytest.MonkeyPatch) -> None:
    real = overlay_module.load

    def load(root: str, overlay: Any, policy: Any) -> Loaded | LoadRefused:
        result = real(root, overlay, policy)
        return LoadRefused(result.reason, root) if isinstance(result, LoadRefused) else result

    mp.setattr(overlay_module, "load", load)


def _an_error_source_moved(mp: pytest.MonkeyPatch) -> None:
    real = port.validate

    def validate(entries: list[port.Directive], options: port.OptionsMap) -> list[port.BeancountError]:
        errors = real(entries, options)
        first = errors[0]
        error_type: Any = type(first)  # Beancount's errors are named tuples (source, message, entry)
        moved: port.BeancountError = error_type(
            {**first.source, "lineno": first.source["lineno"] + 1}, first.message, first.entry
        )
        return [moved, *errors[1:]]  # the same type and message, another line

    mp.setattr(port, "validate", validate)


def _errors_reordered(mp: pytest.MonkeyPatch) -> None:
    real = port.validate

    def validate(entries: list[port.Directive], options: port.OptionsMap) -> list[port.BeancountError]:
        errors = real(entries, options)
        assert len(errors) >= 2, "the target case must have two validation errors"
        return errors[::-1]

    mp.setattr(port, "validate", validate)


DIVERGENCES: dict[str, tuple[Callable[[pytest.MonkeyPatch], None], str, Callable[[Case], None]]] = {
    "an entry dropped": (_drop_last_entry, "fava-features", empty_overlay),
    "plugins not run": (_no_plugins, "plugins-local", empty_overlay),
    "a glob followed without recursion": (_glob_not_recursive, "example-split", empty_overlay),
    "options of includes not merged": (_includes_not_merged, "plugins-local", disk_bytes_as_candidates),
    "validation errors dropped": (_validation_errors_dropped, "options", empty_overlay),
    "an option changed": (_an_option_changed, "custom-roots", empty_overlay),
    "a loader error worded differently": (_error_message_changed, "includes", empty_overlay),
    "an encrypted root read": (_encrypted_read, "encrypted-root-gpg", refused),
    "an encrypted include read": (_encrypted_read, "encrypted-nested", refused),
    "a candidate ignored": (_candidates_ignored, "custom-roots", round_trip),
    "the same entries in another order": (_entries_reordered, "fava-features", empty_overlay),
    "an entry's content changed": (_a_flag_changed, "custom-roots", empty_overlay),
    "an option the loader has not": (_an_extra_option, "fava-features", empty_overlay),
    "display precision of includes not merged": (_precision_not_merged, "includes", empty_overlay),
    "the input hash wrong": (_input_hash_wrong, "fava-features", empty_overlay),
    "a refusal naming another file": (_refusal_names_the_root, "encrypted-nested", refused),
    "an error's source moved": (_an_error_source_moved, "options", empty_overlay),
    "the same errors in another order": (_errors_reordered, "options", empty_overlay),
}


def test_each_seeded_divergence_in_the_overlay_is_caught(tmp_path: Path) -> None:
    survived: list[str] = []
    for label, (seed, target, check) in DIVERGENCES.items():
        cases = {c.name: c for c in every_case(tmp_path / re.sub(r"\W+", "-", label))}
        with pytest.MonkeyPatch.context() as mp:
            seed(mp)
            caught = run(check, [cases[target]])
        if not caught:
            survived.append(label)
        # the same check passes without the seed (the divergence, not the case, is what failed)
        clean = {c.name: c for c in every_case(tmp_path / (re.sub(r"\W+", "-", label) + "-clean"))}
        assert run(check, [clean[target]]) == [], label
    assert survived == []
    assert len(DIVERGENCES) == 18
