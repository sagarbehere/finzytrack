# ADR-0056: Sidecar packaging uses python-build-standalone; Linux ships AppImage and .deb

This record replaces PyInstaller with python-build-standalone (PBS) for the Python processes and fixes the Linux package set, from spike S1.

- **Status:** Accepted
- **Date:** 2026-10-25
- **Deciders:** owner: "I accept the recommendations for 21 and 22." (2026-10-25, in session)
- **Area:** packaging, desktop
- **Supersedes:** the PyInstaller onedir choice in ADR-0007, ARCHITECTURE.md §5 and the §10 PyInstaller pin

## Context

Spike S1 built the Tauri 2 shell with both sidecar variants on Apple-silicon macOS. Both meet the targets (cold start ≈ 2 s against ≤ 10 s; warm ≈ 1.1 s against ≤ 3 s; update ≈ 91 MB). PBS is a normal relocatable CPython with the locked wheels installed by uv, so "frozen equals unfrozen" holds with no extra work (S8: 5/5 corpus hashes). PyInstaller needed two fixes to match (every stdlib module as a hidden import so user plugins can import them; a custom argv dispatcher for scikit-learn's worker processes). PBS is about 50 MB larger on disk. S1 also found that `externalBin` cannot carry a onedir tree, and that tauri-plugin-updater 2.13.3 updates AppImage, `.deb` and `.rpm` on Linux (the last two behind a root prompt), not AppImage only.

## Decision

- **The Python processes (main sidecar and ledger engine, ADR-0054) ship on python-build-standalone**: one relocatable CPython build in the bundle's resources, with each process's locked dependencies installed by uv into its own site-packages. No PyInstaller.
- The tree ships as `bundle.resources` and is spawned with the shell's own process API (not `externalBin`); every Mach-O is ad-hoc signed inside-out before bundling; the runtime is never extracted to a temp or data dir.
- **Linux:** ship **AppImage and `.deb`**. `.deb` updates ask for the password (documented); `.rpm` is not built for v0.4.0.

## Alternatives considered and rejected

- **PyInstaller 6.22.3 onedir** (the earlier plan). Works after two fixes, but each fix is a frozen-vs-unfrozen difference to maintain, and plugin users could hit missing stdlib modules.
- **PyInstaller onefile.** Rejected: it extracts at runtime, and S1 measured 17–49 s first runs for binaries outside the signed app.
- **AppImage only.** Rejected: `.deb` is the expected install on Ubuntu and the updater supports it.

## Consequences

- ARCHITECTURE.md §5 (sidecar supervisor), §10 (drop the PyInstaller pin; add the PBS release pin) and §11 (`shell/sidecar/` holds the PBS build script) follow this record; the S1 build script is the starting point.
- CAP-DESK-INSTALLERS and CAP-DESK-AUTOUPDATE text: Linux = AppImage + `.deb`; the updater handles both.
- The licence notices include CPython's licence (PSF) for the bundled interpreter.

## References

ADR-0007, ADR-0054; review:spikes/s01-tauri-sidecar; CAP-DESK-INSTALLERS, CAP-DESK-AUTOUPDATE.
