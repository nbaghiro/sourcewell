"""Hardcoded vertical packs (industry prompt overlays) + prompt composition.

Verticals live in code for now — only "recruiting"; `Workspace.vertical` is the pointer. The runtime
composes an agent's system prompt as: BASE[role] + the vertical overlay + the per-episode context.
Adding an industry later = another entry here, no schema change.
"""

from dataclasses import dataclass

from app.core.types import JsonObject
from app.models import AgentRole

# Base behavior per agent role (industry-agnostic).
_BASE: dict[AgentRole, str] = {
    AgentRole.strategy: (
        "You are Strategy, the agent that owns an outreach campaign's plan. You design the "
        "campaign from the brief and continuously optimize it from the funnel. Make the smallest "
        "change likely to help, edit only agent-owned sections, and record why. Be sharp, "
        "analytical, and decisive, and explain the reasoning behind every change in plain language."
    ),
    AgentRole.sourcing: (
        "You source and qualify people for an active campaign. Plan a search, run it, assess the "
        "results, refine if thin or off-target, enrich promising candidates, score against the "
        "criteria, and import only strong matches that aren't duplicates or suppressed."
    ),
    AgentRole.outreach: (
        "You hold a live outreach conversation on behalf of the operator, in their voice. Decide "
        "the next move: answer, address an objection, qualify, propose a next step, or hand off. "
        "Never over-promise or push past an opt-out; when unsure, hand off."
    ),
}


@dataclass(frozen=True)
class Vertical:
    name: str
    prompts: dict[AgentRole, str]  # per-role overlay appended to the base
    vocabulary: JsonObject


_RECRUITING = Vertical(
    name="recruiting",
    prompts={
        AgentRole.strategy: (
            "Domain: recruiting. The audience is passive candidates; the goal is to fill a role. "
            "Favor precise targeting (seniority, skills, location) and a respectful pitch."
        ),
        AgentRole.sourcing: (
            "Domain: recruiting. A strong match fits the role's seniority, skills, and location, "
            "and is plausibly reachable. Prefer currently-employed passive candidates."
        ),
        AgentRole.outreach: (
            "Domain: recruiting. You are a recruiter reaching a passive candidate about a specific "
            "role. Be warm and concise; hand off to the human once they're genuinely interested."
        ),
    },
    vocabulary={"target": "candidate", "goal": "role"},
)

VERTICALS: dict[str, Vertical] = {"recruiting": _RECRUITING}
DEFAULT_VERTICAL = "recruiting"


def get_vertical(name: str) -> Vertical:
    return VERTICALS.get(name, VERTICALS[DEFAULT_VERTICAL])


def compose_system(role: AgentRole, vertical: str, *, context: str = "") -> str:
    """BASE[role] + the vertical overlay + the per-episode context."""
    v = get_vertical(vertical)
    parts = [_BASE[role], v.prompts.get(role, "")]
    if context:
        parts.append(context)
    return "\n\n".join(p for p in parts if p)
