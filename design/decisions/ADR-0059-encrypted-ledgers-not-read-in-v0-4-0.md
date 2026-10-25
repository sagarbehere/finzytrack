# ADR-0059: Encrypted ledger files are not read in v0.4.0

This record decides that v0.4.0 refuses ledgers with GnuPG-encrypted files instead of reading them, as a stated exception to INV-03, and defers encryption support to a later feature.

- **Status:** Accepted
- **Date:** 2026-10-25
- **Deciders:** owner: "I think that for v0.4.0 Finzytrack should not even read encrypted ledgers. Supporting encryption can be done as its own small-ish(?) feature later." (2026-10-25, in session; `design/inbox/answered/20261025-encrypted-ledgers.md`)
- **Area:** ledger loading, scope
- **Amends:** INV-03 (a stated exception); ADR-0010 (its item 4 refused only writes to encrypted files)

## Context

Beancount 3.2.0 reads a `.gpg` file, or an `.asc` file holding a PGP message, by running `gpg --batch --decrypt` and parsing the output in memory (`beancount/utils/encryption.py`); the passphrase comes from the user's gpg-agent, which may show its own pinentry dialog or make the load fail. The capability map marked CAP-DATA-ENCRYPTED `cut` with the note "the overlay loader refuses encrypted files", but no ADR decided that: ADR-0010 refused only writes, and INV-03 says a ledger Beancount loads must be handled (S0015 verifier F1).

## Decision

(owner, 2026-10-25, quoted above)
- In v0.4.0, a ledger set that contains an encrypted file (any file Beancount's `is_encrypted_file` would decrypt: `.gpg`, or `.asc` with a PGP message) is **not read**. Opening it shows a plain message that names the file, says the ledger is unchanged, and says encrypted files are not supported in this version.
- This is a **stated exception to INV-03**: Finzytrack narrows this one Beancount feature in v0.4.0 and says so.
- Encryption support (read, and later write) is a **deferred capability**, built as its own feature after v0.4.0: CAP-DATA-ENCRYPTED moves from `cut` to `deferred`. Like every deferred row it never holds a milestone exit (ADR-0043); it was already outside v0.4.0's scope as a `cut` row, so ADR-0043's list of four is unchanged.

## Alternatives considered and rejected

- **Read encrypted files, refuse writes to them** (the agent's recommendation). Rejected by the owner for v0.4.0: it needs a gpg dependency on three OSes, a timeout and message for a locked agent or a pinentry dialog, and a decision on decrypted entries in the mirror database; better done whole, as its own feature.
- **Leave the `cut` row without an ADR.** Rejected: an unrecorded narrowing of INV-03.

## Consequences

- The M1a overlay-load slice (BL-0003) refuses encrypted files with the message above and tests it (an encrypted file in the parity corpus must give the refusal, never a partial load).
- No gpg dependency, no passphrase handling and no decrypted text in the mirror in v0.4.0.
- The later feature starts from the analysis in `design/inbox/answered/20261025-encrypted-ledgers.md` (gpg-agent, pinentry, timeouts, mirror privacy).

## References

INV-03, ADR-0010, ADR-0043, ADR-0054; CAP-DATA-ENCRYPTED; BL-0003; journal S0015 (verifier F1).
