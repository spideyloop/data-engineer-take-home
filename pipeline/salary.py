"""Normalize explicitly advertised compensation without mixing pay ranges."""

import re
from dataclasses import dataclass

AMOUNT = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?[kK]?"
RANGE = re.compile(
    rf"(?<![\w.,])(?P<left>\$?\s*{AMOUNT})\s*(?:[-–—]|\bto\b)\s*"
    rf"(?P<right>\$?\s*{AMOUNT})(?![\w,])"
    r"(?P<suffix>\s*(?:USD|CAD|EUR|GBP)\b)?"
    r"(?P<period>\s*(?:/\s*|per\s+)(?:year|yr|annum|month|mo|hour|hr|week)\b)?",
    re.IGNORECASE,
)
PAY_LABEL = re.compile(
    r"\b(?:(?:base\s+)?salary(?:\s+range)?|(?:base\s+)?pay range|base compensation)"
    r"\s*(?:(?:for\s+[^.!?]{1,55}?)?\s+(?:is|of)(?:\s+(?:estimated\s+to\s+be|between))?)?"
    r"\s*[:=]?\s*$",
    re.IGNORECASE,
)
STRUCTURED = re.compile(
    rf"^Salary: (?P<currency>[A-Z]{{3}}) (?P<left>{AMOUNT}) (?P<right>{AMOUNT}) "
    r"(?P<period>per-year|per-month|per-hour|per-week)$",
    re.MULTILINE,
)
NON_BASE = re.compile(
    r"\b(?:bonus|equity|commission|stipend|total compensation|ote)\s*[:=]?\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Pay:
    bounds: tuple[float, float] | None
    currency: str | None
    evidence: str


def _amount(value: str) -> float:
    cleaned = value.strip().removeprefix("$").strip().replace(",", "").lower()
    return float(cleaned.removesuffix("k")) * (1000 if cleaned.endswith("k") else 1)


def _pay(
    match: re.Match,
    currency: str | None,
    period: str,
    quote: str,
    *,
    annual_context: bool,
) -> Pay:
    lower, upper = _amount(match["left"]), _amount(match["right"])
    normalized = (
        period.lower().strip().removeprefix("/").removeprefix("per").strip(" -")
    )
    factor = {"year": 1, "yr": 1, "annum": 1, "month": 12, "mo": 12}.get(normalized)
    if not normalized and annual_context and lower >= 20_000:
        factor = 1
    bounds = (lower * factor, upper * factor) if factor and lower <= upper else None
    return Pay(bounds, currency, quote)


def stated_pay(text: str, *, full_time_heading: bool = False) -> Pay | None:
    if structured := STRUCTURED.search(text):
        return _pay(
            structured,
            structured["currency"],
            structured["period"],
            structured[0],
            annual_context=False,
        )

    candidates = []
    for match in RANGE.finditer(text):
        before = text[max(0, match.start() - 95) : match.start()]
        label = PAY_LABEL.search(before)
        currency = (match["suffix"] or "").strip().upper() or None
        period = match["period"] or ""
        if not (label or full_time_heading or (period and "$" in match[0])):
            continue
        if NON_BASE.search(before):
            continue
        if not (currency or "$" in match[0] or "k" in match[0].lower()):
            continue
        quote = ((label[0] if label else "") + match[0]).strip()
        candidates.append(
            _pay(
                match,
                currency,
                period,
                quote,
                annual_context=bool(label or full_time_heading),
            )
        )

    if not candidates:
        return None
    options = {(pay.bounds, pay.currency) for pay in candidates}
    if len(options) > 1:
        return Pay(None, None, " … ".join(pay.evidence for pay in candidates))
    return candidates[0]
