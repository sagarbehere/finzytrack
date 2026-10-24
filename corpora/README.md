# corpora/

Shared test assets that grow over time: nasty ledgers (globs, CRLF, pushtag, plugins, `@@`), payload corpora (markup in payees and metadata, chart formatters, markdown, SQL/BQL escapes, path traversal), old-workspace fixtures, and anonymized real exports.

**Real exports** (bank and broker statements, CAS, `.eml`) enter only after anonymization by script and **the owner's review of the result**. Each such file is listed in `MANIFEST.tsv` with its SHA-256 and the review date; the T0 staged-file guard refuses any export that is not listed with a matching hash (INV-23). Real ledgers never come here: copies of the owner's ledgers live outside the repo and run only in T-local.
