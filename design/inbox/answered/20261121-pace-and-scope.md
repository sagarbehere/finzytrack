# Getting v0.4 out sooner: refuse rare setups, handle Low findings inside their round, support typical OneDrive, a lighter push gate mid-slice
- Asked: 2026-11-21 by the orchestrator, after the owner's question in session about M1b's pace. The owner agreed to the direction ("I like your recommendations and we should go that way") and asked for this note so the exact wording can be accepted, edited or rejected.
- blocks: nothing. S0138 is in review and goes on. The remaining M1b rows are re-planned under whichever rules are accepted.
- Rubric item: a process change (PROCESS.md and the owner's standing rules), which is the owner's. Changes B and C relax rules that IMP-0065 to IMP-0073 set.
- ADRs checked: ADR-0061 and its amendment of 2026-11-14 (all three OSes; the Windows runner required at every milestone exit from M1b). Nothing here changes it: v0.4 still ships for all three OSes. ADR-0068 D2 and D6: the best-effort modes already built (S0065, S0136) stay.

## Situation
Since M1b began on 2026-11-13, the slice journals record about 9 hours of build and fix time on the write contract itself (S0115 to S0124), about 3.5 hours on the Linux port (S0125), and **about 44 hours on the Windows port** (S0126 to S0137). Windows writes are still not on.

Most of the Windows time went to rare setups: network shares, EFS, FAT32 and exFAT, ReFS, read-only pointer files, hard links in the cache folder, a cache folder on a share. Each one cost an options note, a slice, two reviews and several 25-minute CI runs. Two fixed costs multiply this on every slice: a 10 to 15 minute push gate on each push, three or four times per slice, and Low test or docs findings becoming backlog rows that later become slices of their own.

v0.4 must work on all three OSes for current users, and we do not know their setups. What keeps their data safe in an unknown setup is a **clear refusal**: nothing written, and a message saying what to do. A refusal is cheap. An accommodation (a best-effort mode, a retry engine, a fallback) is where the weeks go.

## The changes (accept, edit or reject each)

### A. Refuse first for rare setups; build support when a user asks
New text for PROCESS.md §3 step 2, after "the policy option goes to the owner before coding":
> **Refuse first** (owner, 2026-11-21): a setup that is neither the OS's default nor one the OS promotes is refused before any effect, with a named reason that tells the user what to do (and, in the user docs, where to ask for support). An accommodation for it (a best-effort mode, a retry, a fallback, a probe-driven special case) is built only when a user has asked for it, or on the owner's words naming the setup as common. Accommodations already built stay.

A **common setup** means the OS's default, or a setup the OS pushes users into, such as Documents redirected to OneDrive (change D).

What it changes in M1b's remaining rows (each row edited with the owner's words once accepted):

