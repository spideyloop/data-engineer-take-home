# US data and ML job postings

A Python pipeline that builds an evidence-backed job dataset from Greenhouse, Lever, and Hacker News. It retains source links and tracks salary, location, and status changes in SQLite.

## Run

On macOS, use Python 3.11 or newer. From the repository root, create a fresh virtual environment and install the dependencies:

```sh
python3 --version
python3 -m venv .venv
source .venv/bin/activate
python -m pip install '.[test,browser]'
python -m playwright install chromium
python -m pytest
```

Chromium provides JavaScript rendering for pages that need it. For setup without browser rendering, install `'.[test]'` and skip the Chromium command.

### Live extraction

Use either the hosted OpenAI model or local Ollama. For the hosted model, set your own `OPENAI_API_KEY` in the shell:

```sh
export OPENAI_API_KEY="your-key"
python -m pipeline run
python -m pipeline evaluate
```

For local inference, start [Ollama](https://ollama.com/) and prepare the model from the repository's `evaluation/Modelfile`:

```sh
ollama pull qwen3.5:4b
ollama create takehome-qwen35 -f evaluation/Modelfile
python -m pipeline run --ollama
python -m pipeline evaluate --ollama
```

The run writes `data/jobs.sqlite` and `data/jobs.csv`. The included CSV contains 119 open US jobs from 19 companies. The latest live refresh preserved all 118 existing IDs and added one Palantir listing. Replaying the repaired listings preserved the new ID and added no rows or change-history entries.

The [evaluation](evaluation/results.json) uses the same 20 source postings, including 10 HN comments. Labels were transcribed with Codex before scoring. Single roles are compared directly, and multi-role comments are paired by normalized title. Skills use exact case-insensitive set matching. On the rerun, 18 of 23 roles paired with a prediction, up from 15. Accuracy on stated values went from 0/9 to 8/9 for city and from 1/7 to 6/7 for each salary endpoint. Explicit ATS facts recovered two listings whose model responses failed validation. Ollama incurred $0 in API fees.

## Design

**Fetch → extract → reconcile → export.** The adapters share robots.txt checks, pacing, retries, and a raw-response cache. HN uses HTML and follows `More` when present. Plaid's Lever board returned 404, so the configured Lever sources are Palantir and Spotify. A title prefilter limits LLM calls, and unchanged model inputs reuse validated results. Source text is treated as data, and extracted facts require source excerpts. Explicit city fields and uniquely stated pay ranges supplement the model. Conflicting pay ranges and hourly amounts without enough annualization information stay null.

Reconciliation uses stable source IDs and conservative cross-source matching. Complete ATS snapshots can withdraw missing listings. Failed or incomplete snapshots preserve prior status. HN closures require a first-party signal, so HN-only rows can go stale after a monthly rollover. The canonical record stays open while any linked listing is open. A transaction keeps the canonical row and its history together. CSV arrays and evidence are JSON cells. The SQLite tables `listings`, `jobs`, `changes`, `responses`, and `extractions` are directly inspectable.

**What breaks first.** Chromium renders HTML when the expected selector is missing. Browser resource requests still use robots rules, pacing, and the raw cache. This fallback supports same-origin GET resources. An unexpected Lever HTML response fails that JSON board refresh. Title variations and weak location text can cause missed jobs or duplicates. The evidence check verifies the source excerpt and the evaluation surfaces interpretation errors. Salary and location have one canonical value or range, so multiple incompatible regions remain in source evidence.

**At 5,000 sources per week.** Schedule by domain with bounded concurrency, store compressed raw bodies with retention, and index matching by normalized company. Preserve per-source refresh state and retry only failed work. At 50 eligible candidates per source and 10% changed per week, 25,000 extraction calls at 1,800 input and 350 output tokens would cost about **$20/week** at the modeled GPT-5.4 nano rates, before provider caching discounts. Verify the change rate and token mix from run logs before sizing the refresh. [Model pricing](https://developers.openai.com/api/docs/models/gpt-5.4-nano)

**AI tools.** Codex assisted with design, code, tests, and source annotation. The extraction backend is GPT-5.4 nano via OpenAI, or Qwen3.5 4B via Ollama for local runs.
