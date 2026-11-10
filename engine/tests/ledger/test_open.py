"""The open job inside the engine (`ledger_engine.ledger.opening`; `design/specs/engine-jobs.md` §2's `open`, §7;
`design/specs/mirror/publish.md` §4, §8; ADR-0015, ADR-0064 decision 9, ADR-0012 F-6, ADR-0059; S0088, BL-0152), on
real APFS in scratch folders. Written from the spec's lines:

- §2: `open` is "the open's mirror build when the fingerprint differs from the published generation"; publish.md §4:
  "the open: its overlay load", then steps 2 to 8 of the publish, as the rebuild does; §8: a published generation that
  is fresh builds nothing;
- §7: "Before returning a result or publishing (for a publish, just before it), the job re-computes the fingerprint and
  checks that this workspace has no `.open` journal"; "A moved fingerprint discards the result" (the sidecar runs the
  job again); "A `.open` journal refuses the job with `WRITES_PAUSED {unfinished_save}`";
- ADR-0059: an encrypted file is refused, never decrypted;
- the owner (2026-11-08, "Only its own mirror"): the open writes and deletes only in this workspace's own mirror
  folder; the ledger, the journals, `.finzytrack/` and another key's folder stay as they were.
"""

from __future__ import annotations

import os
import shutil
import sqlite3
import stat
import sys
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any, override

import pytest

from ledger_engine.fs.darwin import for_this_os
from ledger_engine.ledger import beancount_port as port
from ledger_engine.ledger import opening, overlay
from ledger_engine.ledger.core.ledger_set import decode
from ledger_engine.ledger.fingerprint import observe
from ledger_engine.ledger.policy import DESKTOP
from ledger_engine.mirror import publish as pb
from ledger_engine.mirror.store import SqliteStore
from tests.mirror.publish_support import BUILT_AT, ENGINE_BUILD, KEY, TODAY, fs_now, meta

pytestmark = [
    pytest.mark.inv("INV-05"),
    pytest.mark.inv("INV-30"),
    pytest.mark.skipif(sys.platform != "darwin", reason="the write path's OS parts are macOS-only until BL-0087"),
]

MAIN = b'option "operating_currency" "USD"\ninclude "inc.bean"\n2026-01-01 open Assets:Cash\n'
INC = b'2026-01-01 open Expenses:Food\n2026-01-02 * "Lunch"\n  Expenses:Food  5 USD\n  Assets:Cash\n'


@dataclass
class FakeJob:
    events: list[str] = field(default_factory=list[str])

    def progress(self, phase: opening.Phase) -> None:
        self.events.append(phase)


@dataclass(frozen=True)
class World:
    root: Path
    cache: Path
    app: Path

    def target(self, **kw: Any) -> opening.Target:
        t = opening.Target(
            app_cache=str(self.cache),
            workspace_key=KEY,
            app_local_data=str(self.app),
            lineage="lin1",
            floor=0,
            today=TODAY,
            built_at=BUILT_AT,
            now_ns=fs_now(self.cache),
            engine_build=ENGINE_BUILD,
        )
        return replace(t, **kw)

    def go(self, ports: opening.Ports | None = None, root: Path | None = None, **kw: Any) -> opening.Answer:
        return opening.run(ports or opening.Ports(), str(root or self.root), FakeJob(), self.target(**kw))

    @property
    def mirror(self) -> Path:
        return self.cache / "workspaces" / KEY / "mirror"

    @property
    def journal(self) -> Path:
        return self.app / "local" / KEY / "journal"

    def current(self) -> str | None:
        link = self.mirror / pb.CURRENT
        return os.readlink(link) if link.is_symlink() else None

    def generations(self) -> list[str]:
        return sorted(p.name for p in self.mirror.iterdir() if pb.gen_of(p.name) is not None)

    def cut_save(self) -> None:
        """An unfinished save of this workspace: its `.open` journal (what a crash leaves; recovery's to resolve)."""
        self.journal.mkdir(parents=True, exist_ok=True)
        (self.journal / "s1-j1.open").write_bytes(b"{}")


