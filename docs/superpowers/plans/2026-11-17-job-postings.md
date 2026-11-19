# Job postings implementation plan

Use the approved [design](../../../DESIGN.md). The entry point runs fetch → extract → reconcile → export. Use HTTPX, Beautiful Soup, Pydantic, and SQLite. Exercise each stage offline before running the public sources.

- [x] **Fetch** — `sources.py` produces `Snapshot` objects from Greenhouse, Lever, and HN. `http.py` owns pacing, robots, retries, and raw response storage. Test content type errors and HN comment pagination and reply handling.
- [x] **Extract** — `extract.py` takes `Posting` objects and returns validated, evidenced roles. Cache successful results by input and provider configuration. Test missing evidence and cache reuse.
- [x] **Reconcile and export** — `reconcile.py` owns cross-source matching and canonical records. `storage.py` defines the persistent tables. Test stable IDs, conflicts, partial refreshes, and closed listings. Export structured fields as JSON CSV cells.
- [x] **Run and evaluate** — wire stages in `__main__.py`. Evaluate against 20 independently annotated source postings, including at least 10 HN comments. Record token-derived costs and field-level denominators. Verify setup, CLI, and live collection, and write the README.

Live extraction and scoring use the configured provider credentials. Offline tests must run without credentials.
