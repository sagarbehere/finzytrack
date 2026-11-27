# M1b exit: Write contract (in progress)

The milestone's exit record (roadmap §3.2: "the milestone's journal entry"), started 2026-11-27 with the owner's local-tier runs; the exit decision, the red teams, the spot audit, the area review and the green manual CI run are added as they happen.

## Local tier (T-local) on copies of the owner's ledgers (2026-11-27)

Copies made by the owner with `cp -RL` into the T-local folder (`FT_LOCAL_TIER_DIR`), outside the repo; counts and case IDs only (PROCESS.md §8). The owner's two ledger roots are L1 and L2 below.

- **The copy's setup, fixed by the owner on the copy only:** each root sets `option "documents"` to a folder outside its own (`../…`); the first oracle run, on a copy without that folder, gave 15 of 15 cases UNEXPLAINED, one constant difference each, unrelated to the edit (no booked difference). With the documents folder copied beside the ledgers and the option pointed at it, and the old ledger backups (`*.backup*`, which still named the old folder) removed from the copy, both runs below pass. The D2 runner's plain-copy rule refused the copy until then (S0158; a stated cost).
- **D2 equivalence** (`engine/scripts/d2_local.py`, S0158): **2 EQUAL, 0 DIFFERENT, 0 REFUSED, 0 ERROR**, exit 0. L1: entries 10253, errors 0, options 35. L2: entries 10175, errors 1673 (the same in both loads), options 35. Run by the coordinator on the owner's word ("you can do Step 3 yourself").
- **Differential oracle** (`scripts/oracle/harness.py`, BL-0046), L1, 5 cases per mutator: **15 cases, 0 UNEXPLAINED**, exit 0: 10 MATCH (upd-001..004, ren-000..004), 5 EXPECTED_DIVERGENCE (upd-000 FIX-L3-02, the legacy's refusal of a cost edit; imp-000 FIX-L3-05+M28; imp-001..004 M28, the legacy's import format). No booked difference in any case. The owner ran the first run; the coordinator the reruns.
