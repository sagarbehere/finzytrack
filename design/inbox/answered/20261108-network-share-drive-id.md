# A ledger on a drive that reports no ID: refuse every save there (as built), or identify it another way?
- Asked: 2026-11-08 by the implementer of S0089-recovery-volume-id
- blocks: nothing (S0089 ships the refusal, which fails closed; this asks you to confirm it)
- Rubric item: 5 (scope: it may stop saving on some network shares), 8
- ADRs checked: ADR-0012 and your amendment of 2026-11-08 ("every journal will carry the UUID; one without it is refused the same way"), ADR-0068 D2 (network shares are saved on in best-effort mode), ADR-0061 (macOS first). Your decision says a journal without a drive ID is refused; it does not say what a save does on a drive that has no ID to record.

## Context (plain language, a concrete example first)
Example: your ledger is on a NAS share mounted at `/mnt/finance` (on a Mac, under the Volumes folder). Since S0089 every save writes down the ID (the volume UUID) of the drive it writes on, so that an interrupted save is undone only on that same drive and never on another drive mounted at the same place later.

Some kinds of drive may report no ID at all. On the Mac I checked: the Mac's own disk (APFS), HFS+, exFAT and FAT drives all report one, stable when the drive is unplugged and plugged in again. System folders such as `/dev` report none. I could not test network shares (SMB, NFS) here; some of them may report none.

S0089 refuses a save on a drive with no ID before writing anything ("Finzytrack cannot identify the drive that holds main.beancount, so it could not undo the change if saving were interrupted"). The reason: a save there would write a journal that, by your rule, could never be undone; if that save were interrupted, the ledger would stay half-changed and blocked until you chose "Keep the files as they are now".

## Options
1. **Keep the refusal (as built).** Safe and simple. A user with a ledger on such a share must move it to a drive that reports an ID. Cost: if many network shares report no ID, network shares are not supported for saving (best-effort mode stays for those that report one).
2. **Use another identity for a drive without a UUID**, for example the share's server and name (`//nas/finance`) as macOS reports it. Saving works there; recovery checks that the same share is mounted. Cost: one more rule to build and test, on volume kinds I cannot test here.
3. **Probe first:** you run a 1-minute check on a share you use (a script I would give you); then decide between 1 and 2 with facts.

## Recommendation
Option 3, then option 1 unless your shares report no ID. Keeping the refusal costs nothing on the drives most people use, and it never risks writing on the wrong drive.

## Answer (owner, verbatim, with date)
Owner, 2026-11-09, in session, choosing "Probe, then keep refusal (Recommended)": "Ship the refusal now (fails closed, never writes on a wrong drive). You run a 1-minute script on a network share you use; if it reports an ID, nothing changes; if not, we decide then whether to add a share identity (server + share name). Network shares stay best-effort where they have an ID."

Recorded in S0089 (round 1): the refusal stays (`no_drive_id`, ADR-0012 amendment of 2026-11-08); the probe is `scripts/probe_drive_id.py` (read-only); the follow-up decision is BL-0219.
