# ADR-0007: Tauri 2 desktop shell with a Python sidecar

This record decides the desktop shell, its signing and update model, the permanent app identifier and the supported platforms.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** desktop, release

## Context

v0.2/v0.3 ship as PyInstaller + pywebview. There is no auto-updater, installers are hand-rolled, and macOS builds are ad-hoc signed. Tauri 2 offers a built-in signed updater and installers for all three OSes. Both shells use the same system webviews, so the frontend does not change. The owner has no Apple Developer membership yet.

## Decision

(owner, 2026-10-24)
- **Shell:** Tauri 2 (2.12.x). The Python backend runs as a **PyInstaller onedir sidecar**, loopback only.
- **The shell is thin.** It supplies paths, native dialogs, process control and updates; it holds no business logic. The web UI talks to the shell only through one `ShellPort`. No `fs` plugin and no `shell:allow-execute` for the webview. A strict CSP is set (ADR-0040).
- **Sidecar lifecycle:** spawn with `--port 0`, a one-line `FT_READY` handshake, wait for `GET /api/boot`, graceful `quiesce` on exit, orphan kill (process group / Job Object), restart with backoff and a "backend stopped" screen after three crashes.
- **Identifier:** `com.finzytrack.desktop` (permanent; it fixes the data dirs and how updates find the app). Product name "Finzytrack".
- **Platforms for v0.4.0:** Apple-silicon macOS (ad-hoc signed, **not notarized**), Windows 10/11 x64 (unsigned; the SmartScreen first-run warning is documented), Linux x64 (Ubuntu 22.04 or newer; the updater updates the AppImage only). **Intel Macs are dropped.**
- **Updates:** the Tauri updater with the project's own signing key pair (generated once, two offline backups; the key and its password are the only release secrets in CI). The signed manifest is on GitHub Releases. **Stable channel only** in v0.4.0 (beta deferred, ADR-0043). An update never runs migrations silently; rollback = install the previous version + "restore pre-upgrade backup". Install never proceeds while a write journal is open (ADR-0012).
- **Headless mode** (`--headless --host … --port N`) keeps browser and phone access, with the LAN guard (ADR-0040).
- Spike S1 verifies that on an ad-hoc-signed build the updater replaces the app without a new Gatekeeper prompt. If it does not, the behaviour is documented.

## Alternatives considered and rejected

- **Keep pywebview.** Rejected: no updater, and installers and signing would be hand-built.
- **Notarized macOS builds for v0.4.0.** Rejected: no Apple Developer ID yet. Notarization and Windows code signing become later release-pipeline tasks, not release blockers.
- **The legacy identifier `com.finzytrack.app`.** Rejected: the Tauri bundle must differ from the pywebview build, and Tauri advises against an identifier ending in `.app`.
- **`app.finzytrack`.** Rejected in favour of the `finzytrack.com` form.
- **Tauri 3** (alpha) and **PyInstaller onefile** (slower start). Rejected.

## Consequences

- Rust/Cargo joins CI on all three OSes; a packaged-build matrix runs nightly.
- The packaged macOS UI cannot be driven by `tauri-driver`; macOS relies on `--smoke-test`, sidecar and API checks, Playwright WebKit against the sidecar-served bundle, and dogfooding.
- Users see a Gatekeeper first-run warning on macOS; the install docs explain it.

## References

review:22 §2; review:25 §5, §9.8; review:F §2 item 8, steering 2; ADR-0012, ADR-0040, ADR-0043.
