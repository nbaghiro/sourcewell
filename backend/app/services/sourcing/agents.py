"""Evaluator agent — LLM-judged fit on top of the deterministic scorer.

The deterministic scoring + the `Candidate` protocol now live in `app/targeting.py` (the single
source of truth, mirrored byte-for-byte by the frontend). This module keeps only the async,
Claude-judged path used for single-contact, user-initiated scoring (e.g. enroll); bulk ranking
uses `app.targeting.evaluate` directly so the audience estimate stays mirrored on the frontend.
"""

from app.core import llm
from app.core.types import JsonObject
from app.targeting import FIT_THRESHOLD, Candidate, Targeting, as_targeting, evaluate

__all__ = ["FIT_THRESHOLD", "Candidate", "evaluate", "evaluate_llm"]


async def evaluate_llm(contact: Candidate, criteria: "Targeting | JsonObject") -> tuple[int, str]:
    """Claude-judged fit when enabled, else the deterministic `evaluate`.

    Used for single-contact, async, user-initiated scoring (e.g. enroll). Bulk ranking keeps the
    fast deterministic `evaluate` so the audience estimate stays mirrored on the frontend.
    """
    score, rationale = evaluate(contact, criteria)
    if not llm.is_enabled():
        return score, rationale
    t = as_targeting(criteria)
    system = (
        "You are a sourcing analyst. Score 0-100 how well a person fits outreach criteria, "
        "weighing title, skills, and location, and whether they're reachable."
    )
    user = (
        f"Criteria: {t.model_dump()}\n"
        f"Person: name {getattr(contact, 'full_name', '?')}, "
        f"title {contact.title}, location {contact.location}, "
        f"skills {', '.join(contact.skills or [])}, reachable_email {bool(contact.email)}.\n"
        'Return JSON {"score": integer 0-100, "rationale": one short sentence}.'
    )
    obj = await llm.complete_json(system, user, max_tokens=120)
    raw = (obj or {}).get("score")
    if isinstance(raw, int | float):
        return max(0, min(100, int(raw))), str((obj or {}).get("rationale") or rationale)
    return score, rationale
