"""Brief intake: turn a JD / free-text brief into an objective + targeting (a single LLM call).

Generalizes the old `parse_query` — vertical-aware, with a keyword fallback when the LLM is off.
One structured `complete_json`, not an agent. The LinkedIn JD pull (`ext/unipile`) is stubbed; the
resolved text feeds this.
"""

from dataclasses import dataclass

from app.agents.verticals import DEFAULT_VERTICAL, get_vertical
from app.core import llm
from app.core.types import JsonObject
from app.models import AgentRole
from app.targeting import Targeting


@dataclass
class BriefResult:
    objective: str
    targeting: Targeting
    facts: JsonObject


def _as_list(v: object) -> list[str]:
    return [str(x) for x in v if str(x).strip()] if isinstance(v, list) else []


async def parse_brief(text: str, *, vertical: str = DEFAULT_VERTICAL) -> BriefResult:
    """Brief/JD -> {objective, Targeting, facts}. Claude when enabled, else a keyword fallback."""
    cleaned = text.strip()
    fallback = BriefResult(
        objective=cleaned[:280], targeting=Targeting(keywords=cleaned or None), facts={}
    )
    if not llm.is_enabled() or not cleaned:
        return fallback

    overlay = get_vertical(vertical).prompts.get(AgentRole.main, "")
    system = (
        "Extract a concise outreach objective and people-targeting filters from a brief. " + overlay
    )
    user = (
        f"Brief:\n{cleaned[:4000]}\n\n"
        'Return JSON {"objective": one sentence, "titles": [...], "skills": [...], '
        '"locations": ["EU"/"US"/cities], "seniorities": [...], "industries": [...], '
        '"facts": {"comp": str, "must_haves": [...]}}.'
    )
    obj = await llm.complete_json(system, user, max_tokens=500)
    if not obj:
        return fallback

    facts_raw = obj.get("facts")
    facts: JsonObject = (
        {str(k): val for k, val in facts_raw.items()} if isinstance(facts_raw, dict) else {}
    )
    targeting = Targeting(
        titles=_as_list(obj.get("titles")),
        skills=_as_list(obj.get("skills")),
        locations=_as_list(obj.get("locations")),
        seniorities=_as_list(obj.get("seniorities")),
        industries=_as_list(obj.get("industries")),
        keywords=cleaned[:200],
    )
    return BriefResult(
        objective=str(obj.get("objective") or cleaned[:280]), targeting=targeting, facts=facts
    )
