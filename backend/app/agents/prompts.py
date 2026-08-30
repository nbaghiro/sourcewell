"""Hardcoded vertical packs (industry prompt overlays + label packs) + prompt composition.

Verticals live in code; the `vertical` policy key is the pointer. The runtime composes an agent's
system prompt as: BASE[role] + the vertical overlay + the per-run context, and the UI takes its
nouns from the same pack's labels. Adding an industry later = another entry here, no schema change.
"""

from dataclasses import dataclass, replace

from app.models import AgentRole, WorkspaceKind

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
class Labels:
    """The nouns the UI uses. One pack per vertical; `workspace` is overridden by workspace kind."""

    contact: str
    contact_plural: str
    campaign: str
    campaign_plural: str
    workspace: str
    goal: str


@dataclass(frozen=True)
class Vertical:
    name: str
    prompts: dict[AgentRole, str]  # per-role overlay appended to the base
    labels: Labels


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
    labels=Labels(
        contact="candidate",
        contact_plural="candidates",
        campaign="role",
        campaign_plural="roles",
        workspace="client",
        goal="role",
    ),
)

_SALES = Vertical(
    name="sales",
    prompts={
        AgentRole.strategy: (
            "Domain: B2B sales. The audience is buyers and economic champions; the goal is a "
            "qualified meeting. Favor targeting by title, company size, and technology fit, and "
            "lead with a concrete outcome rather than a product tour."
        ),
        AgentRole.sourcing: (
            "Domain: B2B sales. A strong match sits in the buying committee for this offer at a "
            "company that plausibly has the problem. Prefer decision-makers over end users."
        ),
        AgentRole.outreach: (
            "Domain: B2B sales. You are a rep working an outbound thread about a specific offer. "
            "Qualify gently, answer objections plainly, and hand off once there's real interest."
        ),
    },
    labels=Labels(
        contact="lead",
        contact_plural="leads",
        campaign="sequence",
        campaign_plural="sequences",
        workspace="client",
        goal="offer",
    ),
)

VERTICALS: dict[str, Vertical] = {"recruiting": _RECRUITING, "sales": _SALES}
DEFAULT_VERTICAL = "recruiting"

_WORKSPACE_LABEL: dict[WorkspaceKind, str] = {
    WorkspaceKind.client: "Client",
    WorkspaceKind.department: "Department",
    WorkspaceKind.team: "Team",
}


def get_vertical(name: str) -> Vertical:
    return VERTICALS.get(name, VERTICALS[DEFAULT_VERTICAL])


def resolve_labels(vertical: str, kind: WorkspaceKind | None = None) -> Labels:
    """The vertical's label pack, with the workspace noun taken from its kind when we know it."""
    labels = get_vertical(vertical).labels
    if kind is None:
        return labels
    return replace(labels, workspace=_WORKSPACE_LABEL[kind])


def compose_system(role: AgentRole, vertical: str, *, context: str = "") -> str:
    """BASE[role] + the vertical overlay + the per-run context."""
    v = get_vertical(vertical)
    parts = [_BASE[role], v.prompts.get(role, "")]
    if context:
        parts.append(context)
    return "\n\n".join(p for p in parts if p)
