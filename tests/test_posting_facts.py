from pipeline.geography import stated_city
from pipeline.salary import stated_pay


def test_single_ats_city_keeps_original_evidence():
    assert stated_city("Location: New York, NY") == (
        "New York",
        "Location: New York, NY",
    )
    assert stated_city("Location: San Francisco") == (
        "San Francisco",
        "Location: San Francisco",
    )
    assert stated_city("Location: San Francisco, CA • New York, NY") is None
    assert stated_city("Location: Remote, US") is None


def test_salary_normalizes_a_unique_range_and_preserves_currency_uncertainty():
    text = "The salary range is $100,000 - $145,000/year. Equity is separate."
    result = stated_pay(text)
    assert result.bounds == (100_000, 145_000)
    assert result.currency is None
    assert result.evidence == "salary range is $100,000 - $145,000/year"
    assert stated_pay("Pay Range $191,000 — $223,000 USD").currency == "USD"


def test_conflicting_ranges_and_hourly_rates_do_not_create_annual_salary():
    assert (
        stated_pay(
            "Pay range $100,000–$150,000 USD. Pay range $120,000–$160,000 USD"
        ).bounds
        is None
    )
    assert stated_pay("Pay range $46–$46 USD per hour").bounds is None
    assert stated_pay("Pay range $46–$46 USD per hour").currency == "USD"
