import json

import httpx
import pytest

from pipeline.extract import Extractor
from pipeline.sources import Posting
from pipeline.storage import Store


def _posting():
    return Posting(
        "lever:example",
        "42",
        "https://example.com/jobs/42",
        "Example | Data Engineer | Remote (US)",
        "Data Engineer",
    )


def _response(payload):
    return httpx.Response(
        200,
        json={
            "choices": [
                {"message": {"content": json.dumps(payload)}, "finish_reason": "stop"}
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 60},
        },
    )


def _role():
    return {
        "company": "Example",
        "title": "Data Engineer",
        "country": "US",
        "remote_policy": "remote",
        "evidence": {
            "company": "Example",
            "title": "Data Engineer",
            "country": "Remote (US)",
            "remote_policy": "Remote (US)",
        },
    }


def test_extraction_is_cached_and_unstated_fields_are_null(tmp_path):
    requests = []

    def respond(request):
        requests.append(request)
        return _response({"roles": [_role()]})

    with (
        Store(tmp_path / "run.sqlite") as store,
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
    ):
        extractor = Extractor(store, client, "test-key")
        first = extractor.extract(_posting())
        second = extractor.extract(_posting())
        assert first == second
        assert len(requests) == 1
        assert first[0].salary_min is None
        assert first[0].evidence["country"] == "Remote (US)"
        assert extractor.cost_usd == pytest.approx(0.000095)


def test_missing_or_invented_evidence_cannot_populate_a_field(tmp_path):
    role = _role()
    role.update(salary_min=120_000, seniority="senior")
    role["evidence"]["salary_min"] = "$120k"

    with (
        Store(tmp_path / "run.sqlite") as store,
        httpx.Client(
            transport=httpx.MockTransport(lambda request: _response({"roles": [role]}))
        ) as client,
    ):
        extracted = Extractor(store, client, "test-key").extract(_posting())[0]
        assert extracted.salary_min is None
        assert extracted.seniority is None
        assert "salary_min" not in extracted.evidence


def test_hn_does_not_accept_url_prefix_or_open_statement_as_closure(tmp_path):
    posting = Posting(
        "hn:100",
        "42",
        "https://news.ycombinator.com/item?id=42",
        "Example | Data Engineer | Remote (US)\n"
        "https://example.com/careers/jobs/12\n"
        "Employer follow-up: The role is still open.",
    )
    role = _role()
    role.update(
        application_url="https://example.com/careers",
        closure_evidence="The role is still open.",
    )
    with (
        Store(tmp_path / "run.sqlite") as store,
        httpx.Client(
            transport=httpx.MockTransport(lambda request: _response({"roles": [role]}))
        ) as client,
    ):
        (extracted,) = Extractor(store, client, "test-key").extract(posting)
        assert extracted.application_url is None
        assert extracted.closure_evidence is None


def test_ats_location_supplies_country_evidence_for_a_known_us_city(tmp_path):
    posting = Posting(
        "greenhouse:stripe",
        "42",
        "https://example.com/jobs/42",
        "Company: Stripe\nTitle: Data Engineer\nLocation: San Francisco",
        "Data Engineer",
    )
    output = {
        "roles": [
            {
                "company": "Stripe",
                "title": "Data Engineer",
                "country": "US",
                "evidence": {"company": "Stripe", "title": "Data Engineer"},
            }
        ]
    }
    with (
        Store(tmp_path / "run.sqlite") as store,
        httpx.Client(
            transport=httpx.MockTransport(lambda request: _response(output))
        ) as client,
    ):
        (extracted,) = Extractor(store, client, "test-key").extract(posting)
        assert extracted.country == "US"
        assert extracted.evidence["country"] == "Location: San Francisco"


def test_structured_ats_facts_keep_their_exact_original_evidence(tmp_path):
    posting = Posting(
        "lever:example",
        "42",
        "https://example.com/jobs/42",
        "Company: Example\nTitle: Senior Staff Data Engineer\nlocation: New York, NY\n"
        "commitment: Full-time\nWorkplace type: hybrid",
        "Senior Staff Data Engineer",
    )
    output = {"roles": [{"company": "Example", "title": "Senior Staff Data Engineer"}]}
    with (
        Store(tmp_path / "run.sqlite") as store,
        httpx.Client(
            transport=httpx.MockTransport(lambda request: _response(output))
        ) as client,
    ):
        (extracted,) = Extractor(store, client, "test-key").extract(posting)
        assert (
            extracted.seniority,
            extracted.employment_type,
            extracted.remote_policy,
        ) == ("staff_plus", "full_time", "hybrid")
        assert extracted.evidence["seniority"] == "Senior Staff Data Engineer"
        assert extracted.evidence["remote_policy"] == "Workplace type: hybrid"
        assert extracted.evidence["employment_type"] == "commitment: Full-time"


