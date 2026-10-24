# ADR-0034: AI stack: Pydantic AI, AI SDK v7, markdown-it + DOMPurify, our own components

This record decides the libraries for the agent loop, the chat stream, markdown and the chat UI, and the scope of AI providers in v0.4.0.

- **Status:** Accepted
- **Date:** 2026-10-24
- **Deciders:** owner
- **Area:** AI, stack

## Context

v0.3 hand-wrote the agent loop, the provider layer, JSON fence-stripping, the SSE handling, the "working" indicator, the thinking display and a markdown renderer. A spike with fake models tested the candidate libraries (review:13 §4).

## Decision

(owner, 2026-10-24)
- **Agent loop and LLM gateway: Pydantic AI** (`pydantic-ai-slim[openai,anthropic]`): model-agnostic (OpenAI-compatible and Anthropic), typed tools, structured outputs, per-server model profiles (`ModelCaps`), usage capture, and the Vercel AI adapter with `sdk_version=7`.
- **Chat state and stream: the Vercel AI SDK UI layer, v7** (`ai` + `@ai-sdk/react`; `@ai-sdk/vue` if ADR-0006 keeps Vue). The chat endpoint speaks the AI SDK UI message stream protocol v7. Our custom parts (`data-proposal`, `data-plan-status`, `data-citation`) are exported into the OpenAPI spec. `ai`, `@ai-sdk/react` and Pydantic AI's `sdk_version` are upgraded together in one slice.
- **The approval binding is ours** (ADR-0033). The library's stock approval flow trusts client-sent approvals by design, so it is not used as is.
- **Markdown: `markdown-it` with raw HTML disabled, plus DOMPurify.** One `SafeMarkdown` component is the only raw-HTML site in the app.
- **Visible components are our own**, thin, on shadcn/ui primitives: message, tool card, proposal card, thinking block, a stick-to-bottom hook.
- **Providers in v0.4.0:** bring-your-own cloud providers (OpenAI-compatible or Anthropic, with URL, model and key) and local models. **Finzytrack AI mode is deferred** past v0.4.0 (CAP-AI-FINZYTRACK), so v0.4.0 needs no cloud service.
- Exact pins with the 14-day rule; the hand-written-SSE rule of the old `frontend/CLAUDE.md` does not apply to the chat stream.

## Alternatives considered and rejected

- **A chat UI kit (AI Elements Vue).** Rejected: its confirmation component showed "Accepted" when the server denied an approval; its markdown loaded remote images and fetched favicons for every linked site (data-leak channels); it added 16 caret-ranged dependencies and about 260 KB gzipped. Individual pieces may be copied.
- **`markstream-vue`.** Rejected: safe only with HTML escaping forced, and about 233 KB gzipped.
- **Keeping the hand-written loop and renderer.** Rejected: about 1,800 lines replaced by about 400 of ours.

## Consequences

- Upgrading forces newer `pydantic`, `openai` and `anthropic` versions (pins in ARCHITECTURE §10).
- AI SDK majors arrive about every six months; protocol churn is contained by the one-slice upgrade rule.
- A frontend contract test re-runs on `@ai-sdk/react` in the first assistant slice.

## References

review:13 §4 (spike result and decisions); review:25 §4.3, §10; ADR-0006, ADR-0033, ADR-0040, ADR-0043; INV-09, INV-13; CAP-AI-MARKDOWN, CAP-AI-PROVIDERS, CAP-AI-FINZYTRACK.
