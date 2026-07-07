"""
API-level automation of dev-docs/multi-file-ledger-test.md.

The manual checklist drives the GUI; these tests drive the same operations
through the real HTTP routers (via the FastAPI TestClient in conftest.py) against
the shipped `fake-multi` fixture, and assert on the resulting on-disk state.
This closes the layer between the LedgerManager unit tests
(`test_multi_file_ledger.py`) and a browser: it catches API-wiring bugs the unit
tests can't see, while the manual checklist is left to cover only the genuinely
visual pieces (banner rendering, dashboard charts, browser-session dismissal).

Step numbers in the test names map to sections of the manual checklist.

"Only file X changed" is asserted by content byte-equality (snapshot before vs.
after) — deterministic, unlike mtimes — plus backup-dir globbing. The
`multi_file_client` fixture pre-normalizes the ledger (collapsing the
intentionally un-normalized pushtag block) so a mutation only rewrites the file
it actually changes; `multi_file_client_fresh` keeps the raw state for the
pushtag-transition checks.
"""

from __future__ import annotations

from pathlib import Path

from beancount import loader
from beancount.core.data import Transaction


# ───────────────────────── helpers ──────────────────────────────────────────

def _ld(root: Path) -> Path:
    return root / "data" / "ledgers"


def _main(root: Path) -> Path:
    return _ld(root) / "main.beancount"


def _snapshot(root: Path) -> dict:
    """Map of {relative-path-str: bytes} for every ledger file."""
    return {
        p.relative_to(_ld(root)).as_posix(): p.read_bytes()
        for p in _ld(root).rglob("*.beancount")
    }


def _changed(before: dict, after: dict) -> set:
    return {k for k in (set(before) | set(after)) if before.get(k) != after.get(k)}


def _files_with(root: Path, substring: str) -> list:
    return sorted(
        p.relative_to(_ld(root)).as_posix()
        for p in _ld(root).rglob("*.beancount")
        if substring in p.read_text()
    )


def _entries(root: Path) -> list:
    entries, _, _ = loader.load_file(str(_main(root)))
    return entries


def _txn_id(root: Path, narration: str) -> str:
    for e in _entries(root):
        if isinstance(e, Transaction) and narration in (e.narration or ""):
            return e.meta["id"]
    raise AssertionError(f"no transaction with narration ~ {narration!r}")


def _txn_ids_in_file(root: Path, filename: str) -> list:
    out = []
    for e in _entries(root):
        if isinstance(e, Transaction) and Path(e.meta.get("filename", "")).name == filename:
            out.append(e.meta["id"])
    return out


def _backups(root: Path, filename: str) -> list:
    # rglob: backups are namespaced by source path under data/backups/ in the
    # production build (BackupManager base_dir), so search recursively.
    bdir = root / "data" / "backups"
    return sorted(bdir.rglob(f"{filename}.*.backup")) if bdir.exists() else []


def _all_backups(root: Path) -> list:
    bdir = root / "data" / "backups"
    return sorted(bdir.rglob("*.backup")) if bdir.exists() else []


def _commit(client, *, date, payee, narration, postings, source_account="Assets:Bank:Checking"):
    """POST a single new transaction through the importer commit endpoint.
    `postings` is a list of (account, amount) pairs, in order (last = destination)."""
    return client.post("/api/import/commit", json={"transactions": [{
        "date": date, "flag": "*", "payee": payee, "narration": narration,
        "postings": [{"account": a, "amount": str(amt), "currency": "USD"} for a, amt in postings],
        "source_account": source_account,
    }]})


def _update_narration(client, root, narration, new_narration, *, extra_meta=None):
    """Edit an existing transaction (found by current narration), resending its
    postings unchanged and only changing the narration (+ optional meta)."""
    txn = next(e for e in _entries(root)
               if isinstance(e, Transaction) and narration in (e.narration or ""))
    payload = {
        "id": txn.meta["id"],
        "date": txn.date.isoformat(),
        "flag": txn.flag or "*",
        "payee": txn.payee or "",
        "narration": new_narration,
        "tags": sorted(txn.tags or []),
        "links": sorted(txn.links or []),
        "postings": [
            {"account": p.account,
             "amount": str(p.units.number), "currency": p.units.currency}
            for p in txn.postings
        ],
        "meta": extra_meta or {},
    }
    return client.put("/api/ledger/transactions", json={"transactions": [payload]})


