# beanquery needs TatSu-LTS, whose licence adds an acknowledgment clause: may the ledger engine ship it?
- Asked: 2026-10-26 by the implementer of S0035-engine-process
- blocks: the first BQL slice (CAP-ENGINE-BQL, M7), which is the first code to need beanquery; not S0035, which leaves beanquery out of the engine for now
- Rubric item: 4 (ADR-0054's reviewed list of Beancount's own dependencies would change) and the legal question ADR-0042 keeps for the owner
- ADRs checked: ADR-0054 decision 5 names the engine allowlist as "GPL-2.0-compatible plus an explicit, reviewed list of Beancount's own dependencies (`regex`, `python-dateutil`, pending legal advice)"; TatSu-LTS is on neither part. ADR-0042 keeps the bundle question for legal advice before v0.4.0. Neither settles TatSu-LTS.

## Context (plain language, a concrete example first)

The ledger engine is the separate program that holds Beancount (ADR-0054). Everything inside it must be compatible with Beancount's licence, GPL version 2. A typical "BSD" licence is fine: it asks you to keep the copyright notice, and the GPL asks for that too.

`beanquery` (Beancount's query language, BQL; our pin 0.2.0) needs one more library, **TatSu-LTS 5.16.0** (a parser generator). Its licence is a BSD licence with an extra fourth clause:

> 4. Redistributions of any form whatsoever must retain the following acknowledgment: 'This product includes software developed by "Juancarlo Añez" (https://apalala.bitbucket.io).'

The GPL v2 forbids adding requirements beyond its own. The FSF treats similar acknowledgment clauses (the old 4-clause BSD advertising clause; the Apache 1.1 acknowledgment clause) as incompatible with the GPL. Whether this exact wording is a further restriction is a legal question I cannot settle. Its metadata only says "BSD License", so the licence check could not have read it without the text; I read the licence file in the wheel.

Beancount itself (3.2.0) does not need TatSu-LTS. Only beanquery does, and the plan needs BQL from M7 (recipes' `engine: beanquery` steps and the Analyze screen). The legacy app (v0.3, tag `legacy-final`) already bundles beanquery 0.2.0 with TatSu-LTS 5.13.2, so this is not new exposure, only newly noticed.

## Options
1. **Add TatSu-LTS to ADR-0054's reviewed list, pending legal advice** (like `regex` and `python-dateutil`), and add the question to the lawyer's list. beanquery joins the engine with the first BQL slice.
2. **Keep beanquery out until the legal advice is in**: BQL work (M7) waits for the answer; if the advice is negative, BQL needs another route (for example asking TatSu's author to drop clause 4 for the LTS line, or a different parser for BQL upstream).
3. **Ask the TatSu author now** whether clause 4 can be dropped or relaxed for redistribution under the GPL, and decide between 1 and 2 when the reply comes.

## Recommendation
Option 1, plus option 3 in parallel (a short, polite request costs nothing). Nothing before M7 needs beanquery, so there is time for the legal advice; option 1 keeps BQL on plan and puts the question where ADR-0042 already sends licence questions.

## Answer (owner, verbatim, with date)
