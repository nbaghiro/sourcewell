"""Unified targeting — ONE audience/search definition, used two ways across the whole app.

A `Targeting` is "the kind of person we're after." It is consumed by exactly two operations:
  * search — each provider adapter maps it onto its own query DSL (Find People / discovery).
  * score  — `evaluate()` measures how well a Contact (or a provider hit) fits it. This drives
             campaign ranking, the audience estimate, single enroll, and Find People result scores.

It is stored on `Campaign.criteria` (the audience) and posted to `/people/search` (the search), so
the two are the same object. The frontend mirrors `evaluate()` byte-for-byte in
`src/lib/targeting.ts` (so the composer's live estimate agrees with what server-side ranking
produces) — keep them in lockstep; `tests/test_targeting.py` pins the canonical cases.

Two orthogonal axes:
  * `evaluate` → Fit 0-100: the weighted fraction of the *specified, scorable* criteria a contact
    matches (each specified field shares a 100-point budget by weight; skills score fractionally).
    An exclude match hard-disqualifies. "In the audience" at `>= FIT_THRESHOLD`. Fit is NOT affected
    by whether we have an email.
  * `reachability` → whether we can act on them (verified email / reachable / needs enrichment).

Titles/companies match on permissive substring (so "VP" ⊇ "SVP"); excludes match on precise
word boundaries (so "intern" ≠ "International"); seniority/function match via a synonym taxonomy.
The remaining search-only fields (`technologies / keywords`) narrow a provider search and, when
they're the *only* criteria, contribute a neutral floor rather than a zero.
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
    def email_status(self) -> str | None: ...
    @property
    def linkedin_url(self) -> str | None: ...
    @property
    def company(self) -> str | None: ...
    @property
    def industry(self) -> str | None: ...
    @property
    def company_size(self) -> str | None: ...
    @property
    def seniority(self) -> str | None: ...
    @property
    def function(self) -> str | None: ...


# Relative weight per *scorable* field — only fields the user actually specified share the budget
# (so a one-field audience still scores out of 100). Keep in sync with src/lib/targeting.ts.
WEIGHTS: dict[str, int] = {
    "titles": 30,
    "skills": 30,
    "companies": 20,
    "seniorities": 20,
    "industries": 15,
    "locations": 15,
    "functions": 10,
    "company_sizes": 10,
}

# Fit for an audience defined ONLY by search-only fields (technologies/keywords): the provider
# returned this person for that search, so give a neutral baseline instead of a hard 0.
_SEARCH_ONLY_FLOOR = 50

# Region shorthands so "EU" matches "Berlin, DE", "London, UK", etc.
REGION_ALIASES: dict[str, list[str]] = {
    "eu": ["de", "uk", "nl", "pt", "ie", "fr", "es", "it", "remote · eu"],
    "us": ["us", "usa", "united states"],
    "remote": ["remote"],
}

# Normalize common seniority / function synonyms to one bucket so "VP" == "Vice President" == "SVP".
_SENIORITY_ALIASES: dict[str, str] = {
    "vice president": "vp",
    "vp": "vp",
    "svp": "vp",
    "evp": "vp",
    "c-level": "exec",
    "c-suite": "exec",
    "cxo": "exec",
    "chief": "exec",
    "ceo": "exec",
    "cto": "exec",
    "cfo": "exec",
    "coo": "exec",
    "founder": "exec",
    "owner": "exec",
    "president": "exec",
    "partner": "exec",
    "director": "director",
    "dir": "director",
    "head": "director",
    "senior": "senior",
    "sr": "senior",
    "lead": "lead",
    "staff": "lead",
    "principal": "lead",
    "manager": "manager",
    "mgr": "manager",
    "mid": "mid",
    "intermediate": "mid",
    "junior": "junior",
    "jr": "junior",
    "entry": "junior",
    "intern": "junior",
    "associate": "junior",
}
_FUNCTION_ALIASES: dict[str, str] = {
    "engineering": "engineering",
    "eng": "engineering",
    "software": "engineering",
    "developer": "engineering",
    "development": "engineering",
    "dev": "engineering",
    "sales": "sales",
    "marketing": "marketing",
    "growth": "marketing",
    "product": "product",
    "design": "design",
    "data": "data",
    "operations": "operations",
    "ops": "operations",
    "finance": "finance",
    "accounting": "finance",
    "people": "people",
    "hr": "people",
    "human resources": "people",
    "recruiting": "people",
    "support": "support",
    "customer success": "support",
    "legal": "legal",
}


def as_targeting(x: "Targeting | JsonObject | None") -> Targeting:
    """Coerce a stored criteria dict (or None) into a Targeting. Extra/legacy keys are ignored."""
    return x if isinstance(x, Targeting) else Targeting.model_validate(x or {})


def _contains_any(value: str | None, needles: list[str]) -> bool:
    """Permissive: any needle is a case-insensitive substring of value (so 'VP' matches 'SVP')."""
    v = (value or "").lower()
    return bool(v) and any(n.lower() in v for n in needles if n)


def _word_contains(value: str | None, needle: str) -> bool:
    """Precise: needle occurs in value bounded by non-alphanumerics — so 'intern' matches 'intern
    program' but NOT 'international'. Used for excludes so we never over-disqualify."""
    v = (value or "").lower()
    n = needle.lower().strip()
    if not v or not n:
        return False
    start = 0
    while True:
        i = v.find(n, start)
        if i < 0:
            return False
        before = v[i - 1] if i > 0 else ""
        after = v[i + len(n)] if i + len(n) < len(v) else ""
        if not before.isalnum() and not after.isalnum():
            return True
        start = i + 1


