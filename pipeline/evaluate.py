"""Score frozen source postings separately from the production database."""

import json
import logging
from collections import defaultdict
from pathlib import Path

import httpx

from .extract import FACTS, Extractor, Role
from .reconcile import company_key, normalized, title_key
from .sources import Posting

LOG = logging.getLogger(__name__)


def _ratio(correct: int, total: int) -> float | None:
    return correct / total if total else None


def _value(role: Role, field: str):
    value = getattr(role, field)
    if value is None:
        return None
    if field == "title":
        return title_key(value)
    if field == "company":
        return company_key(value)
    if field == "skills":
        return {skill.strip().casefold() for skill in value}
    if isinstance(value, str):
        return normalized(value)
    return value


class Scores:
    def __init__(self):
        self.expected = self.predicted = self.matched = 0
        self.correct: dict[str, int] = defaultdict(int)
        self.stated: dict[str, int] = defaultdict(int)
        self.stated_correct: dict[str, int] = defaultdict(int)

    def add(self, labels: list[Role], predictions: list[Role]) -> None:
        self.expected += len(labels)
        self.predicted += len(predictions)
        # Match one-to-one by title and use city only to disambiguate repeated titles.
        remaining = list(predictions)
        for label in labels:
            matches = [
                i
                for i, predicted in enumerate(remaining)
                if title_key(label.title) == title_key(predicted.title)
            ]
            if len(labels) == len(predictions) == 1:
                matches = [0]
            preferred = [
                i
                for i in matches
                if label.city
                and normalized(label.city) == normalized(remaining[i].city)
            ]
            matched = remaining.pop((preferred or matches)[0]) if matches else None
            self.matched += matched is not None
            for field in FACTS:
                value = _value(label, field)
                equal = matched is not None and value == _value(matched, field)
                self.correct[field] += equal
                if value is not None:
                    self.stated[field] += 1
                    self.stated_correct[field] += equal

    def report(self) -> dict:
        return {
            "roles": {
                "expected": self.expected,
                "predicted": self.predicted,
                "matched": self.matched,
                "precision": _ratio(self.matched, self.predicted),
                "recall": _ratio(self.matched, self.expected),
            },
            "fields": {
                field: {
                    "correct": self.correct[field],
                    "total": self.expected,
                    "accuracy": _ratio(self.correct[field], self.expected),
                    "stated_correct": self.stated_correct[field],
                    "stated_total": self.stated[field],
                    "stated_accuracy": _ratio(
                        self.stated_correct[field], self.stated[field]
                    ),
                }
                for field in FACTS
            },
        }


def evaluate(extractor: Extractor, labels_path: Path, output: Path) -> int:
    examples = json.loads(labels_path.read_text())
    scores = Scores()
    errors = {}
    predictions = []
    for example in examples:
        posting = Posting(**example["posting"])
        labels = [Role.model_validate(role) for role in example["roles"]]
        try:
            predicted = extractor.extract(posting, use_cache=False)
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as error:
            if isinstance(
                error, httpx.HTTPStatusError
            ) and error.response.status_code in {401, 403, 404}:
                raise
            errors[posting.url] = str(error)
            LOG.warning("Evaluation extraction failed for %s: %s", posting.url, error)
            predicted = []
        scores.add(labels, predicted)
        predictions.append(
            {
                "url": posting.url,
                "roles": [role.model_dump(mode="json") for role in predicted],
            }
        )
        LOG.info("Scored %d/%d source postings", len(predictions), len(examples))

    cost = extractor.cost_usd
    report = {
        "model": extractor.model,
        "postings": len(examples),
        "hn_postings": sum(
            example["posting"]["source"].startswith("hn:") for example in examples
        ),
        "errors": errors,
        "metadata_fallbacks": extractor.metadata_fallbacks,
        **scores.report(),
        "cost": {
            "input_tokens": extractor.input_tokens,
            "cached_input_tokens": extractor.cached_tokens,
            "output_tokens": extractor.output_tokens,
            "provider_calls": extractor.calls,
            "estimated_api_cost_usd": cost,
            "estimated_api_cost_per_posting_usd": _ratio(cost, len(examples)),
        },
        "predictions": predictions,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    LOG.info("Wrote evaluation results to %s", output)
    return int(bool(errors))
