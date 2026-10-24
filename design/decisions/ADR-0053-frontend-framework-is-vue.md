# ADR-0053: The frontend framework is Vue 3 (spike S13 outcome)

This record applies the owner's S13 decision rule (ADR-0006) to the measured result: the frontend stays on Vue.

- **Status:** Accepted (by the owner's pre-set rule; the owner may ask for the confirmation rerun below)
- **Date:** 2026-10-25
- **Deciders:** owner (decision rule, ADR-0006); spike S13 (the measurement)
- **Area:** frontend, stack

## Context

ADR-0006 made React the target, conditional on S13: if React is worse than an equivalent Vue prototype by more than 20% on any of scroll frame time (p95), edit latency, initial render or memory (JS heap and webview process), in WebKit, the app stays on Vue. S13 built the same 54k-row virtualized grid with inline edit and keyboard navigation, plus an ECharts dashboard, in both stacks at the pinned versions, and measured them in Playwright WebKit and in the macOS Tauri 2.12 WKWebView, with React and Vue rounds interleaved (10 + 10 per environment, plus a 5 + 5 memory batch). Evidence: review:spikes/s13-react-vs-vue.

## Decision

**Stay on Vue 3.** Timing is a tie: scroll p95, edit latency and initial render ratios are 0.97–1.02 in both environments. Memory is not: after the workload, the React webview process used **22–64% more memory by RSS in every batch and both environments**, and 28% more by phys_footprint in the main Playwright batch (9–18% in the other after-run comparisons). That exceeds the 20% margin on the memory criterion.

The frontend follows ARCHITECTURE.md §3 with the documented substitutions: hooks → composables, `.tsx` → `.vue`, TanStack Vue Query / Table / Virtual, shadcn-vue on Reka UI, `@xstate/vue`, `vue-echarts` or an own composable, `@ai-sdk/vue`, `@lucide/vue`, `vue-sonner`, `eslint-plugin-vue` + `eslint-plugin-vuejs-accessibility`, `vue-tsc` as the type gate. Pins: the Vue fallback rows of ARCHITECTURE.md §10. Large server results use `shallowRef`.

## Alternatives considered and rejected

- **React 19 + React Compiler (the target).** Fails the memory criterion. It also did 1.7–3× more main-thread JS per scroll frame and per edit commit (still inside one 60 Hz frame on this machine, but less headroom), and the Compiler bails out of the grid component because of TanStack Virtual, forcing hand memoization in the most performance-sensitive component.
- **Reading the rule as phys_footprint only.** Under that reading the memory failure is borderline (one of four after-run comparisons over the margin). The rule as written names the webview process's memory without choosing a metric, and RSS fails consistently, so the rule is applied as written. The owner may narrow the metric; that would reopen this ADR.

## Consequences

- Frontend M0 slices proceed on Vue now; "TanStack Query", "hooks", "shadcn/ui" and `tsc --noEmit` in the design documents read as Vue Query, composables, shadcn-vue and `vue-tsc`.
- `frontend/CLAUDE.md` is written for Vue. INV-18's lint is `eslint-plugin-vuejs-accessibility`.
- **Caveats** (review:spikes/s13 "Threats to validity"): the screen was locked during the runs (the Tauri shell disabled WKWebView occlusion detection for both apps alike); other agents loaded the machine (load 4–13, interleaved rounds); Linux WebKitGTK was not measured. A confirmation rerun with the screen unlocked is cheap (`spikes/s13-react-vs-vue`, "How to reproduce") and is offered to the owner; no frontend code exists yet, so changing course costs little until M2.

## References

ADR-0006 (target and rule); review:27 §1(d) S13; review:25 §3, §10; ADR-0034, ADR-0035, ADR-0036.