@pytest.fixture
def w(tmp_path: Path) -> Iterator[World]:
    led = tmp_path / "L"
    led.mkdir()
    (led / "main.bean").write_bytes(MAIN)
    (led / "inc.bean").write_bytes(INC)
    cache = tmp_path / "cache"
    cache.mkdir()
    app = tmp_path / "app"
    app.mkdir()
    yield World(led / "main.bean", cache, app)
    for p in (app, led):  # a test may have taken a folder's rights away
        for d in [p, *(q for q in p.rglob("*") if q.is_dir() and not q.is_symlink())]:
            d.chmod(0o700)


def overlay_fp(root: Path) -> Any:
    loaded = overlay.load(str(root), {}, DESKTOP)
    assert isinstance(loaded, overlay.Loaded)
    return loaded.fingerprint


def counting(loads: list[str], around: Callable[[], object] | None = None) -> opening.Ports:
    """The overlay load, counted, with an outside program's act right after it."""

    def load(r: str) -> overlay.LoadResult:
        loads.append(r)
        out = overlay.load(r, {}, DESKTOP)
        if around is not None:
            around()
        return out

    return opening.Ports(load=load)


class EditingStore(SqliteStore):
    """An outside act while the build writes the generation."""

    def __init__(self, act: Callable[[], object]) -> None:
        self._act = act

    @override
    def build(self, *args: Any, **kw: Any) -> Any:
        out = super().build(*args, **kw)
        self._act()
        return out


# -- the load and the publish -----------------------------------------------------------------------------------------


def test_the_open_loads_with_the_overlay_load_and_publishes_what_it_parsed(w: World) -> None:
    job = FakeJob()
    a = opening.run(opening.Ports(), str(w.root), job, w.target())
    real_entries, real_errors, _ = port.real_load(str(w.root))
    assert (a.outcome, a.entries, a.errors) == ("loaded", len(real_entries), len(real_errors))
    assert job.events == ["reading"]
    assert isinstance(a.published, pb.Published), a
    assert a.published.gen.number == 1
    name = w.current()
    assert name == a.published.gen.name
    facts = meta(w.mirror / a.published.gen.name)
    # stamped with exactly what the overlay load parsed (publish.md §8)
    assert decode(facts["fingerprint"].encode("ascii")) == overlay_fp(w.root)
    assert facts["ledger_version"] == a.published.ledger_version
    assert facts["as_of"] == TODAY.isoformat()


def test_an_include_glob_that_matches_nothing_is_in_the_stamp(w: World) -> None:
    """The overlay load knows the ledger's patterns (the rebuild does not: publish.md §11 "the stamp's globs"), so a
    file appearing later under a pattern that matched nothing makes the generation not fresh."""
    (w.root.parent / "extra").mkdir()
    w.root.write_bytes(MAIN + b'include "extra/*.bean"\n')
    assert w.go().outcome == "loaded"
    got = pb.read_published(for_this_os(), str(w.cache), KEY, SqliteStore())
    assert got is not None

    def ours(r: str) -> bool:
        return pb.same_ledger(for_this_os(), r, str(w.root))

    def obs(fp: Any) -> Any:
        return observe(fp.ledger_set)

    assert pb.fresh_on_disk(got.meta, obs, ours)
    (w.root.parent / "extra" / "a.bean").write_bytes(b"2026-01-01 open Assets:A\n")
    assert not pb.fresh_on_disk(got.meta, obs, ours)


def test_an_open_that_finds_the_published_generation_fresh_builds_nothing(w: World) -> None:
    first = w.go()
    assert isinstance(first.published, pb.Published)
    loads: list[str] = []
    again = w.go(counting(loads))
    assert again.outcome == "fresh"
    assert again.published == first.published
    assert loads == []
    assert w.generations() == [first.published.gen.name]


@pytest.mark.parametrize("change", ["today", "engine_build", "disk", "glob_match"])
def test_a_generation_that_is_not_fresh_is_rebuilt(w: World, change: str) -> None:
    sub = w.root.parent / "sub"
    sub.mkdir()
    w.root.write_bytes(MAIN + b'include "sub/*.bean"\n')
    first = w.go()
    assert first.outcome == "loaded"
    kw: dict[str, Any] = {}
    if change == "today":
        kw["today"] = date(2026, 11, 9)
    elif change == "engine_build":
        kw["engine_build"] = "a newer engine"
    elif change == "disk":
        (w.root.parent / "inc.bean").write_bytes(INC + b"; edited\n")
    else:
        (sub / "a.bean").write_bytes(b"2026-01-01 open Assets:A\n")
    a = w.go(**kw)
    assert a.outcome == "loaded"
    assert isinstance(a.published, pb.Published)
    assert a.published.gen.number == 2


