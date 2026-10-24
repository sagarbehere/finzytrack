# ADR-0005: portalocker 3.2.0 is pinned on purpose

This record pins the file-lock library to the version the legacy apps use, and says why it must not drift.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** agent (proposal), owner (accepted)
- **Area:** stack, data safety

## Context

A Share workspace (ADR-0020) lets v0.4 and a legacy app (v0.2.x/v0.3) write the same ledger. The legacy apps serialize their writes with `portalocker.lock(fp, LOCK_EX)` on `<legacy root>/data/.write.lock`, using portalocker 3.2.0: `fcntl.flock` on POSIX and `LockFileEx` on Windows over a fixed byte range. A lock only excludes another holder when both use the same primitive and range. portalocker 4.4.0 exists.

## Decision

- Pin **portalocker 3.2.0**. v0.4's backend takes every recorded legacy lock with it, then its own per-file locks under `app_local_data_dir/locks/`, in canonical order.
- Legacy-lock waits use a timeout of at least 30 s with a visible status, because the legacy app holds the lock through its whole re-export.
- Upgrading portalocker needs a superseding ADR **and** the lock-interleaving test against the packaged v0.2.2 build on all three OSes (INV-26).

## Alternatives considered and rejected

- **portalocker 4.x.** Rejected for now: its lock semantics against a 3.2.0 holder are untested, and a silent change would let both apps write at once.
- **A Rust/Tauri-side lock, `fcntl`/`lockf`, or `msvcrt.locking`.** Rejected: none of them excludes a `flock` or `LockFileEx` holder.

## Consequences

- The dependency report flags portalocker as outdated; that is expected and is not a reason to upgrade.
- The lock primitive is part of the TLA+ model's assumptions (ADR-0012, ADR-0050).

## References

review:23 §4.6; review:25 §2.3 (`Locks` port), §10, §14 item 3; ADR-0020, ADR-0050; INV-26.
