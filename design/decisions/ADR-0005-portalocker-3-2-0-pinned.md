# ADR-0005: portalocker 3.2.0 is pinned on purpose

This record pins the file-lock library to the version the legacy apps use, and says why it must not drift.

- **Status:** Accepted. Amended by ADR-0062 (2026-10-27): no legacy-lock waits; the upgrade gate is the detection test.
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

## Amendment (2026-10-27): detection only, no waits (ADR-0062)

Owner: "Drop the coexistence policy; I approve the needed changes to INV-26 and ADR-0020. The rest of the pending work you have identified should be done." (2026-10-27, in session). ADR-0062 removes Share workspaces, so v0.4 never writes a ledger together with a legacy app.
- **Still decided:** portalocker stays pinned at **3.2.0**. v0.4 uses it only to *detect* a held legacy lock: one non-blocking try (`LOCK_EX | LOCK_NB`) before Copy reads a legacy ledger set and before a save to a workspace a legacy config names; held means refuse with "Quit the old Finzytrack first", free means hold it for that read or save only.
- **Superseded:** "Legacy-lock waits use a timeout of at least 30 s with a visible status": v0.4 never waits for a legacy lock. "v0.4's backend takes every recorded legacy lock with it, then its own per-file locks": only the save and the copy try it, as above; verifies, R4 and recovery do not.
- **Superseded:** the upgrade gate's "lock-interleaving test against the packaged v0.2.2 build on all three OSes". Upgrading needs a superseding ADR and INV-26's detection test (a lock held from another process with portalocker 3.2.0, the way the legacy app holds it, is reported held and a free one free, on the three OSes).
- **Fact correction:** in portalocker 3.2.0 an exclusive lock on Windows is `msvcrt.locking` over the first 64 KiB of the file (its default `MsvcrtLocker`), not `LockFileEx`, which serves only its shared locks. POSIX is `fcntl.flock`, as stated. The pin does not change; the rejected alternative "`msvcrt.locking`" is wrong as worded, which is one more reason to detect through the pinned library rather than a reimplementation (ADR-0062).
- **Fact on versions:** v0.2.1, v0.2.2 and v0.3.0 pin `portalocker==3.2.0`; v0.2.0 declares `portalocker>=3.0.0` with no pin, so its shipped version is not known from its tag.

## References

review:23 §4.6; review:25 §2.3 (`Locks` port), §10, §14 item 3; ADR-0020, ADR-0050, ADR-0062; INV-26.
