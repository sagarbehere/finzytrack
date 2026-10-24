# ADR-0006: Frontend framework: React, conditional on spike S13

This record states the target frontend stack, the spike that confirms it, the decision rule, and the fallback.

- **Status:** Proposed (pending spike S13). The orchestrator finalizes it from the S13 result.
- **Date:** 2026-10-24
- **Deciders:** owner (direction and decision rule); the S13 result decides
- **Area:** frontend, stack

## Context

The frontend is rewritten from a behaviour spec (ADR-0036); only about 9% of it survives as code, mostly framework-free modules (money, dates, parsers). So the framework can change at little extra cost. The desktop app runs in the system webview, which is WebKit on macOS and Linux (ADR-0007), so performance must be judged in WebKit, not only in Chromium.

## Decision

**Target (owner, 2026-10-24):** a React + Vite single-page app, with no Next.js and no server rendering:
- React 19 with the React Compiler (`babel-plugin-react-compiler`), eslint-plugin-react-hooks strict rules (recommended-latest, compiler rules as errors);
- shadcn/ui on Radix (vendored, unedited), TanStack Query / Table / Form / Virtual for React, `@xstate/react`, `@ai-sdk/react`, react-router in declarative mode, `lucide-react`, `sonner`, `eslint-plugin-jsx-a11y`;
- `tsc --noEmit` as the type gate.

**Spike S13 (M0) decides it.** Build the same prototype twice, in React and in Vue: the transaction grid with 54k rows virtualized, inline edit and keyboard navigation, plus one ECharts dashboard. Measure both in **WebKit** (Playwright WebKit and the macOS Tauri WKWebView), on the same data and machine:
1. scroll frame time (p95);
2. edit latency (keypress → committed cell visible);
3. initial render;
4. memory (JS heap and webview process).

**Decision rule.** If React is worse than Vue by **more than 20%** (the default margin of the spike brief) on **any** of these metrics, the frontend **stays on Vue**. Otherwise it is React.

**Vue fallback stack:** Vue 3.5, vue-router, `@tanstack/vue-query` / `vue-table` / `vue-virtual`, Reka UI via shadcn-vue, `@lucide/vue`, `vue-sonner`, `@xstate/vue`, `vue-echarts`, `@ai-sdk/vue`, `eslint-plugin-vue`, `eslint-plugin-vuejs-accessibility`, `vue-tsc`. The structure is the same (hooks become composables, `.tsx` becomes `.vue`). Exact pins for both stacks are in `design/ARCHITECTURE.md` §10.

Every frontend M0 slice waits for S13. Backend M0 work does not.

## Alternatives considered and rejected

- **Next.js or another server-rendering framework.** Rejected: a desktop SPA served by a local sidecar gains nothing from server rendering, and TanStack Query owns server state.
- **Choosing without a measurement.** Rejected: the grid at 54k rows in WebKit is the riskiest screen, and a wrong choice would be expensive to reverse after M2.

## Consequences

- Design documents describe the React target and name the Vue equivalent where it differs. Behaviour specs stay framework-neutral.
- The AI chat binding (`@ai-sdk/react` vs `@ai-sdk/vue`) and the a11y lint plugin follow this ADR (ADR-0034, ADR-0035).
- `frontend/CLAUDE.md` is written for whichever stack S13 selects.
- When S13 completes, this ADR is set to Accepted (React or Vue) with the measured numbers, or superseded.

## References

review:25 §3, §10; review:27 §1(d) (S13); review:F §2 (frontend note); review:21 §5 row FE; ADR-0034, ADR-0035, ADR-0036.
