# Finzytrack v0.4 (branch `next`)

Pointers, not a design: project knowledge lives in `design/` (INV-19 keeps it from drifting).

- **Start:** `design/README.md`, then `design/process/PROCESS.md`. Rules: `design/INVARIANTS.md`; principles: `design/ENGINEERING.md`; system: `design/ARCHITECTURE.md`; "where are we?": `design/specs/roadmap.md`; "did we do X?": `design/capabilities.md`; "why?": `design/decisions/`.
- **Work** in slices with the `slice` skill; begin every request with the `orient` skill. Owner questions go to `design/inbox/` via the `escalate` skill (rubric: `design/process/escalation-rubric.md`).
- **Agents:** `.claude/agents/implementer.md`, `.claude/agents/verifier.md`.
- **Hooks:** install once per clone with `git config core.hooksPath .githooks`. Never skip them.
- **Scripts** live in three places, by reach: `backend/scripts/` (backend-internal, cwd = `backend/`), `frontend/scripts/` (frontend build tooling, `.mjs`), `scripts/` (repo-wide, run from the root).
- **Never:** touch the owner's live data folder or running app; write into the legacy worktree; run `git clean -x/-X`; commit real data, secrets or home paths (INV-23, INV-24); push unless the owner asks in the session.
- **Owner-reported bugs:** present the diagnosis and get agreement before editing (PROCESS.md §5).
