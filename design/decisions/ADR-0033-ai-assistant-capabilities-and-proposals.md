# ADR-0033: One AI assistant over shared capabilities; changes are proposals, never auto-applied

This record decides the assistant's architecture and its write model.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** AI

## Context

In v0.3 the AI was a parallel application: its tools re-implemented paths the UI used, adding a write capability touched 6–9 files, write tools applied changes immediately, attaching a file forced a rule-setup mode with no ledger read tools, and analyst numbers came from rounded context instead of the financial functions (review:13).

## Decision

(owner, 2026-10-24)
- **Capabilities, not tools.** Each AI capability is a thin declaration over a use case, the same one the UI calls. A new UI capability becomes available to the AI with one declaration.
- **One write model:** propose → preview (diff) → user confirms → commit → undo. Ledger proposals commit through the write contract; rule and dashboard proposals through `RuleStore` / the recipe validator with an atomic write and backup.
- **Proposals are never auto-applied** (INV-13), for ledger, rule and dashboard changes. Relaxing this needs an invariant change.
- **Approvals are bound to server state:** an approval names a server-side plan id and a preview hash; commit recomputes the preview, compares the hash, uses the plan once, and commits the server's stored change. AI proposals are plans in the same `PlanStore` and confirm through the same endpoint as UI previews. Client-sent approvals are never trusted on their own.
- **One assistant, intent-driven.** Attachments are artifacts that tools read (parse with the import adapters, reconcile with the matcher, derive a rule, answer questions). It asks one clarifying question when intent is genuinely ambiguous.
- **Capability profiles instead of modes:** a small default tool set plus `list_capabilities` / `load_guide` expansion, chosen from the request and the model's capabilities, so prompts stay small for local models.
- **Figures come from tools** that use the kernel's financial functions.
- **Trust levels:** content from attachments, emails and payees is untrusted. A session that has read untrusted content cannot auto-apply anything.
- **Conversation history:** server-side, append-only, in memory with a TTL, keyed by (workspace, thread); not persisted across restarts.
- **Data egress:** no in-app disclosure or opt-in; the user docs explain exactly what is sent to the model per use case and are kept accurate.
- **Local models** (LM Studio, Ollama): reads and extraction work via a JSON-mode fallback; proposals require native tool use.
- **IMAP credentials:** the AI writes `${…}` references, never asks for plaintext, and secrets are redacted on every read (ADR-0021).

## Alternatives considered and rejected

- **Tools that re-implement use cases.** Rejected: duplication and drift.
- **Modes that fork on "file attached".** Rejected: brittle; a profile system keeps the local-model benefit.
- **Auto-applying low-risk changes.** Rejected (INV-13).
- **Trusting approvals from the chat message array.** Rejected: the client controls it.

## Consequences

- Ledger proposals arrive only after the matching mutators are promoted (ADR-0014).
- Reconcile-from-assistant and attach-intent are release-blocking (ADR-0043).

## References

review:13 §1–§3, §5; review:25 §4.2, §9.5; ADR-0014, ADR-0021, ADR-0034, ADR-0043; INV-09, INV-13; CAP-AI-PROPOSALS, CAP-AI-LEDGER-WRITES, CAP-RECON-ASSISTANT, CAP-AI-ATTACH-INTENT.