def _query(client, sql: str) -> list:
    r = client.post("/api/ledger/query", json={"query": sql})
    assert r.status_code == 200, r.text
    return r.json()["data"]["rows"]


def _notice_codes(client) -> list:
    r = client.get("/api/notices")
    assert r.status_code == 200, r.text
    return [n["code"] for n in r.json()["data"]["notices"]]


# ───────────────────────── 1. Read ──────────────────────────────────────────

class TestRead:
    def test_step1_per_year_transaction_counts(self, multi_file_client, tmp_root_with_multi_file):
        c = multi_file_client
        total = _query(c, "SELECT COUNT(DISTINCT transaction_id) AS n FROM postings")[0]["n"]
        assert total == 23
        by_year = {2024: 8, 2025: 8, 2026: 7}
        for year, expected in by_year.items():
            n = _query(c, f"SELECT COUNT(DISTINCT transaction_id) AS n FROM postings WHERE year={year}")[0]["n"]
            assert n == expected, f"year {year}: expected {expected}, got {n}"


# ───────────────────────── 3-12. Write routing ──────────────────────────────

class TestWriteRouting:
    def test_step3_edit_2025_rewrites_only_its_file(self, multi_file_client, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        before = _snapshot(root)
        # "Coffee with Sam" is unique to the 2025 file.
        resp = _update_narration(multi_file_client, root, "Coffee with Sam", "Coffee with Sam (edited)")
        assert resp.status_code == 200, resp.text
        # The 2025 file changed; nothing else.
        assert _changed(before, _snapshot(root)) == {"transactions-2025.beancount"}
        assert len(_backups(root, "transactions-2025.beancount")) == 1
        assert len(_all_backups(root)) == 1

    def test_step4_new_unrouted_transaction_lands_in_root(self, multi_file_client, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        before = _snapshot(root)
        resp = _commit(multi_file_client, date="2026-04-01", payee="Test",
                       narration="Routed to root",
                       postings=[("Expenses:Food", 25), ("Assets:Bank:Checking", -25)])
        assert resp.status_code == 200, resp.text
        assert _files_with(root, "Routed to root") == ["main.beancount"]
        assert _changed(before, _snapshot(root)) == {"main.beancount"}

    def test_step5_new_account_lands_in_root(self, multi_file_client, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        before = _snapshot(root)
        resp = multi_file_client.post("/api/accounts", json={
            "name": "Expenses:Demo:NewCategory", "open_date": "2026-01-01", "currencies": ["USD"],
        })
        assert resp.status_code == 201, resp.text
        assert _files_with(root, "Expenses:Demo:NewCategory") == ["main.beancount"]
        assert _changed(before, _snapshot(root)) == {"main.beancount"}

    def test_step6_edit_nested_child_preserves_intermediate_includes(self, multi_file_client, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        before = _snapshot(root)
        resp = _update_narration(multi_file_client, root, "Pantry restock", "Pantry restock (edited)")
        assert resp.status_code == 200, resp.text
        # Only the jan child changed; the include hub and its sibling are intact.
        assert _changed(before, _snapshot(root)) == {"2026/jan.beancount"}
        index = (_ld(root) / "2026" / "index.beancount").read_text()
        assert 'include "jan.beancount"' in index
        assert 'include "feb.beancount"' in index

    def test_step7_delete_last_entries_leaves_empty_child_in_place(self, multi_file_client, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        feb_ids = _txn_ids_in_file(root, "feb.beancount")
        assert len(feb_ids) == 2
        resp = multi_file_client.request("DELETE", "/api/ledger/transactions",
                                         json={"transaction_ids": feb_ids})
        assert resp.status_code == 200, resp.text
        feb = _ld(root) / "2026" / "feb.beancount"
        assert feb.is_file()
        assert feb.read_text().strip() == ""
        # main still includes it
        assert 'include "2026/index.beancount"' in _main(root).read_text()
        assert 'include "feb.beancount"' in (_ld(root) / "2026" / "index.beancount").read_text()

    def test_step9_plugin_and_option_survive_root_rewrite(self, multi_file_client, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        # Any root write (create an account)
        multi_file_client.post("/api/accounts", json={
            "name": "Expenses:Demo:Z", "open_date": "2026-01-01", "currencies": ["USD"]})
        head = _main(root).read_text()
        assert 'option "title" "Demo Ledger (Multi-file)"' in head
        assert 'option "operating_currency" "USD"' in head
        assert 'plugin "beancount.plugins.implicit_prices"' in head
        for inc in ["accounts.beancount", "transactions-2024.beancount",
                    "transactions-2025.beancount", "2026/index.beancount",
                    "subscriptions-archive.beancount", "subscriptions.beancount",
                    "pushtag-demo.beancount"]:
            assert f'include "{inc}"' in head, f"missing include {inc}"

    def test_step11_delete_child_transaction_rewrites_only_that_child(self, multi_file_client, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        tid = _txn_id(root, "Coffee with Sam")  # unique to the 2025 file; non-cascading expense
        before = _snapshot(root)
        resp = multi_file_client.request("DELETE", "/api/ledger/transactions",
                                         json={"transaction_ids": [tid]})
        assert resp.status_code == 200, resp.text
        assert _changed(before, _snapshot(root)) == {"transactions-2025.beancount"}
        assert len(_all_backups(root)) == 1

    def test_step12_delete_root_transaction_touches_only_root(self, multi_file_client, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        # First create one in root, then delete it.
        _commit(multi_file_client, date="2026-04-02", payee="Tmp", narration="DeleteMeRoot",
                postings=[("Expenses:Food", 5), ("Assets:Bank:Checking", -5)])
        tid = _txn_id(root, "DeleteMeRoot")
        before = _snapshot(root)
        resp = multi_file_client.request("DELETE", "/api/ledger/transactions",
                                         json={"transaction_ids": [tid]})
        assert resp.status_code == 200, resp.text
        assert _changed(before, _snapshot(root)) == {"main.beancount"}
        assert "DeleteMeRoot" not in _main(root).read_text()


# ───────────────────────── 13-15. Accounts ──────────────────────────────────

class TestAccounts:
    def test_step14_close_account_whose_open_is_in_child_writes_close_to_root(self, multi_file_client, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        accounts_before = (_ld(root) / "accounts.beancount").read_bytes()
        resp = multi_file_client.post("/api/accounts/Expenses:Transport/close",
                                      json={"close_date": "2026-06-01"})
        assert resp.status_code == 200, resp.text
        # Close lands in root; the Open in accounts.beancount is untouched.
        assert "close Expenses:Transport" in _main(root).read_text()
        assert (_ld(root) / "accounts.beancount").read_bytes() == accounts_before

    def test_step15_reopen_removes_close_from_root_leaves_open_untouched(self, multi_file_client, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        multi_file_client.post("/api/accounts/Expenses:Transport/close", json={"close_date": "2026-06-01"})
        accounts_after_close = (_ld(root) / "accounts.beancount").read_bytes()
        resp = multi_file_client.post("/api/accounts/Expenses:Transport/reopen", json={})
        assert resp.status_code == 200, resp.text
        assert "close Expenses:Transport" not in _main(root).read_text()
        assert (_ld(root) / "accounts.beancount").read_bytes() == accounts_after_close

    def test_step13_delete_account_spanning_files_rewrites_those_files(self, multi_file_client, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        before = _snapshot(root)
        resp = multi_file_client.request(
            "DELETE", "/api/accounts/Assets:Bank:Checking",
            params={"delete_transactions": "true"})
        assert resp.status_code == 200, resp.text
        changed = _changed(before, _snapshot(root))
        # The Open lived in accounts.beancount; postings spanned several txn files.
        assert "accounts.beancount" in changed
        assert "transactions-2024.beancount" in changed
        assert "transactions-2025.beancount" in changed
        # The account and all postings against it are gone from disk.
        assert "open Assets:Bank:Checking" not in (_ld(root) / "accounts.beancount").read_text()
        assert _files_with(root, "Assets:Bank:Checking") == []
        # Deleting the account removes the "To savings" transfer that the
        # 2025 Savings balance (1500) depended on, so that assertion now
        # legitimately fails — the manual checklist calls this out as the
        # expected parse-error banner, not a bug. Verify it surfaces.
        assert "LEDGER_PARSE_ERROR" in _notice_codes(multi_file_client)


# ───────────────────────── 16-17, 19. Balances & pad ────────────────────────

class TestBalances:
    def test_step16_balance_with_pad_for_child_account_lands_in_root(self, multi_file_client, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        accounts_before = (_ld(root) / "accounts.beancount").read_bytes()
        resp = multi_file_client.post(
            "/api/accounts/Assets:Bank:Checking/balance-directives",
            json={"date": "2026-07-01", "currency": "USD", "amount": "1234.56",
                  "include_pad": True, "pad_source_account": "Equity:Opening-Balances"})
        assert resp.status_code == 201, resp.text
        main_text = _main(root).read_text()
        assert "balance Assets:Bank:Checking" in main_text
        assert "pad Assets:Bank:Checking" in main_text
        assert (_ld(root) / "accounts.beancount").read_bytes() == accounts_before
        # Re-parse must be clean (pad fills to the new balance).
        assert "LEDGER_PARSE_ERROR" not in _notice_codes(multi_file_client)

    def test_step17_edit_existing_balance_keeps_pad_in_same_child_file(self, multi_file_client, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        # The fixture has a Pad+Balance for Assets:Bank:Savings in transactions-2024.beancount.
        before = _snapshot(root)
        resp = multi_file_client.put(
            "/api/accounts/Assets:Bank:Savings/balance-directives",
            json={"original_date": "2024-12-31", "original_currency": "USD",
                  "original_amount": "1000.00", "new_amount": "1100.00",
                  "include_pad": True, "pad_source_account": "Equity:Opening-Balances"})
        assert resp.status_code == 200, resp.text
        # The point of this step: the replaced Pad stays in the file the old
        # Pad came from, and only that file is rewritten.
        assert _changed(before, _snapshot(root)) == {"transactions-2024.beancount"}
        txt = (_ld(root) / "transactions-2024.beancount").read_text()
        assert "1100.00 USD" in txt          # balance reflects the edit
        assert "pad Assets:Bank:Savings" in txt   # pad preserved, in the same file
        # (Editing this balance to 1100 makes the linked 2025 Savings balance of
        # 1500 fail downstream — 1100 + 500 transfer = 1600 — which is correct
        # Beancount behavior for this fixture, so we don't assert a clean parse
        # here. Clean-parse-after-pad is covered by step 16's additive case.)

    def test_step19_cross_file_pad_balance_parses_clean(self, multi_file_client, tmp_root_with_multi_file):
        # Fixture pad+balance (Savings, in transactions-2024) + a new root one (step 16).
        root = tmp_root_with_multi_file
        multi_file_client.post(
            "/api/accounts/Assets:Bank:Checking/balance-directives",
            json={"date": "2026-07-01", "currency": "USD", "amount": "1234.56",
                  "include_pad": True, "pad_source_account": "Equity:Opening-Balances"})
        assert "LEDGER_PARSE_ERROR" not in _notice_codes(multi_file_client)


# ───────────────────────── 2, 8, 13. Notices ────────────────────────────────

class TestNotices:
    def test_step2_pushtag_advisory_present_on_load(self, multi_file_client_fresh):
        assert "MULTIFILE_PUSHTAG_PUSHMETA" in _notice_codes(multi_file_client_fresh)

    def test_step8_editing_travel_txn_collapses_block_and_clears_advisory(self, multi_file_client_fresh, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        c = multi_file_client_fresh
        assert "MULTIFILE_PUSHTAG_PUSHMETA" in _notice_codes(c)
        resp = _update_narration(c, root, "Round-trip to demo destination", "Edited travel txn")
        assert resp.status_code == 200, resp.text
        pd = (_ld(root) / "pushtag-demo.beancount").read_text()
        assert "pushtag" not in pd and "poptag" not in pd
        assert "#travel" in pd  # tag preserved inline
        assert "MULTIFILE_PUSHTAG_PUSHMETA" not in _notice_codes(c)

    def test_step13_parse_error_surfaces_as_notice(self, multi_file_client_fresh, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        # Inject a parse error into a child file and re-export.
        bad = _ld(root) / "transactions-2025.beancount"
        bad.write_text(bad.read_text() + "\nNOT A VALID DIRECTIVE\n")
        multi_file_client_fresh.post("/api/ledger/export", json={"force": True})
        assert "LEDGER_PARSE_ERROR" in _notice_codes(multi_file_client_fresh)


# ───────────────────────── 10. Ledger switch ────────────────────────────────

class TestLedgerSwitch:
    def test_step10_switch_to_single_file_clears_pushtag_advisory(self, multi_file_client_fresh, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        c = multi_file_client_fresh
        assert "MULTIFILE_PUSHTAG_PUSHMETA" in _notice_codes(c)
        single = _ld(root) / "single.beancount"
        single.write_text(
            'option "operating_currency" "USD"\n\n'
            '2024-01-01 open Assets:Cash USD\n\n'
            '2024-01-01 commodity USD\n\n'
            '2024-02-01 * "Shop" "Thing"\n'
            '  Expenses:Misc 5.00 USD\n'
            '  Assets:Cash  -5.00 USD\n'
        )
        resp = c.patch("/api/config", json={"ledger_file": str(single)})
        assert resp.status_code == 200, resp.text
        # Reads now reflect the single-file ledger and the advisory is gone.
        assert "MULTIFILE_PUSHTAG_PUSHMETA" not in _notice_codes(c)


# ───────────────────────── 18, 20-24. Fava routing ──────────────────────────

class TestFavaRoutingViaApi:
    def test_step20_new_subscription_routes_to_matching_file(self, multi_file_client, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        before = _snapshot(root)
        resp = _commit(multi_file_client, date="2026-02-01", payee="Netflix", narration="Sub 2026",
                       postings=[("Assets:Bank:Checking", -15), ("Expenses:Subscriptions", 15)])
        assert resp.status_code == 200, resp.text
        assert _files_with(root, "Sub 2026") == ["subscriptions.beancount"]
        assert _changed(before, _snapshot(root)) == {"subscriptions.beancount"}
        # Routing directive survives the routed write.
        assert 'insert-entry' in (_ld(root) / "subscriptions.beancount").read_text()

    def test_step21_earlier_dated_subscription_routes_to_older_rule_file(self, multi_file_client, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        resp = _commit(multi_file_client, date="2025-01-15", payee="Netflix", narration="Sub 2025",
                       postings=[("Assets:Bank:Checking", -15), ("Expenses:Subscriptions", 15)])
        assert resp.status_code == 200, resp.text
        assert _files_with(root, "Sub 2025") == ["subscriptions-archive.beancount"]

    def test_step22_non_matching_new_txn_falls_back_to_root(self, multi_file_client, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        resp = _commit(multi_file_client, date="2026-04-05", payee="Test", narration="Unrouted",
                       postings=[("Assets:Bank:Checking", -20), ("Expenses:Food", 20)])
        assert resp.status_code == 200, resp.text
        assert _files_with(root, "Unrouted") == ["main.beancount"]

    def test_step23_editing_routed_entry_does_not_reroute(self, multi_file_client, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        _commit(multi_file_client, date="2026-02-01", payee="Netflix", narration="Sub Edit Me",
                postings=[("Assets:Bank:Checking", -15), ("Expenses:Subscriptions", 15)])
        assert _files_with(root, "Sub Edit Me") == ["subscriptions.beancount"]
        before = _snapshot(root)
        resp = _update_narration(multi_file_client, root, "Sub Edit Me", "Sub Edited")
        assert resp.status_code == 200, resp.text
        assert _files_with(root, "Sub Edited") == ["subscriptions.beancount"]
        assert _changed(before, _snapshot(root)) == {"subscriptions.beancount"}

    def test_step18_source_account_change_does_not_reroute(self, multi_file_client, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        before = _snapshot(root)
        # "Airport trip" is unique to the 2025 file.
        resp = _update_narration(multi_file_client, root, "Airport trip", "Airport trip v2",
                                 extra_meta={"source_account": "Income:Salary"})
        assert resp.status_code == 200, resp.text
        # Edit stays in the 2025 file regardless of source_account.
        assert _files_with(root, "Airport trip v2") == ["transactions-2025.beancount"]
        assert _changed(before, _snapshot(root)) == {"transactions-2025.beancount"}

    def test_step24_default_file_overrides_root_catch_all(self, multi_file_client, tmp_root_with_multi_file):
        root = tmp_root_with_multi_file
        # Add a default-file directive (empty arg → its own file) out-of-band,
        # exactly as a user editing ledger text would. The next write re-parses
        # and honors it.
        archive = _ld(root) / "subscriptions-archive.beancount"
        archive.write_text(archive.read_text() + '2024-01-01 custom "fava-option" "default-file"\n')
        resp = _commit(multi_file_client, date="2026-04-06", payee="Test", narration="Unrouted Default",
                       postings=[("Assets:Bank:Checking", -7), ("Expenses:Food", 7)])
        assert resp.status_code == 200, resp.text
        assert _files_with(root, "Unrouted Default") == ["subscriptions-archive.beancount"]
        assert "Unrouted Default" not in _main(root).read_text()