def test_the_generation_of_another_ledger_under_this_key_is_not_fresh(w: World, tmp_path: Path) -> None:
    """S0087 round 1 (red team F2) for the open: a ledger located elsewhere under the same key is loaded."""
    assert w.go().outcome == "loaded"
    new = tmp_path / "New"
    shutil.copytree(w.root.parent, new)
    a = w.go(root=new / "main.bean")
    assert a.outcome == "loaded"
    assert isinstance(a.published, pb.Published)
    assert a.published.gen.number == 2


def test_the_number_is_above_the_sidecars_floor(w: World) -> None:
    a = w.go(floor=41)
    assert isinstance(a.published, pb.Published)
    assert a.published.gen.number == 42


# -- §7: a moved fingerprint ------------------------------------------------------------------------------------------


def test_an_edit_during_the_load_is_moved_and_nothing_is_published(w: World) -> None:
    inc = w.root.parent / "inc.bean"
    a = w.go(counting([], lambda: inc.write_bytes(INC + b"; edited\n")))
    assert a.outcome == "moved"
    assert a.published is None
    assert w.current() is None


def test_an_edit_during_the_build_is_moved_and_the_published_generation_stays(w: World) -> None:
    inc = w.root.parent / "inc.bean"
    first = w.go()
    assert isinstance(first.published, pb.Published)
    inc.write_bytes(INC + b"; one\n")
    a = w.go(opening.Ports(store=EditingStore(lambda: inc.write_bytes(INC + b"; two\n"))))
    assert a.outcome == "moved"
    assert a.published is None
    assert w.current() == first.published.gen.name
    assert len(w.generations()) == 2  # the unpublished build waits for the next cleanup


def test_a_file_appearing_under_a_glob_during_the_build_is_moved(w: World) -> None:
    """The stamp names the glob, so the re-check sees its new match (publish.md §4 step 7)."""
    sub = w.root.parent / "sub"
    sub.mkdir()
    w.root.write_bytes(MAIN + b'include "sub/*.bean"\n')
    a = w.go(opening.Ports(store=EditingStore(lambda: (sub / "a.bean").write_bytes(b"2026-01-01 open Assets:A\n"))))
    assert a.outcome == "moved"
    assert w.current() is None


def test_the_cleanup_runs_first_even_when_the_load_then_moves(w: World) -> None:
    w.mirror.mkdir(parents=True)
    (w.mirror / "junk").write_bytes(b"x")
    inc = w.root.parent / "inc.bean"
    a = w.go(counting([], lambda: inc.write_bytes(INC + b"; edited\n")))
    assert a.outcome == "moved"
    assert a.cleaned is not None
    assert a.cleaned.removed == 1
    assert not (w.mirror / "junk").exists()


# -- §7: an `.open` journal --------------------------------------------------------------------------------------------


def test_an_open_journal_before_the_load_is_paused_and_nothing_is_loaded(w: World) -> None:
    w.cut_save()
    loads: list[str] = []
    a = w.go(counting(loads))
    assert a.outcome == "paused"
    assert loads == []
    assert a.published is None
    assert w.current() is None


def test_an_open_journal_beside_a_fresh_generation_is_paused_not_fresh(w: World) -> None:
    """§7: the check comes before returning any result, a fresh one included."""
    assert w.go().outcome == "loaded"
    w.cut_save()
    a = w.go()
    assert a.outcome == "paused"
    assert a.published is None


def test_an_open_journal_seen_at_the_recheck_publishes_nothing(w: World) -> None:
    a = w.go(counting([], w.cut_save))
    assert a.outcome == "paused"
    assert a.published is None
    assert w.current() is None


