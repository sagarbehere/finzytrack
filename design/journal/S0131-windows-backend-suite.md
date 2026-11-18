# S0131: the backend suite on Windows (M1b, BL-0290)

## Brief
- Goal: the whole backend suite runs on the Windows runner (`backend-windows` in `ci.yml`), each test passing or skipping with its reason, as S0127 did for the engine's suite. Today the job runs only S0129's tests; after this slice it runs `pytest` over all of `tests/`. Windows still refuses every file operation in the product until BL-0087: a test that needs the engine's write path skips there naming BL-0087, and each other skip names its reason from `tests/windows_pending.py`.
- **The `tzdata` item (BL-0290 kind 3): a fact checked at the brief changes it.** BL-0290 calls it "a product gap: the sidecar's civil dates need the `tzdata` package there". The code says otherwise: no module under `backend/src` imports `zoneinfo`. `app.domain.dates.civil_date` without a zone calls `datetime.astimezone(None)`, which reads the machine's zone through the C runtime (`localtime`), not through the IANA database; the system clock returns UTC instants. Only `tests/domain/test_dates.py` names zones (`ZoneInfo("America/Los_Angeles")`, `"Asia/Kolkata"`), and on Windows `zoneinfo` finds those only in the `tzdata` package. So `tzdata` is a test dependency today: it joins the `dev` group with the marker `sys_platform == 'win32'`, at ARCHITECTURE.md §10's pin (2026.5), and becomes a runtime dependency with the first product code that names a zone (ADR-0023's `today(tz, clock)` with the user's chosen zone; `pyproject.toml`'s rule: "runtime dependencies arrive with the slice that first uses each one"). A dev dependency is not bundled, so the licence check does not judge it; its licence (Apache-2.0, pending legal advice for the sidecar, ADR-0054) is the question of the slice that bundles it. ARCHITECTURE.md §10's row says so.
- Kinds of failure expected (BL-0290's inventory from the 2026-11-18 run 2, before S0129 landed; the first run of this slice re-takes it at the base):
  1. tests written in POSIX paths (`/L/...`, `/app-data/...`) that the sidecar's codec refuses on Windows since S0127: written on the host's path model instead;
  2. the engine's write path, refused on Windows until BL-0087: skip naming BL-0087;
  3. `tzdata` (above);
  4. the mirror's files: `test_files.py`'s rename over an existing name (`FileExistsError`), a URI path in `test_query.py`, `resource` in the query test;
  5. `test_main.py`'s sidecar-process tests (CRLF in stderr lines, the stand-in engines), the demo script's case rule, the TLA mapping scan's separators.
- **Product code:** none expected. If a failure shows a product defect on Windows (the sidecar doing the wrong thing there, not a test's assumption), it is fixed in this slice only when it is small and outside the write path; otherwise it becomes a backlog row and its tests skip naming it. A product fix here takes the review row "Other product code", with a fresh round after the fix.
- Implements: BL-0290; ADR-0061 and its amendment of 2026-11-14 (the Windows port); engine-jobs §15's test rule (each skip names its reason).
- Capabilities: none changes status.
- Invariants touched: INV-07 (the civil-date tests run on Windows, under `TZ=PST8PDT`), INV-24 (tests on scratch folders only), INV-23 (no runner path or run ID in the repo).
- Risk class / lane: Low / light: tests, CI and a test-only dependency. **Review budget row: "Tooling, gates, measurement"** (one verifier round; the fix round fixes only the stop rule's (1)-(3)). Why not "Other product code": the slice changes no product behaviour (above), and the dependency is a test dependency, not a runtime one. Should a product fix be needed, that part takes "Other product code" (above).
- The bytes it protects: none (no write path changes).
- Mechanism questions (PROCESS.md §3 step 2): no mechanism is added or hardened: each change makes a test run on Windows' path model or names why it cannot run there.
- What the check is a second opinion on (S0122's lesson): the Windows job is a second opinion on the sidecar's OS-dependent code (paths, the codec, the mirror's files and URIs, process I/O) that the macOS and Linux jobs exercise only in POSIX form. A test that skips on Windows is no opinion at all, so every skip names a reason that says which other test or row covers the same rule.
- Acceptance:
  - `backend-windows` runs the whole suite (`pytest -q -rs tests`) and is green; every skip there prints a reason from `tests/windows_pending.py` that names BL-0087, a POSIX-only rule with its Windows counterpart, or another open row;
  - `test_dates.py` runs on Windows with `tzdata` (it fails on the base with `ModuleNotFoundError: tzdata`);
  - the tests rewritten from POSIX paths pass on macOS and Linux unchanged in what they assert (no expected output changes except a path's spelling);
  - `pyright_platforms.py` 0 errors on Darwin, Linux and Windows; the macOS gates green; every other CI job as before (`engine-windows` red on exactly INV-03's five, BL-0282: "keep red").
- Expected doc impact: `ci.yml` (the job's comment and command); `backend/CLAUDE.md` (the Windows note: the whole suite runs there); `tests/windows_pending.py` (its reasons); ARCHITECTURE.md §10 (tzdata's row: a test dependency until first product use); BL-0290 closed; new rows for any product gap found.
- Open questions: none blocking. The `tzdata` reading above departs from BL-0290's wording and from the session's framing ("one new runtime dependency"); it is reported to the owner with the slice.
- Base: 668e64f9.

## Decisions
- **`tzdata` as a test dependency on Windows, not a runtime one (agent; facts above).** The product reads no IANA zone; a runtime dependency with nothing using it would fail `deptry`'s unused-dependency check and break `pyproject.toml`'s rule.
