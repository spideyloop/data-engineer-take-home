"""Link source observations and maintain canonical records in one transaction."""

import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from uuid import uuid4

from .extract import FACTS, Role
from .sources import Posting, Snapshot
from .storage import Store, json_text

SALARY = ("salary_min", "salary_max", "currency")
LOCATION = ("city", "country", "remote_policy")
TRACKED = (*SALARY, *LOCATION, "status")


def normalized(text: str | None) -> str:
    return " ".join(re.findall(r"[^\W_]+", (text or "").casefold()))


def company_key(name: str | None) -> str:
    return re.sub(
        r" (?:inc|incorporated|llc|ltd|corp|corporation)$", "", normalized(name)
    )


def title_key(title: str | None) -> str:
    words = normalized(title).split()
    replacements = {"sr": "senior", "jr": "junior", "eng": "engineer"}
    words = [replacements.get(word, word) for word in words]
    return " ".join(words).replace("machine learning", "ml")


def job_url(url: str | None) -> str | None:
    if not url:
        return None
    parts = urlsplit(url)
    # Some boards encode the actual job ID as a query parameter.
    query = sorted(
        (key, value)
        for key, value in parse_qsl(parts.query)
        if not key.startswith("utm_") and key not in {"gh_src", "lever-source"}
    )
    return urlunsplit(
        ("https", parts.netloc.lower(), parts.path.rstrip("/"), urlencode(query), "")
    )


@dataclass(frozen=True)
class Listing:
    source: str
    listing_key: str
    role_key: str
    posting_id: str
    url: str
    requisition: str | None
    payload: str
    status: str
    seen_at: str

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.source, self.listing_key, self.role_key

    @property
    def ats(self) -> bool:
        return not self.source.startswith("hn:")


def _same_title(role: Role, other: Role) -> bool:
    if role.seniority and other.seniority and role.seniority != other.seniority:
        return False
    left, right = title_key(role.title), title_key(other.title)
    if left == right:
        return True
    a, b = set(left.split()), set(right.split())
    return a ^ b == {"platform"} and {"data", "engineer"} <= a & b


def _match_score(
    posting: Posting, role: Role, candidate: Listing, previous: Role
) -> int:
    if candidate.source == posting.source:
        return 0
    incoming_url = (
        job_url(posting.url) if posting.is_ats else job_url(role.application_url)
    )
    previous_url = (
        job_url(candidate.url) if candidate.ats else job_url(previous.application_url)
    )
    if (
        incoming_url
        and incoming_url == previous_url
        and (posting.is_ats or candidate.ats)
    ):
        return 2
    if candidate.status == "closed" or not _same_title(role, previous):
        return 0
    if posting.requisition and candidate.requisition:
        requisition = str(posting.requisition).casefold()
        if requisition != candidate.requisition.casefold():
            return 0
        if re.fullmatch(r"[a-z0-9_-]*[0-9][a-z0-9_-]*", requisition):
            return 2
    if not posting.is_ats and not candidate.ats:
        return 0
    if (
        role.remote_policy
        and previous.remote_policy
        and role.remote_policy != previous.remote_policy
    ):
        return 0
    if role.city and previous.city:
        return int(normalized(role.city) == normalized(previous.city))
    return int(role.remote_policy == previous.remote_policy == "remote")


def _find_match(
    posting: Posting, role: Role, candidates: list[tuple[Listing, Role]]
) -> str | None:
    scores: dict[str, int] = {}
    same_board = {
        listing.posting_id
        for listing, _ in candidates
        if listing.source == posting.source
    }
    for listing, previous in candidates:
        score = _match_score(posting, role, listing, previous)
        if listing.posting_id in same_board:
            continue
        if score:
            scores[listing.posting_id] = max(score, scores.get(listing.posting_id, 0))
    if not scores:
        return None
    best = max(scores.values())
    matches = [posting_id for posting_id, score in scores.items() if score == best]
    return matches[0] if len(matches) == 1 else None


def _role_key(posting: Posting, role: Role) -> str:
    if posting.is_ats:
        return ""
    return f"{company_key(role.company)}:{title_key(role.title)}"


