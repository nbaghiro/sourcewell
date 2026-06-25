"""Unit tests for per-section campaign provenance (manual vs AI ownership)."""

from app.agents import provenance
from app.core.types import JsonObject
from app.models import Authorship, Campaign


def _campaign(authored_by: Authorship, owners: JsonObject | None = None) -> Campaign:
    return Campaign(workspace_id="w", name="c", authored_by=authored_by, field_owners=owners or {})


def test_default_owners_human_owns_all() -> None:
    assert provenance.default_owners(Authorship.human) == {s: "human" for s in provenance.SECTIONS}


def test_default_owners_agent_owns_all() -> None:
    assert provenance.default_owners(Authorship.agent) == {s: "agent" for s in provenance.SECTIONS}


def test_owner_falls_back_to_authorship() -> None:
    c = _campaign(Authorship.agent, owners={})
    assert provenance.owner_of(c, "audience") is Authorship.agent
    assert provenance.is_agent_owned(c, "messaging")


def test_pinned_section_overrides_authorship() -> None:
    c = _campaign(Authorship.agent, owners={"messaging": "human"})
    assert provenance.owner_of(c, "messaging") is Authorship.human
    assert not provenance.is_agent_owned(c, "messaging")
    assert provenance.owner_of(c, "audience") is Authorship.agent  # the rest still the agent's


def test_agent_writable_excludes_pinned() -> None:
    c = _campaign(Authorship.agent, owners={"messaging": "human"})
    writable = provenance.agent_writable(c)
    assert "messaging" not in writable
    assert "audience" in writable and "sequence" in writable


def test_pin_and_unpin_roundtrip() -> None:
    c = _campaign(Authorship.agent, owners={})
    provenance.pin(c, "audience")
    assert provenance.owner_of(c, "audience") is Authorship.human
    assert "audience" not in provenance.agent_writable(c)
    provenance.unpin(c, "audience")
    assert provenance.owner_of(c, "audience") is Authorship.agent
    assert "audience" in provenance.agent_writable(c)
