# What happens when a ledger runs code and the user has not trusted it yet?
- Asked: 2026-10-28 by the orchestrator, from S0049's verifier (finding F2)
- blocks: the first slice that opens a user's ledger in the product (M2's open flow, with BL-0102), and the restrictive desktop body of `LoadPolicy`; not S0049, which builds the seam only. Until answered, L11-02, L11-06 and L11.M-1 stay open ("seam built; desktop body pending this answer").
- Rubric item: 6 (security posture) and 3 (finance correctness: which numbers the user sees)
- ADRs checked: ADR-0040 (security foundations: "until trusted, it loads under the restrictive policy"), ADR-0041 (Deferred: the desktop trust prompt and its restrictive LoadPolicy ship in v0.4.0), ADR-0010 (overlay load and LoadPolicy), ADR-0059 (load refusals). None says what "loads under the restrictive policy" shows the user.

## Context (plain language, an example first)
A Beancount ledger can run Python code when it is loaded: plugins (`plugin "my_plugin"`) and extra code folders (`option "insert_pythonpath"`). Opening a ledger someone else wrote could run their code on your machine. ADR-0040 says: on first open such a ledger asks "This ledger runs code … Trust?", and until the user trusts it, it "loads under the restrictive policy".

The problem is what that load shows. Example: a ledger uses a plugin that adds price entries or splits expenses between accounts. Loaded without that plugin, its balances and net worth come out **different**: not wrong arithmetic, but not what the ledger means. Showing those numbers, even with a banner, risks the user acting on them.

S0049 built the seam (`LoadPolicy` is asked before every file read, every plugin, every code folder and every documents folder) and the desktop body that allows everything. Its only answer to a "no" today is refusing the whole load.

## Options
1. **Until trusted, the ledger does not open.** The open shows what code the ledger runs (plugin names, code folders) with "Trust and open" and "Cancel". The answer is remembered on this machine. No numbers are ever shown without the ledger's own code. Beancount's own built-in plugins count as trusted (an allowlist). Amends ADR-0040's sentence to "until trusted, it does not load".
2. **As ADR-0040 is written:** until trusted, it loads without the untrusted code and shows its numbers with a banner ("This ledger's plugins did not run; figures may differ"). Users see something at once, but the figures can differ from what the ledger means.
3. **No trust prompt on desktop:** a ledger on your own machine runs its code, as `bean-check` and Fava do today. Simplest; the risk is opening a ledger from someone else. Amends ADR-0040 and ADR-0041 (a loosening).

## Recommendation
Option 1. It keeps the protection ADR-0040 wants and never shows numbers computed without the ledger's own code (finance correctness first). It is also simple: the seam already refuses the whole load on a "no"; the only addition is the trust screen in M2's open flow, and the plugin's config passed to the policy (for ADR-0040's "plugins that evaluate non-literal config"). Built in M2 with the open flow; a backlog row is added once you answer.

## How trust is stored (owner agreed, 2026-10-29, in session: "Yes, that works")
Whichever option is chosen, a trust answer is kept like this:
- **Machine-local:** a small JSON record in `app_local_data/` (beside the engine lock), written atomically; never in the ledger, never in the workspace folder, so a downloaded or synced ledger or workspace cannot arrive trusted, and trust on one computer does not carry to another.
- **Keyed by** the workspace's lineage id plus exactly what code the ledger runs: the plugin module names, whether each plugin's config is a plain literal, and the resolved `insert_pythonpath` folders. If any of these changes (a plugin or code folder added or changed), the ledger asks again. Editing your own plugin's source does not ask again (a changed plugin source already makes the save's re-check "superseded", BL-0124).
- **Records** which code was trusted and when; forgetting the workspace removes its trust; a setting lets you revoke it.
- **Never** a global "trust everything" switch, and never trust by folder path (a path can point at different ledgers over time; the lineage id cannot).

## Answer (owner, verbatim, with date)
Owner, 2026-10-29, in session: "Yes, rename to composables everywhere including INV-12. Agreed with the rest. No, ledger that runs code dosen not open before I trust it." (the first two sentences answer other questions).

To apply: option 1 (BL-0156, M2, with the open flow): on desktop, a ledger that runs code (a plugin outside Beancount's own `beancount.plugins.*`/`beancount.ops.*`, `insert_pythonpath`, or a plugin that evaluates a non-literal config) does not open until the user trusts it on the trust screen, which lists the code it runs; Beancount's own plugins are trusted; the answer is stored as "How trust is stored" above says. Recorded as the ADR-0040 amendment of 2026-10-29; L11-02, L11-06 and L11.M-1 point at BL-0156 (S0056).

## Follow-up (owner, verbatim, with date): the feature is paused
Owner, 2026-10-29, in session, after S0056's verifier found that `beancount.plugins.pedantic` also passes its config to `eval` (so the first design's list of "plugins that evaluate config" was incomplete): "I'm wondering if we should remove this feature for now. None of the beancount tools I know have this.. it'll likely cause some confusion (unless explained well) to even well-versed users.. or, at minimum, add one more friction step whose answer is difficult to give precisely or correctly. I'd say, pause this feature. Mark it as something to be done later, and be done with it. What do you think?" The orchestrator agreed.

Outcome (S0056 verifier round 1 fix): for v0.4.0 this is option 3, "No trust prompt on desktop": a ledger's plugins and `insert_pythonpath` run as Beancount runs them, as `bean-check` and Fava do; the desktop `LoadPolicy` body allows everything (as S0049 built it); the seam stays. It supersedes the answer above ("No, ledger that runs code dosen not open before I trust it.") for v0.4.0. Recorded as ADR-0040's amendment "no trust prompt on desktop in v0.4.0" (the earlier amendment is marked superseded), with notes on ADR-0010, ADR-0041 and ADR-0043. BL-0156 is now "a trust prompt for code-running ledgers (later)", `post-v0.4`, with what was learnt; L11-02, L11-06 and L11.M-1 are `post-v0.4` (only their hosted body is left); CAP-WS-TRUST is `deferred`. The limit users are told (BL-0157): opening a ledger runs its code, so open only ledgers you trust. "How trust is stored" above stays as the starting point for BL-0156, with BL-0156's corrections (each non-literal config's text in the key).