def test_a_journal_folder_that_cannot_be_listed_is_paused(w: World) -> None:
    """Fail closed, as the save's step 1: an unlisted folder may hold an unfinished save."""
    w.journal.mkdir(parents=True)
    w.journal.chmod(0)
    loads: list[str] = []
    a = w.go(counting(loads))
    assert a.outcome == "paused"
    assert loads == []


# -- ADR-0059 ---------------------------------------------------------------------------------------------------------


def test_an_encrypted_include_is_refused_and_never_decrypted(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    secret = w.root.parent / "secret.bean.gpg"
    secret.write_bytes(b"\x85\x01not really")
    w.root.write_bytes(MAIN + b'include "secret.bean.gpg"\n')

    def no_gpg(*_: object, **__: object) -> object:
        msg = "gpg ran"
        raise AssertionError(msg)

    monkeypatch.setattr("subprocess.run", no_gpg)
    monkeypatch.setattr("subprocess.Popen", no_gpg)
    a = w.go()
    assert a.outcome == "refused"
    assert a.refused == str(secret)
    assert a.published is None
    assert w.current() is None


# -- the owner's rule (2026-11-08): only its own mirror folder --------------------------------------------------------


def snapshot(*roots: Path) -> dict[str, tuple[object, ...]]:
    """Every entry under `roots`, the roots included, by path: its kind, its bytes or link target, its modification
    time, its mode (never followed through a link)."""
    out: dict[str, tuple[object, ...]] = {}
    for top in roots:
        for p in [top, *sorted(top.rglob("*"))]:
            st = p.lstat()
            if stat.S_ISLNK(st.st_mode):
                body: object = os.readlink(p)
            elif stat.S_ISREG(st.st_mode):
                body = p.read_bytes()
            else:
                body = sorted(q.name for q in p.iterdir())
            out[str(p)] = (stat.S_IFMT(st.st_mode), body, st.st_mtime_ns, st.st_mode)
    return out


def others(w: World) -> tuple[Path, ...]:
    """What the open must leave as it is: the workspace folder (the ledger, its `.finzytrack/`), the app's
    machine-local folder (the journals, the backups), and another workspace's mirror folder in the same cache."""
    folder = w.root.parent
    (folder / ".finzytrack").mkdir(exist_ok=True)
    (folder / ".finzytrack" / "workspace.json").write_bytes(b'{"lineage": "lin1"}\n')
    for name in ("journal", "backups"):
        (w.app / "local" / KEY / name).mkdir(parents=True, exist_ok=True)
    (w.app / "local" / KEY / "journal" / "s0-j0.closed").write_bytes(b"{}")
    (w.app / "local" / KEY / "backups" / "s0-j0.main.bean").write_bytes(MAIN)
    other = w.cache / "workspaces" / "k2" / "mirror"
    other.mkdir(parents=True)
    (other / "gen-1-0123456789abcdef.db").write_bytes(b"another workspace's generation")
    (other / pb.CURRENT).symlink_to("gen-1-0123456789abcdef.db")
    (other / "junk").write_bytes(b"not this job's to clean")
    return (folder, w.app, w.cache / "workspaces" / "k2")


@pytest.mark.parametrize("case", ["published", "fresh", "moved", "paused", "refused"])
def test_the_open_writes_nothing_outside_its_own_mirror_folder(w: World, case: str) -> None:
    """Must-not-pass (owner, 2026-11-08): in every outcome, the ledger, its `.finzytrack/`, the journals and backups,
    and another key's mirror folder keep their bytes and modification times."""
    roots = others(w)
    act: Callable[[], object] = lambda: None  # noqa: E731
    ports: opening.Ports | None = None
    if case == "fresh":
        assert w.go().outcome == "loaded"
    elif case == "paused":
        w.cut_save()
    elif case == "refused":
        (w.root.parent / "secret.bean.gpg").write_bytes(b"\x85\x01not really")
        w.root.write_bytes(MAIN + b'include "secret.bean.gpg"\n')
    elif case == "moved":
        sub = w.root.parent / "sub"
        sub.mkdir()
        w.root.write_bytes(MAIN + b'include "sub/*.bean"\n')
        act = lambda: (sub / "late.bean").write_bytes(b"; late\n")  # noqa: E731
        ports = opening.Ports(store=EditingStore(lambda: act()))
    before = snapshot(*roots)
    if case == "moved":
        before.pop(str(w.root.parent / "sub"))  # the outside program's own act changes its folder
    a = w.go(ports)
    assert a.outcome == ("loaded" if case == "published" else case)
    after = snapshot(*roots)
    if case == "moved":
        after.pop(str(w.root.parent / "sub"))
        after.pop(str(w.root.parent / "sub" / "late.bean"))
    assert after == before


# -- S0088 round 1: the open's fresh shortcut takes only a generation an open published (owner, 2026-11-08) -----------


def test_a_generation_an_open_published_says_so(w: World) -> None:
    a = w.go()
    assert isinstance(a.published, pb.Published)
    assert meta(w.mirror / a.published.gen.name)["published_by"] == "open"


def _rebuild_published(w: World, hint: Any) -> None:
    """The rebuild after a save publishes a generation (stamped with what its real load read)."""
    from ledger_engine.ledger import rebuild

    class Job:
        def progress(self, phase: rebuild.Phase) -> None:
            pass

    assert rebuild.run(rebuild.Ports(), hint, Job(), w.target()).outcome == "loaded"
    assert meta(w.mirror / str(w.current()))["published_by"] == "rebuild"


def test_after_a_rebuild_published_the_next_open_reloads_once(w: World) -> None:
    """Owner, 2026-11-08 ("Open reloads after saves"): after any save the next open loads, even when nothing changed;
    the open after that keeps its own generation."""
    _rebuild_published(w, overlay_fp(w.root))
    loads: list[str] = []
    assert w.go(counting(loads)).outcome == "loaded"
    assert loads == [str(w.root)]
    assert w.go(counting(loads)).outcome == "fresh"
    assert len(loads) == 1


@pytest.mark.parametrize("include", [b'include "extra/*.bean"\n', b'include "2027.bean"\n'])
def test_an_include_added_before_the_rebuild_is_seen_by_the_next_open(w: World, include: bytes) -> None:
    """S0088 round 1, red team F1 (r1, r1b) and verifier F1 (V_A): an outside edit adds an include, matching nothing
    yet, between a save and its rebuild; the rebuild's stamp lacks it (its hint did not name it); a file then appears
    under it. The open must not keep that generation: it loads and its stamp names the file."""
    hint = overlay_fp(w.root)  # the save's plan: no such include
    (w.root.parent / "extra").mkdir()
    w.root.write_bytes(MAIN + include)
    _rebuild_published(w, hint)
    new = w.root.parent / ("extra/a.bean" if b"extra" in include else "2027.bean")
    new.write_bytes(b'2026-01-01 open Expenses:Rent\n2026-01-03 * "Rent"\n  Expenses:Rent  999 USD\n  Assets:Cash\n')
    a = w.go()
    assert a.outcome == "loaded"
    got = pb.read_published(for_this_os(), str(w.cache), KEY, SqliteStore())
    assert got is not None
    assert str(new) in dict(decode(got.meta.fingerprint.encode("ascii")).files)


# -- a ledger Beancount's own load fails on, and numbers it rounds (BL-0123, S0098) ----------------------------------

LONG_CRASH = b'2026-01-03 * "Coins"\n  Assets:Cash  12345678901.123456789012345678 ETH\n  Expenses:Food\n'
DOC_PLUGIN = (
    b"import datetime\nfrom beancount.core import data\n__plugins__ = ['add']\n"
    b"def add(entries, options):\n"
    b"    meta = data.new_metadata('<plugin>', 0)\n"
    b"    doc = data.Document(meta, datetime.date(2026, 1, 2), 'Assets:Cash', 42, frozenset(), frozenset())\n"
    b"    return entries + [doc], []\n"
)


def test_a_number_beancount_cannot_compute_refuses_the_open_naming_its_line(w: World) -> None:
    (w.root.parent / "inc.bean").write_bytes(INC + LONG_CRASH)
    a = w.go()
    assert (a.outcome, a.refusal, a.refused, a.line) == ("refused", "number-failed", str(w.root.parent / "inc.bean"), 5)
    assert a.published is None
    assert w.current() is None


def test_a_plugins_entry_beancount_cannot_check_refuses_the_open(w: World) -> None:
    (w.root.parent / "plug_doc_open.py").write_bytes(DOC_PLUGIN)
    w.root.write_bytes(b'option "insert_pythonpath" "TRUE"\nplugin "plug_doc_open"\n' + MAIN)
    a = w.go()
    assert (a.outcome, a.refusal, a.refused, a.line) == ("refused", "beancount-failed", str(w.root), None)
    assert w.current() is None


def test_a_ledger_fixed_while_beancount_failed_on_it_is_moved_not_refused(w: World) -> None:
    """S0098 round 1, data-safety F2: the disk is observed again before a refusal is answered."""
    inc = w.root.parent / "inc.bean"
    inc.write_bytes(INC + LONG_CRASH)

    def load(r: str) -> overlay.LoadResult:
        out = overlay.load(r, {}, DESKTOP)
        inc.write_bytes(INC)  # an outside editor's fix lands while Beancount was failing on the old text
        return out

    a = w.go(opening.Ports(load=load))
    assert a.outcome == "moved"
    assert w.current() is None


def test_an_encrypted_file_is_still_its_own_refusal(w: World) -> None:
    secret = w.root.parent / "secret.bean.gpg"
    secret.write_bytes(b"\x85\x01not really")
    w.root.write_bytes(MAIN + b'include "secret.bean.gpg"\n')
    a = w.go()
    assert (a.outcome, a.refusal, a.refused, a.line) == ("refused", "encrypted", str(secret), None)


def test_the_warnings_of_numbers_beancount_rounded_are_published_but_are_no_errors(w: World) -> None:
    long = b"12345678901.123456789012345678"
    (w.root.parent / "inc.bean").write_bytes(
        INC + b'2026-01-03 * "Coins"\n  Assets:Cash  ' + long + b" ETH\n  Expenses:Food  -" + long + b" ETH\n"
    )
    a = w.go()
    _, real_errors, _ = port.real_load(str(w.root))
    assert (a.outcome, a.errors) == ("loaded", len(real_errors))  # Beancount's errors, as bean-check counts them
    assert isinstance(a.published, pb.Published)
    con = sqlite3.connect(f"file:{w.mirror / a.published.gen.name}?mode=ro", uri=True)
    got = con.execute("SELECT line_number FROM ledger_errors WHERE message LIKE 'Number longer than 28 digits%'")
    assert [r[0] for r in got] == [6, 7]


# -- the plugin code the load imports (S0099, BL-0124) ----------------------------------------------------------------

PLUGIN = '__plugins__ = ["keep"]\n\n\ndef keep(entries, options_map):\n    return entries, []\n'


@pytest.mark.parametrize("change", ["edit", "appear", "shadow"])
def test_a_generation_whose_plugin_code_changed_is_not_fresh(w: World, change: str) -> None:
    """publish.md §8: fresh means the next load would read the same inputs, the plugin code included. `shadow`: a file
    beside the ledger replaces a standard library module a plugin imports (the `pythonpath` folder comes first)."""
    name = f"ft_s0099_open_{change}"
    plugin = w.root.parent / f"{name}.py"
    try:
        w.root.write_bytes(b'option "insert_pythonpath" "TRUE"\nplugin "' + name.encode() + b'"\n' + MAIN)
        if change != "appear":
            plugin.write_text("import colorsys\n" + PLUGIN)
        sys.modules.pop("colorsys", None)
        first = w.go()
        assert first.outcome == "loaded", first
        sys.modules.pop(name, None)
        assert w.go().outcome == "fresh"
        if change == "edit":
            plugin.write_text("import colorsys\n" + PLUGIN + "# edited\n")
        elif change == "appear":
            plugin.write_text(PLUGIN)
        else:
            (w.root.parent / "colorsys.py").write_text("")
        sys.modules.pop(name, None)  # the next open's engine is fresh (spec §5.1)
        sys.modules.pop("colorsys", None)
        a = w.go()
        assert a.outcome == "loaded", a
        assert isinstance(a.published, pb.Published)
        assert a.published.gen.number == 2
    finally:
        sys.modules.pop(name, None)
        sys.modules.pop("colorsys", None)
        (w.root.parent / "colorsys.py").unlink(missing_ok=True)
