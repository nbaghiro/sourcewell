"""Pins the unified targeting evaluator across a canonical case table — one row per scoring path.

`src/lib/targeting.ts` is a byte-for-byte mirror and MUST produce identical scores. When you change
the scoring model, update both sides and these expected values (run the cases through `evaluate`).
"""

from typing import TypedDict

from app.targeting import Targeting, evaluate


class TargetingCase(TypedDict, total=False):
    titles: list[str]
    skills: list[str]
    locations: list[str]
    companies: list[str]
    industries: list[str]
    company_sizes: list[str]
    exclude_companies: list[str]
    exclude_titles: list[str]


class ContactCase(TypedDict, total=False):
    title: str
    skills: list[str]
    location: str
    email: str
    company: str
    industry: str
    company_size: str


# (name, targeting fields, contact fields, expected score)
CASES: list[tuple[str, TargetingCase, ContactCase, int]] = [
    (
        "titles-only exact + email",
        {"titles": ["VP of Sales"]},
        {"title": "SVP of Sales Ops", "email": "a@b.com"},
        100,
    ),
    (
        "skills partial 1of2 + email",
        {"skills": ["Go", "Rust"]},
        {"skills": ["Go"], "email": "a@b.com"},
        55,
    ),
    ("skills 0 + email", {"skills": ["Go", "Rust"]}, {"skills": ["Java"], "email": "a@b.com"}, 10),
    (
        "title+industry+size + email",
        {"titles": ["VP of Sales"], "industries": ["Fintech"], "company_sizes": ["501-1,000"]},
        {
            "title": "VP of Sales",
            "industry": "Fintech",
            "company_size": "501-1,000",
            "email": "a@b.com",
        },
        100,
    ),
    (
        "location EU alias + email",
        {"locations": ["EU"]},
        {"location": "Berlin, DE", "email": "a@b.com"},
        100,
    ),
    ("empty + email", {}, {"email": "a@b.com"}, 10),
    (
        "perfect, no email caps at 90",
        {"titles": ["Engineer"], "skills": ["Go"]},
        {"title": "Staff Engineer", "skills": ["Go"]},
        90,
    ),
    (
        "exclude company disqualifies",
        {"titles": ["VP of Sales"], "exclude_companies": ["Initech"]},
        {"title": "VP of Sales", "company": "Initech", "email": "a@b.com"},
        0,
    ),
    (
        "companies match + email",
        {"companies": ["Globex"]},
        {"company": "Globex Inc", "email": "a@b.com"},
        100,
    ),
    (
        "title-only of title+skills + email",
        {"titles": ["VP"], "skills": ["Go", "Rust"]},
        {"title": "VP Sales", "skills": ["Java"], "email": "a@b.com"},
        55,
    ),
]


class _Contact:
    """A minimal in-memory candidate that structurally satisfies the `Candidate` protocol."""

    title: str | None = None
    skills: list[str] | None = None
    location: str | None = None
    email: str | None = None
    company: str | None = None
    industry: str | None = None
    company_size: str | None = None

    def __init__(self, **kw: object) -> None:
        for f in ("title", "skills", "location", "email", "company", "industry", "company_size"):
            if f in kw:
                setattr(self, f, kw[f])


def test_evaluator_canonical_scores() -> None:
    for name, targeting, contact, expected in CASES:
        score, _ = evaluate(_Contact(**contact), Targeting(**targeting))
        assert score == expected, f"{name}: got {score}, want {expected}"


def test_exclude_overrides_positive_matches() -> None:
    """An exclude match hard-disqualifies even an otherwise-perfect positive match."""
    t = Targeting(titles=["VP of Sales"], exclude_titles=["intern"])
    score, reason = evaluate(_Contact(title="VP of Sales (intern program)", email="a@b.com"), t)
    assert score == 0 and "exclud" in reason
