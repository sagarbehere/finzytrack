"""P1 integrity tests for the Ledger Profile's unknown account (L3-14, due M4) and document cleanup (L11.M-2, due M5).

review:27 §1(d), Appendix B (L3-14 in M4; L11.M-2 in M5).

L3-14. Spec: ADR-0024 (the Ledger Profile, backend-owned, carries "the unknown account"; the frontend never invents
ledger facts), review:L3 L3-14 (four converters wrote the literal `Expenses:Unknown`; with a customised unknown account
imported rows posted to an account that may not exist), review:L4 §1.4 (an open slot becomes the profile's unknown
account at commit), INV-15 (never propose an account name). Interface: `accounts.usecases` serves the Profile
(ARCHITECTURE.md §2.2: "accounts/ SLICE. Ledger Profile"). **Not decided:** where the user names the unknown account;
this test assumes the workspace setting `accounts.default_unknown_account` (the v0.2 config key) and the use case
`accounts.usecases.ledger_profile(ws_root)`. The M4 slice decides the source; the assertion that matters is the last
two lines: the configured name is served, and no backend module carries the literal.

L11.M-2. Spec: review:L11-verif M-2 (with `option "documents" "."` the orphan scan offered `main.beancount` and
`accounts.beancount` for deletion), review:99 WS-ISOLATION ("destructive document operations never touch include
files, the sidecar or `*.beancount`"), INV-25 (workspace operations never delete user files they do not own).
Interface: `documents.usecases` ("Document store, orphan scan (never ledger sources, M-2)", ARCHITECTURE.md §2.2).
Assumed: `orphan_scan(ws_root) -> list[str]` (paths relative to the documents root) and
`delete_orphans(ws_root, paths) -> result` with `.refused`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.integrity.planned import planned

APP = Path(__file__).resolve().parents[2] / "src" / "app"


@pytest.mark.fixme(id="L3-14", due="M4", reason="capability not built")
def test_the_unknown_account_comes_from_the_profile(tmp_path: Path) -> None:
    ledger_profile = planned("app.accounts.usecases", "ledger_profile")
    (tmp_path / ".finzytrack").mkdir()
    (tmp_path / ".finzytrack" / "settings.yaml").write_text(
        "accounts:\n  default_unknown_account: Expenses:Uncategorized\n"
    )
    (tmp_path / "main.beancount").write_text(
        'option "operating_currency" "INR"\n2026-01-01 open Assets:Bank INR\n2026-01-01 open Expenses:Uncategorized\n'
    )

    profile = ledger_profile(tmp_path)

    assert profile.unknown_account == "Expenses:Uncategorized"
    assert [p for p in APP.rglob("*.py") if "Expenses:Unknown" in p.read_text()] == []


@pytest.mark.fixme(id="L11.M-2", due="M5", reason="capability not built")
def test_document_cleanup_never_offers_ledger_include_or_sidecar_files(tmp_path: Path) -> None:
    orphan_scan = planned("app.documents.usecases", "orphan_scan")
    delete_orphans = planned("app.documents.usecases", "delete_orphans")
    (tmp_path / "main.beancount").write_text(
        'option "documents" "."\ninclude "accounts.beancount"\ninclude "prices.beancount"\n'
        '2026-01-02 document Assets:Bank "statements/jan.pdf"\n'
    )
    (tmp_path / "accounts.beancount").write_text("2026-01-01 open Assets:Bank\n")
    (tmp_path / "prices.beancount").write_text("2026-01-02 price AAPL 190.00 USD\n")
    (tmp_path / "statements").mkdir()
    (tmp_path / "statements" / "jan.pdf").write_bytes(b"%PDF-1.4 referenced\n")
    (tmp_path / "receipt.pdf").write_bytes(b"%PDF-1.4 orphan\n")

    assert sorted(orphan_scan(tmp_path)) == ["receipt.pdf"]
    result = delete_orphans(tmp_path, ["main.beancount", "accounts.beancount", "prices.beancount"])
    assert sorted(result.refused) == ["accounts.beancount", "main.beancount", "prices.beancount"]
    assert all((tmp_path / n).exists() for n in ("main.beancount", "accounts.beancount", "prices.beancount"))
