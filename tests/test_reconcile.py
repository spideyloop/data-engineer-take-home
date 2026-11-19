import csv
import json

from pipeline.export import export_csv
from pipeline.extract import Role
from pipeline.reconcile import reconcile
from pipeline.sources import Posting, Snapshot
from pipeline.storage import Store

FIRST = "2026-11-17T06:00:00+00:00"
SECOND = "2026-11-17T06:00:01+00:00"
THIRD = "2026-11-17T06:00:02+00:00"


def _ats(key="1", requisition=None):
    return Posting(
        "greenhouse:example",
        key,
        f"https://example.com/jobs/{key}",
        "Example | Sr. Data Engineer | New York",
        "Sr. Data Engineer",
        requisition,
    )


def _hn(key="10", source="hn:100"):
    return Posting(source, key, f"https://news.ycombinator.com/item?id={key}", "hiring")


def _role(title="Senior Data Engineer", **facts):
    return Role(
        company="Example",
        title=title,
        country="US",
        **({"city": "New York", "seniority": "senior"} | facts),
    )


def _export(store, tmp_path):
    out = tmp_path / "jobs.csv"
    export_csv(store, out)
    with out.open() as file:
        return list(csv.DictReader(file))


def test_title_variants_merge_and_rerun_keeps_id_links_and_history(tmp_path):
    ats, hn = _ats(), _hn()
    snapshots = [Snapshot(ats.source, [ats]), Snapshot(hn.source, [hn])]
    observations = [(ats, [_role()]), (hn, [_role("Senior Data Engineer, Platform")])]

    with Store(tmp_path / "jobs.sqlite") as store:
        reconcile(store, snapshots, observations, FIRST)
        (first,) = _export(store, tmp_path)
        reconcile(store, snapshots, observations, SECOND)
        (second,) = _export(store, tmp_path)
        assert second["posting_id"] == first["posting_id"]
        assert second["first_seen"] == FIRST
        assert second["last_seen"] == SECOND
        assert json.loads(second["source_urls"]) == [ats.url, hn.url]
        assert second["title"] == "Senior Data Engineer"
        assert store.db.execute("SELECT COUNT(*) FROM changes").fetchone()[0] == 0


def test_same_board_distinct_requisitions_make_hn_title_match_ambiguous(tmp_path):
    first, second, hn = _ats("1", "R1"), _ats("2", "R2"), _hn()
    with Store(tmp_path / "jobs.sqlite") as store:
        reconcile(store, [], [(p, [_role()]) for p in (first, second, hn)], FIRST)
        assert len(_export(store, tmp_path)) == 3


def test_refresh_failure_does_not_close_and_complete_omission_records_changes(tmp_path):
    ats = _ats()
    with Store(tmp_path / "jobs.sqlite") as store:
        reconcile(
            store,
            [Snapshot(ats.source, [ats])],
            [(ats, [_role(salary_min=150000, salary_max=210000)])],
            FIRST,
        )
        reconcile(store, [], [], SECOND)
        assert _export(store, tmp_path)[0]["status"] == "open"
        reconcile(
            store,
            [Snapshot(ats.source, [ats])],
            [(ats, [_role(city="Boston", salary_min=160000, salary_max=210000)])],
            THIRD,
        )
        reconcile(store, [Snapshot(ats.source, [])], [], "2026-11-17T06:00:03+00:00")
        reconcile(store, [Snapshot(ats.source, [])], [], "2026-11-17T06:00:04+00:00")
        (row,) = _export(store, tmp_path)
        assert row["status"] == "closed"
        assert row["last_seen"] == THIRD
        assert set(
            map(
                tuple,
                store.db.execute("SELECT field, old_value, new_value FROM changes"),
            )
        ) == {
            ("city", '"New York"', '"Boston"'),
            ("salary_min", "150000.0", "160000.0"),
            ("status", '"open"', '"closed"'),
        }


def test_hn_month_rollover_preserves_open_status(tmp_path):
    old, new = _hn(), _hn("20", "hn:200")
    with Store(tmp_path / "jobs.sqlite") as store:
        reconcile(store, [Snapshot(old.source, [old])], [(old, [_role()])], FIRST)
        reconcile(store, [Snapshot(new.source, [new])], [], SECOND)
        assert _export(store, tmp_path)[0]["status"] == "open"
        reconcile(
            store,
            [Snapshot(old.source, [old])],
            [(old, [_role(closure_evidence="Position filled")])],
            THIRD,
        )
        assert _export(store, tmp_path)[0]["status"] == "closed"


def test_shared_requisition_placeholder_and_generic_application_url_keep_jobs_separate(
    tmp_path,
):
    first = _ats("1", "See Opening ID")
    second = _ats("2", "See Opening ID")
    hn = _hn()
    shared_url = "https://example.com/careers"
    with Store(tmp_path / "jobs.sqlite") as store:
        reconcile(
            store,
            [],
            [
                (first, [_role(application_url=shared_url)]),
                (second, [_role("Platform Engineer", application_url=shared_url)]),
                (hn, [_role("ML Engineer", application_url=shared_url)]),
            ],
            FIRST,
        )
        assert len(_export(store, tmp_path)) == 3


def test_reordered_same_title_hn_roles_preserve_ids_and_locations(tmp_path):
    posting = _hn()
    ny, la = _role(city="New York"), _role(city="Los Angeles")
    with Store(tmp_path / "jobs.sqlite") as store:
        reconcile(store, [], [(posting, [ny, la])], FIRST)
        first = {row["city"]: row["posting_id"] for row in _export(store, tmp_path)}
        reconcile(store, [], [(posting, [la, ny])], SECOND)
        assert {
            row["city"]: row["posting_id"] for row in _export(store, tmp_path)
        } == first
        assert store.db.execute("SELECT COUNT(*) FROM changes").fetchone()[0] == 0
