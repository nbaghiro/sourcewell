"""Per-section provenance for a campaign's strategy — the manual vs AI ownership model.

A campaign's `field_owners` maps each strategy SECTION to "human" or "agent". Agents write only
agent-owned sections; a human edit pins a section to "human"; "let AI manage" hands it back. This
is the data substrate behind the cockpit's ✨/🔒 chips and the single autonomy/manual code path.
"""

from app.core.types import JsonObject
from app.models import Authorship, Campaign

# The strategy sections that carry ownership (the cockpit's editable cards).
SECTIONS: tuple[str, ...] = ("audience", "sequence", "messaging")


def default_owners(authored_by: Authorship) -> JsonObject:
    """Initial ownership — a human-authored campaign owns every section; an agent-authored one
    lets the agent own them all (until the human takes a section over)."""
    owners: JsonObject = {}
    for section in SECTIONS:
        owners[section] = authored_by.value
    return owners


def owner_of(campaign: Campaign, section: str) -> Authorship:
    """The owner of a section, falling back to who authored the campaign."""
    raw = campaign.field_owners.get(section)
    if raw == Authorship.human.value:
        return Authorship.human
    if raw == Authorship.agent.value:
        return Authorship.agent
    return campaign.authored_by


def is_agent_owned(campaign: Campaign, section: str) -> bool:
    return owner_of(campaign, section) is Authorship.agent


def agent_writable(campaign: Campaign) -> list[str]:
    """The sections an agent may write on this campaign."""
    return [s for s in SECTIONS if is_agent_owned(campaign, s)]


def pin(campaign: Campaign, section: str) -> None:
    """Take a section over (a human edit) — agents will no longer touch it."""
    campaign.field_owners = {**campaign.field_owners, section: Authorship.human.value}


def unpin(campaign: Campaign, section: str) -> None:
    """Hand a section back to the agent ('let AI manage')."""
    campaign.field_owners = {**campaign.field_owners, section: Authorship.agent.value}
