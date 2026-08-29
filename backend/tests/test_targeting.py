"""Pins the unified targeting evaluator across a canonical case table — one row per scoring path.

`frontend/src/lib/targeting.ts` is a byte-for-byte mirror and MUST produce identical scores. The
case table lives in `shared/targeting-cases.json`, consumed by this test AND the frontend's
`src/lib/targeting.test.ts` — change the scoring model on both sides and update the table once.
"""

import json
from pathlib import Path
from typing import TypedDict, cast

from app.targeting import Targeting, evaluate, reachability

CASES_PATH = Path(__file__).resolve().parents[2] / "shared" / "targeting-cases.json"


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


class SharedCase(TypedDict):
    """One row of shared/targeting-cases.json. Fit is the weighted fraction of *specified*
    criteria matched (0-100), independent of whether we have an email (that's `reachability`)."""

    name: str
    targeting: TargetingCase
    contact: ContactCase
    fit: int


def load_cases() -> list[SharedCase]:
    return cast(list[SharedCase], json.loads(CASES_PATH.read_text()))


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
    cases = load_cases()
    assert cases, "shared/targeting-cases.json is missing or empty"
    for case in cases:
        score, _ = evaluate(_Contact(**case["contact"]), Targeting(**case["targeting"]))
        assert score == case["fit"], f"{case['name']}: got {score}, want {case['fit']}"


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
