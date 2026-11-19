# Job postings pipeline design

Build a modular Python pipeline that produces a current dataset of US and US-remote data, ML, analytics, and platform engineering roles. Reviewers can run it with `python -m pipeline run` and inspect the SQLite database and CSV. The expected 100–300 postings are an estimate. The README will report the observed count.

## Approach

Use one package with four stages and an entry point that calls them in order. Keep the stages callable on their own for tests. Separate CLI commands with intermediate files would make each handoff inspectable, but would add file formats and setup. SQLite gives us a single place to inspect what the pipeline observed and changed.

1. **Fetch.** Give Greenhouse, Lever, and Hacker News one adapter each. Use the boards listed in the brief and document any same-ATS replacement for an empty board. Discover the current HN hiring thread from the submitter's HTML page, follow its `More` links, and retain comment permalinks. Share HTTP code for robots.txt, per-domain pacing, bounded retries with backoff, and raw-response caching. Check response types before parsing. Refresh live sources on each run so we can detect changes. Record whether each source snapshot completed, and continue past individual source failures.
2. **Extract.** Use a light title and geography filter to reduce LLM calls while allowing mixed-role HN comments through. Send cleaned posting text and source metadata to the LLM, split comments into individual roles, and validate the results against the brief's schema. Require a verifiable source snippet for each stated field. Use null for missing or ambiguous facts. Include roles with evidence of US eligibility, including remote roles whose stated geography includes the US. Annualize compensation when the period and any required work schedule are stated. Preserve conflicting location-specific ranges in the source evidence.
3. **Reconcile.** Track each source listing and its link to a stable canonical ID in SQLite. Use a shared requisition ID or job-specific application URL as strong duplicate evidence, then compare normalized company, title, and location. Keep uncertain matches separate. Prefer employer ATS evidence for conflicting values and retain each source's evidence and link. Write canonical records and salary, location, and status changes in one transaction. Advance `last_seen` on fresh observations. Reprocessing unchanged input creates zero duplicate jobs or history events.
4. **Export.** Write the latest canonical records to CSV with the brief's columns, including status. Encode lists and evidence as JSON cells and represent nulls as blank cells. Keep the database as the source for observations and history.

## Refresh and closure

Cache validated extractions by source and a hash of the inputs that affect extraction, including the model and prompt version. Only a complete ATS snapshot can mark a missing ATS listing as withdrawn. A fetch, parse, or pagination failure preserves the previous state. For HN-only postings, require an explicit closure signal from the poster. Older HN listings can stay stale across monthly rollovers. Keep a canonical job open while any linked source still indicates it is open.

## Review and acceptance

- Run the pipeline twice against fixed inputs and verify stable IDs, retained source links, reused extractions, and zero duplicate history events. Test matching across title variants, keeping separate requisitions distinct, and updates after salary, location, or status changes.
- Test a partial refresh and an HN month rollover to verify that neither causes a false closure.
- Hand-label 20 source postings, at least 10 from HN, before scoring model output. Report field-level accuracy for the extraction fields, including a separate breakdown for stated values, plus average LLM cost per evaluated posting from recorded token usage. Match predictions to individual labeled roles when a comment lists several.
- Package the database, CSV, labeled set, results, and tests in a private GitHub repository. The one-page README covers setup, AI tools used, trade-offs, failure modes, and how cached extraction and bounded fetch concurrency could support 5,000 weekly sources.

The named sources use JSON and static HTML, so the design uses ordinary HTTP requests and HTML parsing. Unexpected HTML from a JSON endpoint is a failed snapshot. The LLM provider remains open within the brief's $20 reimbursement allowance.
