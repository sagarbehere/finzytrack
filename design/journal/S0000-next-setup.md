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
- `next` lives in its own worktree, so the legacy checkout's ignored files (real statement files among them) never sit beside the new tree (agent).
- Commit order: `.gitignore` alone first; then the gates with the guard hooks and the capability map; then the `design/` seed; then agents, skills and pointer files. Each commit after the first ran through the new T0 hook (agent).
- Capability map source is TOML (stdlib-readable), generated to markdown (agent; IMPROVEMENTS IMP-0002).
- Pushing is off in a clone until the owner turns it on (agent tightening; IMP-0003).
- The four review:F capability rows without a milestone got one: CAP-WS-TEST-COPY M3, CAP-VIZ-A11Y M7 (from review:27 App. A.1), CAP-APP-OFFLINE M10, CAP-DESK-LICENSE-NOTICES M11 (agent).
- Invariant due milestones: the plan names INV-18/INV-20 (M11); the others are the milestone that builds their first check (agent), aligned between `INVARIANTS.md` and `backlog.md`.
- The checks run on the system Python with no third-party packages, so T0 needs no setup; detect-secrets joins when a pinned dev environment exists (BL-0035).

## Verifier
- Pending: the first verifier run happens when the owner reviews this branch (the setup was done by the orchestrating agent with sub-agents for the ADR, architecture and backlog migration).

## Retro
- The staged-generated check caught a real ordering error in this slice (generator staged without its output) on the first try.
- The privacy scanner was calibrated on the 25 private planning docs before use: 7 hits, all genuine home paths, no false positives.