def _any_word(value: str | None, needles: list[str]) -> bool:
    return any(_word_contains(value, n) for n in needles if n)


def _canon(value: str | None, aliases: dict[str, str]) -> str:
    k = (value or "").lower().strip()
    return aliases.get(k, k)


def _bucket_match(value: str | None, crits: list[str], aliases: dict[str, str]) -> bool:
    """Normalized-synonym equality for the single-token fields (seniority / function)."""
    if not crits:
        return False
    cv = _canon(value, aliases)
    return bool(cv) and cv in {_canon(c, aliases) for c in crits}


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
    """Fit 0-100 — the weighted fraction of the *specified* criteria this contact matches. Whether
    we can reach them is a separate axis (see `reachability`). Mirrors the TS byte-for-byte."""
    t = as_targeting(targeting)

    # Negative targeting → hard disqualify (word-boundary so 'intern' ≠ 'international').
    if _any_word(contact.company, t.exclude_companies) or _any_word(
        contact.title, t.exclude_titles
    ):
        return 0, "excluded by targeting"

    want = [s.lower() for s in t.skills]
    have = [s.lower() for s in (contact.skills or [])]
    overlap = [w for w in want if any(_word_contains(h, w) for h in have)]

    title_match = _contains_any(contact.title, t.titles)
    company_match = _contains_any(contact.company, t.companies)
    industry_match = _contains_any(contact.industry, t.industries)
    size_match = _contains_any(contact.company_size, t.company_sizes)
    loc_match = _location_matches(contact.location, t.locations)
    sen_match = _bucket_match(contact.seniority, t.seniorities, _SENIORITY_ALIASES)
    fn_match = _bucket_match(contact.function, t.functions, _FUNCTION_ALIASES)

    cats: list[tuple[int, float]] = []  # (weight, hit 0..1) per specified scorable field
    if t.titles:
        cats.append((WEIGHTS["titles"], 1.0 if title_match else 0.0))
    if want:
        cats.append((WEIGHTS["skills"], len(overlap) / len(want)))
    if t.companies:
        cats.append((WEIGHTS["companies"], 1.0 if company_match else 0.0))
    if t.seniorities:
        cats.append((WEIGHTS["seniorities"], 1.0 if sen_match else 0.0))
    if t.industries:
        cats.append((WEIGHTS["industries"], 1.0 if industry_match else 0.0))
    if t.locations:
        cats.append((WEIGHTS["locations"], 1.0 if loc_match else 0.0))
    if t.functions:
        cats.append((WEIGHTS["functions"], 1.0 if fn_match else 0.0))
    if t.company_sizes:
        cats.append((WEIGHTS["company_sizes"], 1.0 if size_match else 0.0))

    total_w = sum(w for w, _ in cats)
    if total_w:
        fit = 100 * sum(w * h for w, h in cats) / total_w
    elif t.technologies or (t.keywords or "").strip():
        fit = float(
            _SEARCH_ONLY_FLOOR
        )  # only search-only criteria → the provider match is the signal
    else:
        fit = 0.0
    score_i = min(100, int(fit + 0.5))  # round-half-up to match JS Math.round

    reasons: list[str] = []
    if overlap:
        reasons.append(f"matches {', '.join(overlap)}")
    if title_match:
        reasons.append("title fits the role")
    if sen_match:
        reasons.append("seniority fits")
    if fn_match:
        reasons.append("right function")
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


def reachability(contact: Candidate) -> str:
    """Can we act on this candidate, independent of fit: 'verified' (validated email), 'reachable'
    (email or LinkedIn), or 'needs_enrichment'. A separate axis from `evaluate`. Mirrors the TS."""
    if contact.email_status == "valid":
        return "verified"
    if contact.email or contact.linkedin_url:
        return "reachable"
    return "needs_enrichment"