def reconcile(
    store: Store,
    snapshots: list[Snapshot],
    observations: list[tuple[Posting, list[Role]]],
    observed_at: str,
) -> None:
    db = store.db
    present = {
        snapshot.source: {p.listing_id for p in snapshot.postings}
        for snapshot in snapshots
    }
    with db:
        db.execute("BEGIN IMMEDIATE")
        existing = [Listing(**row) for row in db.execute("SELECT * FROM listings")]
        identities = {listing.identity: listing for listing in existing}
        companies = defaultdict(dict)
        for listing in existing:
            previous = Role.model_validate_json(listing.payload)
            companies[company_key(previous.company)][listing.identity] = (
                listing,
                previous,
            )

        touched = set()
        for posting, roles in sorted(observations, key=lambda item: not item[0].is_ats):
            occurrences: Counter[str] = Counter()
            keys = Counter(_role_key(posting, role) for role in roles)
            for role in roles:
                base_key = _role_key(posting, role)
                if keys[base_key] > 1:
                    location = normalized(role.city) or role.remote_policy or ""
                    base_key += ":" + location
                occurrences[base_key] += 1
                role_key = (
                    base_key
                    if occurrences[base_key] == 1
                    else f"{base_key}#{occurrences[base_key]}"
                )
                identity = (posting.source, posting.listing_id, role_key)
                old = identities.get(identity)
                candidates = companies[company_key(role.company)]
                if old:
                    posting_id = old.posting_id
                    old_company = company_key(
                        Role.model_validate_json(old.payload).company
                    )
                    companies[old_company].pop(identity, None)
                else:
                    posting_id = _find_match(posting, role, list(candidates.values()))
                if posting_id is None:
                    posting_id = uuid4().hex
                    db.execute(
                        "INSERT INTO jobs VALUES (?, '{}', ?, ?)",
                        (posting_id, observed_at, observed_at),
                    )

                status = (
                    "closed" if role.closure_evidence and not posting.is_ats else "open"
                )
                listing = Listing(
                    *identity,
                    posting_id,
                    posting.url,
                    str(posting.requisition) if posting.requisition else None,
                    role.model_dump_json(),
                    status,
                    observed_at,
                )
                db.execute(
                    """INSERT INTO listings VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                       ON CONFLICT (source, listing_key, role_key) DO UPDATE SET
                         url = excluded.url, requisition = excluded.requisition,
                         payload = excluded.payload, status = excluded.status, seen_at = excluded.seen_at""",
                    (
                        listing.source,
                        listing.listing_key,
                        listing.role_key,
                        listing.posting_id,
                        listing.url,
                        listing.requisition,
                        listing.payload,
                        listing.status,
                        listing.seen_at,
                    ),
                )
                identities[identity] = listing
                candidates[identity] = (listing, role)
                touched.add(posting_id)

        for listing in existing:
            if not listing.ats or listing.source not in present:
                continue
            if listing.listing_key in present[listing.source]:
                # The source still shows this ID even if extraction failed this time.
                db.execute(
                    "UPDATE listings SET status = 'open', seen_at = ? WHERE source = ? AND listing_key = ?",
                    (observed_at, listing.source, listing.listing_key),
                )
            elif listing.status == "open":
                db.execute(
                    "UPDATE listings SET status = 'closed' WHERE source = ? AND listing_key = ?",
                    (listing.source, listing.listing_key),
                )
            else:
                continue
            touched.add(listing.posting_id)

        for posting_id in sorted(touched):
            _write_canonical(store, posting_id, observed_at)


def _write_canonical(store: Store, posting_id: str, observed_at: str) -> None:
    db = store.db
    rows = [
        Listing(**row)
        for row in db.execute(
            "SELECT * FROM listings WHERE posting_id = ? ORDER BY source, listing_key, role_key",
            (posting_id,),
        )
    ]
    active = [row for row in rows if row.status == "open"]
    ranked = sorted(
        active or rows, key=lambda row: (row.ats, row.seen_at), reverse=True
    )
    roles = [Role.model_validate_json(row.payload) for row in ranked]
    old = json.loads(
        db.execute(
            "SELECT payload FROM jobs WHERE posting_id = ?", (posting_id,)
        ).fetchone()["payload"]
    )
    if not active and old:
        # A withdrawal changes availability, not the last known job details.
        payload = old.copy()
        payload["evidence"] = dict(old["evidence"])
    else:
        payload = dict.fromkeys(FACTS)
        evidence = {}
        for fields in [
            LOCATION,
            SALARY,
            *((field,) for field in FACTS if field not in LOCATION + SALARY),
        ]:
            choice = next(
                (
                    role
                    for role in roles
                    if any(getattr(role, field) is not None for field in fields)
                ),
                roles[0],
            )
            for field in fields:
                value = getattr(choice, field)
                payload[field] = (
                    value.isoformat() if field == "posted_date" and value else value
                )
                if field in choice.evidence:
                    evidence[field] = choice.evidence[field]
        if context := next(
            (
                role.evidence["salary_context"]
                for role in roles
                if "salary_context" in role.evidence
            ),
            None,
        ):
            evidence["salary_context"] = context
        payload["evidence"] = evidence

    payload["status"] = "open" if active else "closed"
    if not active and (
        closure := next(
            (role.closure_evidence for role in roles if role.closure_evidence), None
        )
    ):
        payload["evidence"]["status"] = closure
    payload["source_urls"] = sorted(
        {row.url for row in rows}
        | {url for row in rows if (url := json.loads(row.payload)["application_url"])}
        | set(old.get("source_urls", []))
    )

    for field in TRACKED:
        if old and old.get(field) != payload[field]:
            db.execute(
                "INSERT INTO changes (posting_id, field, old_value, new_value, changed_at) VALUES (?, ?, ?, ?, ?)",
                (
                    posting_id,
                    field,
                    json_text(old.get(field)),
                    json_text(payload[field]),
                    observed_at,
                ),
            )
    db.execute(
        "UPDATE jobs SET payload = ?, last_seen = ? WHERE posting_id = ?",
        (json_text(payload), max(row.seen_at for row in rows), posting_id),
    )
