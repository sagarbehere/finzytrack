"""P1 integrity tests for setup and lifecycle runs (review:27 §1(d), Appendix B: green in M3): L10-02, L10.M-2, L10-06.

Spec: ADR-0019 ("Setup is a plan: `plan_create_workspace` … pure, show every file to be written, never overwrite,
apply with a backup"; "No backup, no write for every lifecycle step"; "A file whose bytes equal any shipped version
counts as pristine"); review:L10 §1.4 (the plan offers "open the existing file" or "use another name"), review:L10-verif
M-2 (a failed backup must stop the step and be reported, not `errors: []`), review:L10 L10-06 / L10-verif §1 (a crash
between writing a seed file and recording it must not make it look user-modified).

Interface: `boot.LifecyclePlan`, `boot.execute(plan, consent)`, `boot.ContentPack` (ARCHITECTURE.md §2.3);
`plan_create_workspace` (ADR-0019). Assumed, not decided: the module paths below, a plan's `.writes` (paths it would
write) and `.conflicts`, `execute(...)` returning a run report with `.applied`, `.failed` and `.errors`, the op
`Write(path, data)`, the backup root passed to `execute`, and `classify_seed(on_disk, shipped, recorded)`.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from tests.integrity.planned import planned

USER_LEDGER = b'option "operating_currency" "EUR"\n2020-01-01 open Assets:Bank EUR\n; my books\n'


@pytest.mark.fixme(id="L10-02", due="M3", reason="capability not built")
def test_setup_never_overwrites_an_existing_ledger(tmp_path: Path) -> None:
    plan_create_workspace = planned("app.boot.setup", "plan_create_workspace")
    execute = planned("app.boot", "execute")
    folder = tmp_path / "Finance"
    folder.mkdir()
    (folder / "main.beancount").write_bytes(USER_LEDGER)  # review:L10 demo A3: re-entering setup over real books

    plan = plan_create_workspace(folder=folder, ledger_name="main.beancount", currency="USD")

    assert folder / "main.beancount" not in set(plan.writes)
    assert [c.path for c in plan.conflicts] == [folder / "main.beancount"]
    report = execute(plan, consent=True, backup_root=tmp_path / "backups")
    assert report.applied == []  # a plan with a conflict is not applied
    assert (folder / "main.beancount").read_bytes() == USER_LEDGER


@pytest.mark.fixme(id="L10.M-2", due="M3", reason="capability not built")
def test_no_backup_no_write(tmp_path: Path) -> None:
    lifecycle_plan = planned("app.boot", "LifecyclePlan")
    write = planned("app.boot", "Write")
    execute = planned("app.boot", "execute")
    target = tmp_path / "ws" / ".finzytrack" / "dashboards" / "home.json"
    target.parent.mkdir(parents=True)
    target.write_bytes(b'{"version": 1}\n')
    blocked = tmp_path / "backups"
    blocked.write_bytes(b"")  # a file where the backup folder must go: creating the backup fails (review:L10-verif VM3)

    report = execute(lifecycle_plan(ops=[write(target, b'{"version": 2}\n')]), consent=True, backup_root=blocked)

    assert target.read_bytes() == b'{"version": 1}\n'  # the step did not run without its backup
    assert report.failed == [target]
    assert report.errors != []  # reported, never a silent `errors: []`


@pytest.mark.fixme(id="L10-06", due="M3", reason="capability not built")
def test_a_seed_file_equal_to_any_shipped_version_is_pristine() -> None:
    classify_seed = planned("app.boot.packs", "classify_seed")
    shipped = [b'{"title": "Home", "v": 1}\n', b'{"title": "Home", "v": 2}\n', b'{"title": "Home", "v": 3}\n']
    recorded_v1 = hashlib.sha256(shipped[0]).hexdigest()

    # A crash after writing v2 but before recording it: the bytes are a shipped version, so it is pristine.
    assert classify_seed(on_disk=shipped[1], shipped=shipped, recorded=recorded_v1) == "pristine"
    assert classify_seed(on_disk=shipped[2], shipped=shipped, recorded=None) == "pristine"
    # Bytes equal to no shipped version are the user's edit (ADR-0019: pristine means equal to a shipped version).
    edited = b'{"title": "My home", "v": 2}\n'
    assert classify_seed(on_disk=edited, shipped=shipped, recorded=recorded_v1) == "modified"
