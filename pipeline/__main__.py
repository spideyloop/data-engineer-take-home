"""Command-line entry point for the pipeline and its evaluation."""

import argparse
import fcntl
import logging
import os
from contextlib import contextmanager
from datetime import date
from pathlib import Path

import httpx

from .browser import render_html
from .evaluate import evaluate
from .export import export_csv
from .extract import (
    DEFAULT_MODEL,
    DEFAULT_RATES,
    OLLAMA_MODEL,
    Extractor,
    should_extract,
)
from .http import ScrapeClient
from .reconcile import reconcile
from .sources import collect
from .storage import Store, utc_now

LOG = logging.getLogger(__name__)


@contextmanager
def run_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(path.suffix + ".lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError(f"another run is already using {path}") from error
        yield


def run(
    store: Store, client: httpx.Client, extractor: Extractor, csv_path: Path
) -> int:
    scraper = ScrapeClient(client, store)

    def render(response: httpx.Response, selector: str) -> str:
        return render_html(
            response, selector, lambda url: scraper.get(url, same_origin=True)
        )

    snapshots = list(collect(scraper.get, date.today(), LOG.warning, render=render))
    observed_at = utc_now()
    for snapshot in snapshots:
        LOG.info(
            "Fetched %s: %d source postings", snapshot.source, len(snapshot.postings)
        )
    if not snapshots:
        LOG.error("No source snapshots completed")
        return 1

    candidates = [
        posting
        for snapshot in snapshots
        for posting in snapshot.postings
        if should_extract(posting)
    ]
    LOG.info("Extracting %d candidate source postings", len(candidates))
    observations = []
    errors = 0
    for index, posting in enumerate(candidates, start=1):
        try:
            observations.append((posting, extractor.extract(posting)))
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
            if isinstance(
                error, httpx.HTTPStatusError
            ) and error.response.status_code in {401, 403, 404}:
                raise
            errors += 1
            LOG.warning("Extraction failed for %s: %s", posting.url, error)
        if index % 20 == 0:
            LOG.info("Processed %d/%d candidates", index, len(candidates))

    reconcile(store, snapshots, observations, observed_at)
    count = export_csv(store, csv_path)
    LOG.info(
        "Exported %d jobs to %s; %d cache hits; %d extraction errors; estimated API cost $%.4f",
        count,
        csv_path,
        extractor.cache_hits,
        errors,
        extractor.cost_usd,
    )
    return int(bool(candidates and errors == len(candidates)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    refresh = commands.add_parser(
        "run", help="fetch, extract, reconcile and export jobs"
    )
    score = commands.add_parser(
        "evaluate", help="measure extraction against frozen labels"
    )
    for command in (refresh, score):
        command.add_argument(
            "--ollama",
            action="store_true",
            help="use local Ollama instead of the OpenAI API",
        )
        command.add_argument("--db", type=Path)
    refresh.add_argument("--csv", type=Path, default=Path("data/jobs.csv"))
    score.add_argument("--labels", type=Path, default=Path("evaluation/labels.json"))
    score.add_argument("--output", type=Path, default=Path("data/evaluation.json"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    if args.ollama:
        provider = {
            "api_key": "ollama",
            "base_url": "http://127.0.0.1:11434/v1",
            "model": os.environ.get("PIPELINE_OLLAMA_MODEL", OLLAMA_MODEL),
            "rates": (0, 0, 0),
            "ollama": True,
        }
    else:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            parser.error("set OPENAI_API_KEY or run with --ollama")
        provider = {"api_key": api_key, "model": DEFAULT_MODEL, "rates": DEFAULT_RATES}
    db_path = args.db or Path(
        "data/jobs.sqlite" if args.command == "run" else "data/evaluation.sqlite"
    )
    if args.command == "run" and db_path.resolve() == args.csv.resolve():
        parser.error("--db and --csv must be different files")
    if args.command == "evaluate" and args.output.resolve() in {
        db_path.resolve(),
        args.labels.resolve(),
    }:
        parser.error("--output must be different from --db and --labels")
    try:
        with (
            run_lock(db_path),
            Store(db_path) as store,
            httpx.Client(timeout=90) as client,
        ):
            extractor = Extractor(store, client, **provider)
            if args.command == "run":
                return run(store, client, extractor, args.csv)
            return evaluate(extractor, args.labels, args.output)
    except (httpx.HTTPError, ValueError, OSError) as error:
        LOG.error("%s", error)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
