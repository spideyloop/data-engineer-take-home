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

The submission uses GPT-OSS 120B through the Groq API. Set `GROQ_API_KEY` in your shell, then run:

```sh
python -m pipeline run --groq
python -m pipeline evaluate --groq --output data/evaluation-groq.json
```

The OpenAI API is also supported. Set `OPENAI_API_KEY` and omit `--groq` to use GPT-5.4 nano.

For local inference, start [Ollama](https://ollama.com/) and prepare the model from the repository's `evaluation/Modelfile`:

```sh
ollama pull qwen3.5:4b
ollama create takehome-qwen35 -f evaluation/Modelfile
python -m pipeline run --ollama
python -m pipeline evaluate --ollama
```

The run writes `data/jobs.sqlite` and `data/jobs.csv`. The included CSV is the verified Ollama snapshot of 119 open US jobs from 19 companies. The latest live refresh preserved all 118 existing IDs and added one Palantir listing. Replaying the repaired listings preserved the new ID and added no rows or change-history entries.

Both benchmarks use the same 20 labeled postings, including 10 HN comments. The linked reports include field-level accuracy and cost per posting. For multi-role comments, predictions are paired by normalized title. Skills use exact case-insensitive set matching.

| Metric | [Groq GPT-OSS 120B](evaluation/results-groq.json) | [Ollama Qwen3.5 4B](evaluation/results.json) |
| --- | ---: | ---: |
| Roles matched | 22/23 | 18/23 |
| Role precision | 91.7% | 100% |
| Correct city when stated | 9/9 | 8/9 |
| Correct salary bounds when stated | 6/7 each | 6/7 each |
| Estimated API cost per posting | $0.00051 | $0 |

Groq's estimated API cost was $0.0102 for the benchmark at published rates. Both runs completed without extraction errors.

## Design

**Fetch → extract → reconcile → export.** The adapters share robots.txt checks, pacing, retries, and a raw-response cache. HN uses HTML and follows `More` when present. Plaid's Lever board returned 404, so the configured Lever sources are Palantir and Spotify. A title prefilter limits LLM calls, and unchanged model inputs reuse validated results. Source text is treated as data, and extracted facts require source excerpts. Explicit city fields and uniquely stated pay ranges supplement the model. Conflicting pay ranges and hourly amounts without enough annualization information stay null.

Reconciliation uses stable source IDs and conservative cross-source matching. Complete ATS snapshots can withdraw missing listings. Failed or incomplete snapshots preserve prior status. HN closures require a first-party signal, so HN-only rows can go stale after a monthly rollover. The canonical record stays open while any linked listing is open. A transaction keeps the canonical row and its history together. CSV arrays and evidence are JSON cells. The SQLite tables `listings`, `jobs`, `changes`, `responses`, and `extractions` are directly inspectable.

**What breaks first.** Chromium renders HTML when the expected selector is missing. Browser resource requests still use robots rules, pacing, and the raw cache. This fallback supports same-origin GET resources. An unexpected Lever HTML response fails that JSON board refresh. Title variations and weak location text can cause missed jobs or duplicates. The evidence check verifies the source excerpt and the evaluation surfaces interpretation errors. Salary and location have one canonical value or range, so multiple incompatible regions remain in source evidence.

**At 5,000 sources per week.** Schedule by domain with bounded concurrency, store compressed raw bodies with retention, and index matching by normalized company. Preserve per-source refresh state and retry only failed work. At 50 eligible candidates per source and 10% changed per week, 25,000 extraction calls at 1,800 input and 350 output tokens would cost about **$12/week** at the Groq GPT-OSS 120B rates, before provider caching discounts. Verify the change rate and token mix from run logs before sizing the refresh. [Model pricing](https://console.groq.com/docs/model/openai/gpt-oss-120b)

**AI tools.** Codex assisted with design, code, tests, and source annotation. The submission benchmark uses GPT-OSS 120B via Groq, with a Qwen3.5 4B baseline via Ollama.
