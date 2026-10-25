# S0012: Packaged three-OS builds with `--smoke-test`, and the nightly workflow (BL-0037)

## Brief
- Goal: build the empty Tauri window as a real package on all three platforms (Apple-silicon macOS `.app`, ad-hoc signed; Windows x64 NSIS installer, unsigned; Linux x64 AppImage and `.deb`) with the python-build-standalone runtime and the main sidecar's package inside, and prove each package works with `--smoke-test`: the window's page finishes loading, and the bundled interpreter runs isolated from inside the package and imports the sidecar package from there. CI: `packaged.yml` (three-OS build and smoke) and `nightly.yml` (T2, calls it).
- Implements: ADR-0056 (PBS, resources not `externalBin`, inside-out ad-hoc signing, never extracted at runtime, AppImage + `.deb`), ADR-0007 (+ amendment: platforms, macOS 14, "a packaged-build matrix runs nightly"), ADR-0054 (the main sidecar package; the engine process arrives with BL-0041), EP-24 (pins: the PBS release is 2026-10-09, 16 days old; `@tauri-apps/cli` 2.12.2 per ARCHITECTURE.md §10), review:27 §1(d) ("the empty Tauri window passes `--smoke-test` on all three platforms"); BL-0037 (last part).
- Capabilities: none (CAP-DESK-INSTALLERS is M11; this is M0 infrastructure).
- Invariants touched: INV-23 (artifacts carry no data; placeholder icons generated from the existing placeholder); INV-24 (the smoke test reads nothing outside the package; CI only).
- Risk class / lane: Medium / full. Tooling that touches no ledger or user data, so one verifier round unless a High is found (ADR-0058). Supply-chain aspects (a downloaded runtime, archive extraction, signing) are attacked as such.
- Spec of `--smoke-test` (none existed; defined here, in `shell/src-tauri/src/smoke.rs`'s module doc): exit 0 and print exactly one line `FT_SMOKE_OK {json}` only when (1) the `main` window's page load reached *Finished*, and (2) the interpreter at the package's own resource path `sidecar/python/…` (never one from `PATH`), run with `-I`, reports Python 3.13, a `sys.prefix` inside the package's resource directory, and the sidecar package `app` imported from inside that directory. Anything else, including a timeout (120 s), prints `FT_SMOKE_FAIL {reason}` and exits non-zero.
- Acceptance tests: Rust unit tests for the probe verdict (`cargo test`, new in the shell job); `scripts/tests/test_build_sidecar.py` (pinned hash enforced, tampered archive and tampered cache refused, unknown target refused, unsafe archive members refused, no symlinks left); `scripts/tests/test_smoke_packaged.py` (the harness passes only on exit 0 with the OK line); `scripts/tests/test_shell_config.py` extended (the packaged override may touch only `bundle`, the merged config still holds, no other `tauri*.conf*` file); the CI run of `packaged.yml` on three OSes, with one must-not-pass run (Linux `.deb` with the runtime moved away must fail the smoke test).
- Expected doc impact: ARCHITECTURE.md §5 (packaging paragraph), §10 (PyInstaller row replaced by the PBS pin), §11 (layout); ENGINEERING.md §0 if it lists gates; PROCESS.md §6 ("Built today"); backlog BL-0037 done, new items for what is left; roadmap §1; THIRD_PARTY_NOTICES (CPython); this journal. No user docs.
- Open questions: `design/inbox/20261025-nightly-schedule-needs-main.md` (blocks nothing in this slice).
- Base SHA: 6c3cec4.

## Decisions
- `--smoke-test` had no written spec; it is defined in `smoke.rs`'s module doc (above in the Brief) and checks what ADR-0056 depends on: the package's own interpreter runs from inside the package, isolated, and finds the sidecar package there (agent).
- The Tauri CLI is a frontend devDependency (`@tauri-apps/cli` 2.12.2, ARCHITECTURE.md §10), so pnpm's lockfile and `minimumReleaseAge` cover it; it ships nothing (agent).
- Windows ships the NSIS installer in per-user mode (no administrator prompt); no MSI. macOS builds the `.app` only; the dmg and updater artifacts belong to M11's `release.yml` (agent).
- The packaged override (`tauri.packaged.conf.json`) may set only `bundle`; no platform config file may exist, since Tauri would merge it silently (EP-30: refuse the class) (agent).
- Every symlink in the runtime tree is removed rather than judged one by one (EP-30); the smoke test on each OS proves the runtime still starts without them (agent).
- The Linux build runs on Ubuntu 22.04 so the package's glibc baseline is the oldest supported Ubuntu (ARCHITECTURE.md §5) (agent).
- Placeholder `icon.icns` and `icon.ico` are generated from the existing placeholder `icon.png` with `tauri icon` (Windows builds need the `.ico`) (agent).
- GitHub runs schedules only from `main`; the schedule question went to the owner (inbox, blocks nothing). Meanwhile `packaged.yml` also runs on pushes that touch packaging (agent).
- CPython is listed in `THIRD_PARTY_NOTICES` (ADR-0056 consequence); the libraries PBS links into it are BL-0053 (M11, before anything is distributed) (agent).
- Running the smoke test on a developer Mac creates WebKit cache folders for `com.finzytrack.desktop` under the user's Library; v0.3 is not a Tauri app, so nothing of the owner's is touched (INV-24) (agent).

## Hand-off (PROCESS.md §3 step 4; ADR-0057)
- Checks with must-not-pass tests: archive hash (download and cache) → `Fetch`; unsafe archive members (`..`, absolute, links out, hard link out, device, fifo) → `Extract`; unpinned target → `Lock`; no symlinks left (incl. dangling, directory links) → `Tree`; Mach-O detection by content, inside-out order → `Tree`; smoke harness verdict (7 normal and 5 expected-refusal cases) → `test_smoke_packaged.py`; the probe verdict (13 cases: exit, line count, JSON, version, prefix/app outside, link out, missing path, sibling prefix) and the page-and-probe verdict → `smoke.rs` tests; the packaged override (10 planted variants, RFC 7396 merge, extra config files) → `test_shell_config.py`; end to end on macOS: runtime removed → `FT_SMOKE_FAIL`, sidecar package removed → `FT_SMOKE_FAIL` (CI repeats the first on every OS).
- Self-mutation: Python 16 tried, 15 killed; the survivor (merge ignoring `null`) got a direct RFC 7396 test and is now killed. Rust 8 tried, 8 killed.
- Not covered by a unit test: the 120 s timeout path in the shell (exercised only by a hang); the harness's own timeout is tested.

## Verifier
- Round 1, head 95850b0: PASS WITH FINDINGS. Attacks: 13 (tests at head and on base; clean rebuild, signatures, 0 symlinks; positive and five negative smoke variants incl. a shim to Homebrew Python with a fake `app` on PYTHONPATH; PBS hashes for all three targets re-downloaded; extraction filter; real-archive symlinks and trim; smoke.rs concurrency and exit paths; RFC 7396 merge against Tauri's; workflows read; licences and generated files).
- F1 Low: `trim()` named Tcl/Tk folders by versions the release does not carry, so about 4 MB of Tcl/Tk shipped. Fixed: removed by pattern, with a test on the real layout; BL-0053 wording corrected.
- F2 Low: `--expect-fail` accepted any refusal, including a hang. Fixed: it takes the expected reason; CI passes "cannot start the bundled interpreter"; two new negatives.
- F3 Low: nothing checked that the probe ran isolated. Fixed: the probe reports `sys.flags.isolated` and the verdict requires 1; two new negatives.
- F4 Low: PBS binaries are already linker-signed, so skipping our signing passed every check. Fixed: the build refuses a Mach-O that still carries only the linker's signature.
- F5 Low: frontend sources were missing from the push paths. Fixed: `frontend/**`.
- F6 Low: the AppImage ran extracted, not mounted as users run it. Fixed: run FUSE-mounted (libfuse2 installed).
- All Low; no further round (ADR-0058).

## Retro
- What the verifier found that I did not: two checks proved less than their names (a refusal for any reason; a signature anyone could have made). The cheaper gate is the question "what else would produce this same output?" asked of each positive signal, not only of each check.
- Trim was written from the spike's file list, not from the pinned archive; a test on the real layout costs a minute.
- Process cost: about 1 h 30 min building (incl. local macOS package and smoke); verifier about 6 min wall; fixes about 20 min. CI on Windows and Linux is the first real run of those jobs; results are recorded below once it finishes.

## First CI run (c640611) and fixes
- Linux: green (AppImage mounted and `.deb` installed; the run with the runtime moved away refused as expected).
- macOS: failed before smoke: `grep '\.app$'` also matched the bundler's "Signing …Finzytrack.app" log line, because the artifact list was parsed from console output. Fixed: `package_app.py --list FILE` writes the artifact paths to a file.
- Windows: the smoke step hung for 12 minutes, past the harness's 180 s limit, so it was cancelled. Diagnosis: `subprocess.run` with pipes waits for end-of-file, and WebView2's helper processes inherit the app's handles and outlive it. Fixed: output goes to temporary files, the harness waits on the app process only, a timeout stops the whole process tree (`taskkill /T`); the installer runs under `timeout 300`; each smoke step has a 12-minute limit.
- ci.yml: knip flagged `@tauri-apps/cli` as an unused devDependency (it is run by path from `package_app.py`); now in `ignoreDependencies`. Escape: the pre-push hook did not run knip, so this reached CI; it does now (PROCESS.md §9).
- Second CI run (8ba75f0): macOS and Linux green (signature, smoke, must-not-pass). Windows: the silent install timed out (exit 124). Diagnosis: Git Bash's path conversion rewrote the NSIS flag `/S` into a drive path, so the installer opened its wizard and waited. Fixed with `MSYS_NO_PATHCONV=1`.
