from pipeline.evaluate import Scores
from pipeline.extract import Role


def test_evaluation_penalizes_missed_and_extra_roles_and_reports_stated_accuracy():
    senior = Role(
        company="Example", title="Senior Data Engineer", country="US", city="Boston"
    )
    staff = Role(
        company="Example", title="Staff ML Engineer", country="US", salary_min=200_000
    )
    observed = Role(
        company="example", title="Sr. Data Engineer", country="US", city="New York"
    )
    extra = Role(company="Example", title="Frontend Engineer", country="US")
    scores = Scores()
    scores.add([senior, staff], [extra, observed])

    report = scores.report()
    assert report["roles"] == {
        "expected": 2,
        "predicted": 2,
        "matched": 1,
        "precision": 0.5,
        "recall": 0.5,
    }
    assert report["fields"]["company"]["accuracy"] == 0.5
    assert report["fields"]["city"]["stated_accuracy"] == 0.0
    assert report["fields"]["salary_min"]["stated_accuracy"] == 0.0
    assert report["fields"]["visa_sponsorship"]["stated_accuracy"] is None


def test_wrong_title_on_single_role_does_not_zero_other_field_scores():
    expected = Role(company="Example", title="Analytics Engineer")
    predicted = Role(company="Example", title="Data Engineer")
    scores = Scores()
    scores.add([expected], [predicted])
    fields = scores.report()["fields"]
    assert fields["title"]["accuracy"] == 0.0
    assert fields["company"]["accuracy"] == 1.0
