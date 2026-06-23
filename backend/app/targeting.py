"""Unified targeting — ONE audience/search definition, used two ways across the whole app.

A `Targeting` is "the kind of person we're after." It is consumed by exactly two operations:
  * search — each provider adapter maps it onto its own query DSL (Find People / discovery).
  * score  — `evaluate()` measures how well a Contact (or a provider hit) fits it. This drives
             campaign ranking, the audience estimate, single enroll, and Find People result scores.

It is stored on `Campaign.criteria` (the audience) and posted to `/people/search` (the search), so
the two are the same object. The frontend mirrors `evaluate()` byte-for-byte in
`src/lib/targeting.ts` (so the composer's live estimate agrees with what server-side ranking
produces) — keep them in lockstep; `tests/test_targeting.py` pins the canonical cases.

Scoring model: each *specified, scorable* field contributes its weight proportionally to a 90-point
budget; a reachable email adds the final 10; capped at 100; a contact is "in the audience" at
`>= FIT_THRESHOLD`. An exclude match hard-disqualifies (score 0). The fields marked search-only
(`seniorities / functions / technologies / keywords`) narrow which people a provider returns but
are NOT scored — a stored Contact doesn't carry them.
"""

from typing import Protocol

from pydantic import BaseModel

from app.core.types import JsonObject

FIT_THRESHOLD = 40


class Targeting(BaseModel):
    """Normalized targeting spec. Each adapter maps this onto its own query language; `evaluate`
    scores a Contact against it. Stored as `Campaign.criteria`; the search request extends it."""

    # --- person ---
    titles: list[str] = []
    seniorities: list[str] = []  # search-only
    functions: list[str] = []  # search-only (department)
    skills: list[str] = []
    locations: list[str] = []
    # --- company ---
    companies: list[str] = []
    industries: list[str] = []
    company_sizes: list[str] = []
    technologies: list[str] = []  # search-only
    # --- free text ---
    keywords: str | None = None  # search-only
    # --- negative targeting ---
    exclude_companies: list[str] = []
    exclude_titles: list[str] = []


class Candidate(Protocol):
    """Anything scorable — a persisted Contact or a live provider hit. Read-only attributes."""

    @property
    def title(self) -> str | None: ...
    @property
    def skills(self) -> list[str] | None: ...
    @property
    def location(self) -> str | None: ...
    @property
    def email(self) -> str | None: ...
    @property
    def company(self) -> str | None: ...
    @property
    def industry(self) -> str | None: ...
    @property
    def company_size(self) -> str | None: ...


# Relative weight per *scorable* field — only fields the user actually specified share the 90-pt
# budget (so a one-field audience still scores out of 90). Keep in sync with src/lib/targeting.ts.
WEIGHTS: dict[str, int] = {
    "titles": 30,
    "skills": 30,
    "companies": 20,
    "industries": 15,
    "locations": 15,
    "company_sizes": 10,
}

# Region shorthands so "EU" matches "Berlin, DE", "London, UK", etc.
REGION_ALIASES: dict[str, list[str]] = {
    "eu": ["de", "uk", "nl", "pt", "ie", "fr", "es", "it", "remote · eu"],
    "us": ["us", "usa", "united states"],
    "remote": ["remote"],
}


def as_targeting(x: "Targeting | JsonObject | None") -> Targeting:
    """Coerce a stored criteria dict (or None) into a Targeting. Extra/legacy keys are ignored."""
    return x if isinstance(x, Targeting) else Targeting.model_validate(x or {})


def _contains_any(value: str | None, needles: list[str]) -> bool:
    """True if any needle is a case-insensitive substring of value."""
    v = (value or "").lower()
    return bool(v) and any(n.lower() in v for n in needles if n)


def _location_matches(location: str | None, crits: list[str]) -> bool:
    if not crits:
        return True  # no location filter → neutral
    cl = (location or "").lower()
    for crit in crits:
        k = crit.lower()
        if k in cl or any(tok in cl for tok in REGION_ALIASES.get(k, [])):
            return True
    return False


def evaluate(contact: Candidate, targeting: "Targeting | JsonObject") -> tuple[int, str]:
    """Score a contact 0-100 against a Targeting and explain why (one line). Mirrors the TS."""
    t = as_targeting(targeting)

    # Negative targeting → hard disqualify, regardless of any positive matches.
    if _contains_any(contact.company, t.exclude_companies) or _contains_any(
        contact.title, t.exclude_titles
    ):
        return 0, "excluded by targeting"

    want = [s.lower() for s in t.skills]
    have = [s.lower() for s in (contact.skills or [])]
    overlap = [s for s in want if s in have]

    title_match = _contains_any(contact.title, t.titles)
    company_match = _contains_any(contact.company, t.companies)
    industry_match = _contains_any(contact.industry, t.industries)
    size_match = _contains_any(contact.company_size, t.company_sizes)
    loc_match = _location_matches(contact.location, t.locations)

    cats: list[tuple[int, float]] = []  # (weight, hit 0..1)
    if t.titles:
        cats.append((WEIGHTS["titles"], 1.0 if title_match else 0.0))
    if want:
        cats.append((WEIGHTS["skills"], len(overlap) / len(want)))
    if t.companies:
        cats.append((WEIGHTS["companies"], 1.0 if company_match else 0.0))
    if t.industries:
        cats.append((WEIGHTS["industries"], 1.0 if industry_match else 0.0))
    if t.locations:
        cats.append((WEIGHTS["locations"], 1.0 if loc_match else 0.0))
    if t.company_sizes:
        cats.append((WEIGHTS["company_sizes"], 1.0 if size_match else 0.0))

    total_w = sum(w for w, _ in cats)
    score = 90 * sum(w * h for w, h in cats) / total_w if total_w else 0.0
    if contact.email:
        score += 10
    score_i = min(100, int(score + 0.5))  # round-half-up to match JS Math.round

    reasons: list[str] = []
    if overlap:
        reasons.append(f"matches {', '.join(overlap)}")
    if title_match:
        reasons.append("title fits the role")
    if company_match:
        reasons.append("target company")
    if industry_match:
        reasons.append("target industry")
    if t.locations and loc_match:
        reasons.append("in target location")
    if size_match:
        reasons.append("company size fits")
    if not reasons:
        reasons.append("limited overlap with the criteria")
    return score_i, "; ".join(reasons)