| Row | Today | Under A |
|---|---|---|
| BL-0284 (a file held by antivirus or another program; the share's refused replaces) | a retry design and new wording | one clear "in use, try again" text; no retry. The share's refused replaces are stated as a limit |
| BL-0306 (naming the folder a share refused) | a wording slice | folded into BL-0284's text |
| BL-0300 (a linked read-only file in the mirror folder) | a limit, plus a test at each site | stated limit only; the tests are dropped |
| BL-0301 (backslash paths in Copy's moved lines) | read the string as Beancount's lexer does, or a limit | stated limit (Copy refuses, nothing written; `/` works) |
| BL-0303 (a false "wrong drive" seen once on Linux exFAT) | diagnose on the runner | stated as "seen once, fails closed"; diagnosed if it recurs; moves to M11 |
| BL-0305 (S0136's test gaps) | five test items | the cheap ones done in one batch with BL-0087's slice; the rest dropped with a reason each |
| BL-0283 (OneDrive and other reparse points) | refuse or support | **support the typical OneDrive setups** (change D); every other reparse point stays refused |

Unchanged:
- BL-0287 (S0138, in review).
- BL-0292 (a YAML parse of the workflows): cheap, and it saves CI time.
- BL-0299 (diagnose first).
- BL-0302 (a flaky test): cheap, and flakes cost gate time.
- BL-0307: a guard of real data, its own slice.
- BL-0087's must-fix lines.

### B. Low findings about our own tests or docs are handled inside their round
New text for PROCESS.md §3 step 7, replacing "Low findings may be dropped with a one-line reason" and the fresh round's "the rest become backlog rows" for these findings:
> A Low finding of class (c) or (d) is fixed in its round or dropped with a one-line reason in the journal; it never becomes a backlog row. A fix that changes only tests or docs needs no further review round. Options notes are for class (a) and (b) findings and for policy questions, not for (c), (d) or (e) findings (owner, 2026-11-21).

The fresh round after any **product-code** fix stays, and so do the data-safety and finance red teams, the High rule and INV-01, INV-22 and INV-32. Today's BL-0286 options note (a test guard for code BL-0087 deletes) is the kind of note B removes.

### C. A lighter push gate for mid-slice pushes (to decide; a tooling slice if accepted)
Today every push runs the whole pre-push gate (about 10 to 15 minutes), and a slice pushes three or four times just so the manual CI run can see it. Proposal: a push of a commit that does not land the slice runs only the checks that protect what leaves the machine:
- the staged-file guard;
- the privacy scan of the pushed commits and messages;
- the push-gate preconditions;
- invariants;
- the YAML parse (BL-0292).

The landing push runs the whole gate as today. Risk: a mid-slice commit on `next` may fail a test that the manual CI run then shows, which is the reason it is pushed anyway. The privacy and real-data guards (INV-23, INV-24) run on every push without exception. If you'd rather keep the gate as it is, reject C. A and B carry most of the gain.

### D. Typical OneDrive setups are supported on Windows
Windows 10 and 11 push users to back up Documents and Desktop to OneDrive, so a ledger in Documents is often inside a OneDrive folder. Today the Windows adapter treats every reparse point other than a link as "not a regular file" (BL-0283). OneDrive's Files On-Demand files are reparse points, so once writes turn on, a typical OneDrive user could neither read nor save. It fails closed, but it would break the users who matter most.

New text for BL-0283, as BL-0087's first part:
> Support the typical OneDrive setups: a ledger whose files are on this PC (downloaded or "always keep on this device") is read and saved normally; a cloud-only file is downloaded by Windows when the engine reads it while online; a cloud-only file that cannot be downloaded (offline) is refused with a named reason ("only in OneDrive, and you are offline"). OneDrive's upload holding a file at the moment of a save gets BL-0284's "in use, try again" text. Edits of one ledger on two PCs, which OneDrive turns into conflict copies, are a stated limit in the user docs. Every other reparse point stays refused.

Checking it, cheapest first:
1. **A simulation in CI:** a small test program on the runner registers a folder with Windows' Cloud Files API, the interface OneDrive itself is built on, and makes the same kind of placeholder files. CI can then test the reparse marker, download on read, the offline refusal and our rename over a placeholder on every run. No account is needed.
2. **One check by hand on your Windows PC with OneDrive,** once, near the end of BL-0087. A script I write: it puts a scratch ledger in a OneDrive folder, saves to it through the engine, and reports what OneDrive did (the marker, the upload after our rename, a save during an upload). About 10 minutes with the PC on. This is the only way to see OneDrive's own sync engine; the simulation shows Windows' part, not OneDrive's. A cloud VM signed in to a test Microsoft account would also work, but it costs you more setup than turning your PC on once.

## Recommendation
Accept A, B and D as written, and C if a faster mid-slice push is worth one tooling slice to you. Together they cut M1b's remaining Windows work from about fifteen slices to about five, by my estimate:
- BL-0087 with OneDrive;
- BL-0284 and BL-0306's texts;
- BL-0292 and BL-0302;
- BL-0299;
- BL-0307.

The write path keeps its full rigor where bytes are at stake. After M2, compare the time per feature slice with M1b's, so we know from numbers whether the rest goes faster.

## Answer (owner, verbatim, with date)
Owner, 2026-11-21, in session: "I'm accepting the inbox note as written"

**Decision: A, B, C and D as written.**
- A and B are in PROCESS.md §3 steps 2 and 7 and the review budget (IMP-0075).
- C is BL-0308, a tooling slice.
- D is BL-0283's new text.
- The rows are edited as the table under A says, each with the owner's words; BL-0303 moved to M11.

Earlier in the same session, on OneDrive: the owner has a Windows PC signed in to OneDrive and will turn it on for the one-off check by hand ("I can do so for a one off check if there is no other/better way"). The Cloud Files simulation in CI covers the rest.
