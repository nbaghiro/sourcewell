"""Pins the unified targeting evaluator across a canonical case table — one row per scoring path.

`src/lib/targeting.ts` is a byte-for-byte mirror and MUST produce identical scores. When you change
the scoring model, update both sides and these expected values (run the cases through `evaluate`).
"""

from typing import TypedDict

from app.targeting import Targeting, evaluate, reachability


class TargetingCase(TypedDict, total=False):
    titles: list[str]
    skills: list[str]
    locations: list[str]
    companies: list[str]
    industries: list[str]
    company_sizes: list[str]
    seniorities: list[str]
    functions: list[str]
    technologies: list[str]
    keywords: str
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
    seniority: str
    function: str


# Fit is now the weighted fraction of *specified* criteria matched (0-100), independent of whether
# we have an email (that's `reachability`). (name, targeting, contact, expected fit)
CASES: list[tuple[str, TargetingCase, ContactCase, int]] = [
    (
        "titles substring (SVP ⊇ VP)",
        {"titles": ["VP of Sales"]},
        {"title": "SVP of Sales Ops"},
        100,
    ),
    ("skills partial 1of2", {"skills": ["Go", "Rust"]}, {"skills": ["Go"]}, 50),
    ("skills 0", {"skills": ["Go", "Rust"]}, {"skills": ["Java"]}, 0),
    (
        "title+industry+size all match",
        {"titles": ["VP of Sales"], "industries": ["Fintech"], "company_sizes": ["501-1,000"]},
        {"title": "VP of Sales", "industry": "Fintech", "company_size": "501-1,000"},
        100,
    ),
    ("location EU alias", {"locations": ["EU"]}, {"location": "Berlin, DE"}, 100),
    ("empty audience → 0", {}, {"email": "a@b.com"}, 0),
    (
        "perfect match → 100 (email doesn't matter)",
        {"titles": ["Engineer"], "skills": ["Go"]},
        {"title": "Staff Engineer", "skills": ["Go"]},
        100,
    ),
    (
        "exclude company disqualifies",
        {"titles": ["VP of Sales"], "exclude_companies": ["Initech"]},
        {"title": "VP of Sales", "company": "Initech"},
        0,
    ),
    ("companies substring", {"companies": ["Globex"]}, {"company": "Globex Inc"}, 100),
    (
        "title match, skills miss (of two fields)",
        {"titles": ["VP"], "skills": ["Go", "Rust"]},
        {"title": "VP Sales", "skills": ["Java"]},
        50,
    ),
    # --- seniority / function are now scored (were dropped before) ---
    ("seniority match", {"seniorities": ["VP"]}, {"seniority": "vp"}, 100),
    (
        "seniority synonym (Vice President≡SVP)",
        {"seniorities": ["Vice President"]},
        {"seniority": "svp"},
        100,
    ),
    ("function match", {"functions": ["Engineering"]}, {"function": "engineering"}, 100),
    (
        "title + seniority, both match",
        {"titles": ["Engineer"], "seniorities": ["senior"]},
        {"title": "Staff Engineer", "seniority": "senior"},
        100,
    ),
    # --- word-boundary excludes: 'intern' must NOT exclude 'International' ---
    (
        "intern does not exclude International",
        {"titles": ["Sales"], "exclude_titles": ["intern"]},
        {"title": "International Sales Director"},
        100,
    ),
    # --- search-only audience gets a floor instead of a hard 0 ---
    ("keywords-only → floor", {"keywords": "fintech"}, {"title": "Anyone"}, 50),
    ("technologies-only → floor", {"technologies": ["React"]}, {}, 50),
]


class _Contact:
    """A minimal in-memory candidate that structurally satisfies the `Candidate` protocol."""

    title: str | None = None
    skills: list[str] | None = None
    location: str | None = None
    email: str | None = None
    email_status: str | None = None
    linkedin_url: str | None = None
    company: str | None = None
    industry: str | None = None
    company_size: str | None = None
    seniority: str | None = None
    function: str | None = None

    _FIELDS = (
        "title",
        "skills",
        "location",
        "email",
        "email_status",
        "linkedin_url",
        "company",
        "industry",
        "company_size",
        "seniority",
        "function",
    )

    def __init__(self, **kw: object) -> None:
        for f in self._FIELDS:
            if f in kw:
                setattr(self, f, kw[f])


def test_evaluator_canonical_scores() -> None:
    for name, targeting, contact, expected in CASES:
        score, _ = evaluate(_Contact(**contact), Targeting(**targeting))
        assert score == expected, f"{name}: got {score}, want {expected}"


def test_exclude_overrides_positive_matches() -> None:
    """An exclude match hard-disqualifies even an otherwise-perfect positive match."""
    t = Targeting(titles=["VP of Sales"], exclude_titles=["intern"])
    score, reason = evaluate(_Contact(title="VP of Sales (intern program)"), t)
    assert score == 0 and "exclud" in reason


def test_reachability_is_separate_from_fit() -> None:
    assert reachability(_Contact(email="a@b.com", email_status="valid")) == "verified"
    assert reachability(_Contact(email="a@b.com")) == "reachable"
    assert reachability(_Contact(linkedin_url="https://linkedin.com/in/x")) == "reachable"
    assert reachability(_Contact()) == "needs_enrichment"
