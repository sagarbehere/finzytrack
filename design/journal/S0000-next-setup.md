# S0000: Set up `next` (empty tree, gates, design/ seed, agents)

## Brief
- Goal: start v0.4 on `next` from an empty tree with the guards in place before anything else lands; seed `design/` from the 2026-10 review; add the T0/T1 gates, the Claude Code guard hooks, the first two agents and three skills. Owner instruction for an unattended session (review:RESUME, "Next session: unattended start").
- Implements: ADR-0001 (process), ADR-0002 (greenfield on `next`); review:27 §1(d) M0 "thin"; review:F §3.12.
- Capabilities: none built (the map is created with every row `planned`, `cut` or `deferred`).
- Invariants touched: INV-19, INV-23, INV-24 (enforced from this slice); all others listed with their pending backlog item.
- Risk class / lane: High (repo hygiene and privacy of a public repo) / full.
- Acceptance tests: `scripts/tests/` meta-tests for every check script and guard (62 tests); the gates must pass on every commit of this slice.
- Expected doc impact: `design/` itself; no user docs.
- Open questions: in the owner's private question list (worktree location, push gate, denylist, licence allowlist, security severities in the public backlog, INV-27 enforcement).

## Decisions
- `next` was first built in a separate worktree, so the legacy checkout's ignored files (real statement files among them) never sat beside the new tree (agent). On 2026-10-25 the owner moved the real files out and cleaned the checkout, the extra worktree was removed, and the main checkout now has `next` checked out (owner).
- Commit order: `.gitignore` alone first; then the gates with the guard hooks and the capability map; then the `design/` seed; then agents, skills and pointer files. Each commit after the first ran through the new T0 hook (agent).
- Capability map source is TOML (stdlib-readable), generated to markdown (agent; IMPROVEMENTS IMP-0002).
- Pushing is off in a clone until the owner turns it on (agent tightening; IMP-0003).
- The four review:F capability rows without a milestone got one: CAP-WS-TEST-COPY M3, CAP-VIZ-A11Y M7 (from review:27 App. A.1), CAP-APP-OFFLINE M10, CAP-DESK-LICENSE-NOTICES M11 (agent).
- Invariant due milestones: the plan names INV-18/INV-20 (M11); the others are the milestone that builds their first check (agent), aligned between `INVARIANTS.md` and `backlog.md`.
- The checks run on the system Python with no third-party packages, so T0 needs no setup; detect-secrets joins when a pinned dev environment exists (BL-0035).

## Verifier
- An independent adversarial verifier (fresh context, given the branch and the private sources, not the implementer's reasoning) reviewed commits 2777d57..214f592: privacy/scrub, faithfulness of ADRs/invariants/capabilities/backlog to the decisions, gate bypass attempts, process consistency. Report: review:G (private, because it contains bypass repros).
- Findings: 7 High, 37 Medium. Fixed in the follow-up commit: P-1 (security severities and code pointers made neutral), P-2 (live-data folder name removed from docs; scanner rule added), G-1/G-2/G-3/G-4/G-5/G-7 (scanner bypasses; UTF-16 decoded), G-8..G-11 (staged guard: renamed/other exports, binaries, more ledger and secret file types, symlinks), G-12..G-20 and G-14 (guard bypasses incl. calling the owner's app over HTTP), F-1/F-2/F-3/F-4/F-5/F-6/F-7, R-2..R-6, R-8, R-10, R-11. Each bypass has a meta-test. Tracked: F-8/F-9 (BL-0040), F-10 (unmapped register rows, review:G), F-11 (BL-0039), R-9 (BL-0038), BL-0034/0036.
- **Owner approval:** not yet given. The owner was asleep; this slice is High and needs the owner's sign-off, which is the review before the first push (the push gate stays closed until then). Record the owner's words here verbatim.
- R-7: owner questions from this unattended session went to the owner's private question list because several cite private evidence; the decisions that result are recorded here as ADRs.

## Retro
- The staged-generated check caught a real ordering error in this slice (generator staged without its output) on the first try.
- The privacy scanner was calibrated on the 25 private planning docs before use: 7 hits, all genuine home paths, no false positives.
