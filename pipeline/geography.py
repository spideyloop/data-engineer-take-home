"""Recognize explicit US location evidence and obvious non-US ATS locations."""

import re

# Abbreviations require address syntax. A standalone CA could mean Canada.
US_ADDRESS = re.compile(
    r",\s*(?:AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|"
    r"MS|MO|MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC)\b"
)
US_TERMS = re.compile(
    r"\b(?:U\.S\.|Washington,\s*D\.C\.)(?!\w)"
    r"|\b(?:United States|US|USA|San Francisco|South San Francisco|Seattle|"
    r"New York City|New York|NYC|Mountain View|Palo Alto|Boston|Chicago|Bellevue|Plano|"
    r"Los Angeles|San Diego|Austin|Denver|California)\b"
)
FOREIGN_COUNTRIES = (
    "Australia",
    "Brazil",
    "Canada",
    "China",
    "Costa Rica",
    "England",
    "India",
    "Israel",
    "Japan",
    "Netherlands",
    "Serbia",
    "South Korea",
    "Spain",
    "United Kingdom",
)


def ats_location(text: str) -> str:
    match = re.search(r"^location:\s*[^\r\n]+", text, re.IGNORECASE | re.MULTILINE)
    return match[0] if match else ""


def us_location(text: str) -> bool:
    return bool(US_TERMS.search(text) or US_ADDRESS.search(text))


def clearly_foreign(location: str) -> bool:
    value = location.partition(":")[2].strip()
    if value in {
        "Toronto",
        "Stockholm",
        "Bengaluru",
        "Bangalore",
        "London",
        "Barcelona",
    }:
        return True
    if not value or us_location(value):
        return False
    # Limit the shortcut to the board's country or a single city-and-country.
    parts = value.split(",")
    return (
        len(parts) <= 2
        and not re.search(r"\b(?:and|or)\b|[;•|/]", value, re.IGNORECASE)
        and (parts[-1].strip() in FOREIGN_COUNTRIES)
    )


# A bare place must be a recognized city. Other cities need a US state suffix.
US_CITIES = {
    "Austin",
    "Bellevue",
    "Boston",
    "Chicago",
    "Denver",
    "Los Angeles",
    "Mountain View",
    "New York",
    "New York City",
    "NYC",
    "Palo Alto",
    "Plano",
    "San Diego",
    "San Francisco",
    "Seattle",
    "South San Francisco",
}
STATE_NAMES = {
    "California",
    "Colorado",
    "Illinois",
    "Massachusetts",
    "New York",
    "Texas",
    "Washington",
}


def stated_city(location: str) -> tuple[str, str] | None:
    value = re.sub(r"^location:\s*", "", location, flags=re.IGNORECASE).strip()
    if value in US_CITIES:
        return ("New York City" if value == "NYC" else value), location

    parts = [part.strip() for part in value.split(",")]
    if len(parts) == 3 and parts[-1] in {"US", "USA", "United States"}:
        parts.pop()
    if len(parts) != 2:
        return None
    city, state = parts
    if not re.fullmatch(r"[A-Za-z][A-Za-z .'-]*", city):
        return None
    if re.search(r"\b(?:remote|hybrid|area|region|or|and)\b", city, re.IGNORECASE):
        return None
    if city == "Washington" and state in {"DC", "D.C."}:
        return "Washington, D.C.", location
    if state in STATE_NAMES or US_ADDRESS.fullmatch(", " + state):
        return city, location
    return None