def test_mixed_hn_comment_cannot_export_an_out_of_scope_role(tmp_path):
    posting = Posting(
        "hn:100",
        "42",
        "https://news.ycombinator.com/item?id=42",
        "Example | Data Engineer or Mechatronics Engineer | Remote (US)",
    )
    off_scope = _role()
    off_scope["title"] = "Mechatronics Engineer"
    off_scope["evidence"]["title"] = off_scope["title"]
    with (
        Store(tmp_path / "run.sqlite") as store,
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: _response({"roles": [_role(), off_scope]})
            )
        ) as client,
    ):
        extracted = Extractor(store, client, "test-key").extract(posting)
        assert [role.title for role in extracted] == ["Data Engineer"]


def test_foreign_city_is_not_evidence_of_us_eligibility(tmp_path):
    posting = Posting(
        "greenhouse:stripe",
        "42",
        "https://example.com/jobs/42",
        "Company: Stripe\nTitle: Data Engineer\nLocation: Toronto",
        "Data Engineer",
    )
    output = {
        "roles": [
            {
                "company": "Stripe",
                "title": "Data Engineer",
                "country": "US",
                "evidence": {"country": "Toronto"},
            }
        ]
    }
    with (
        Store(tmp_path / "run.sqlite") as store,
        httpx.Client(
            transport=httpx.MockTransport(lambda request: _response(output))
        ) as client,
    ):
        assert Extractor(store, client, "test-key").extract(posting) == []


def test_incomplete_ats_response_can_use_grounded_city_and_pay(tmp_path):
    posting = Posting(
        "lever:example",
        "42",
        "https://example.com/jobs/42",
        "Company: Example\nTitle: Data Engineer\nlocation: Palo Alto, CA\n"
        "The salary range is $100,000 - $145,000/year.",
        "Data Engineer",
    )
    response = _response({"roles": []}).json()
    response["choices"][0]["finish_reason"] = "length"
    with (
        Store(tmp_path / "run.sqlite") as store,
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=response)
            )
        ) as client,
    ):
        extractor = Extractor(store, client, "test-key")
        (role,) = extractor.extract(posting)
        assert (role.city, role.salary_min, role.salary_max) == (
            "Palo Alto",
            100_000,
            145_000,
        )
        assert role.evidence["city"] == "location: Palo Alto, CA"
        assert "incomplete extraction" in extractor.metadata_fallbacks[posting.url]
        assert store.db.execute("SELECT COUNT(*) FROM extractions").fetchone()[0] == 0


def test_ats_keeps_the_single_role_matching_the_listing_title(tmp_path):
    posting = Posting(
        "lever:example",
        "42",
        "https://example.com/jobs/42",
        "Company: Example\nTitle: Data Engineer\nlocation: Washington, D.C.\n"
        "Python experience required.",
        "Data Engineer",
    )
    role = {**_role(), "skills": ["Python"], "evidence": {"skills": "Python"}}
    with (
        Store(tmp_path / "run.sqlite") as store,
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: _response({"roles": [{}, role]})
            )
        ) as client,
    ):
        extractor = Extractor(store, client, "test-key")
        (extracted,) = extractor.extract(posting)
        assert (
            extracted.title,
            extracted.city,
            extracted.country,
            extracted.skills,
        ) == (
            "Data Engineer",
            "Washington, D.C.",
            "US",
            ["Python"],
        )
        assert extractor.extract(posting) == [extracted]
        assert extractor.calls == 1


def test_truncated_hn_extraction_retries_once_and_caches_valid_result(tmp_path):
    posting = Posting(
        "hn:100",
        "42",
        "https://news.ycombinator.com/item?id=42",
        "Example | Data Engineer | Remote (US)",
        "Data Engineer",
    )
    requests = []

    def respond(request):
        requests.append(json.loads(request.content))
        if len(requests) == 1:
            response = _response({"roles": []}).json()
            response["choices"][0]["finish_reason"] = "length"
            response["choices"][0]["message"]["content"] = '{"roles":['
            return httpx.Response(200, json=response)
        return _response({"roles": [_role()]})

    with (
        Store(tmp_path / "run.sqlite") as store,
        httpx.Client(transport=httpx.MockTransport(respond)) as client,
    ):
        extractor = Extractor(store, client, "test-key", ollama=True)
        (role,) = extractor.extract(posting)
        assert role.title == "Data Engineer"
        assert extractor.extract(posting) == [role]
        assert extractor.calls == 2
        assert extractor.output_tokens == 120
        assert requests[1]["response_format"] == {"type": "json_object"}
