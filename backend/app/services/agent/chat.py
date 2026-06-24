"""The bounded copilot for the agent experience.

Classifies a user's message (LLM when enabled, keyword fallback otherwise) and answers about state,
explains a person, or previews a search. No destructive actions — those are a fast-follow once the
chat direction is validated. Returns a raw dataclass; the HTTP layer maps it to Pydantic.
"""

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import llm
from app.core.types import JsonObject
from app.models import Contact, Enrollment
from app.services.agent.state import StateData, aggregate_state
from app.services.people import discovery, suppression
from app.services.people.adapters.registry import build_providers_for_org
from app.targeting import Targeting

_INTENTS = {"status", "explain", "find", "help"}


@dataclass(frozen=True)
class ChatResult:
    reply: str
    kind: str  # status | explain | find | help
    data: JsonObject | None = None


async def _classify(message: str) -> JsonObject:
    if llm.is_enabled():
        obj = await llm.complete_json(
            "Classify a user's message to an autonomous outreach agent.",
            f"Message: {message!r}\n"
            'Return JSON {"intent": one of ["status","explain","find","help"], '
            '"subject": the person/campaign/criteria mentioned or null}.',
            max_tokens=80,
        )
        if obj and obj.get("intent") in _INTENTS:
            return obj
    m = message.lower()
    if any(w in m for w in ("why", "explain", "about ", "tell me about", "skip")):
        return {"intent": "explain"}
    if any(w in m for w in ("find", "source", "search", "look for", "get me", "prospect")):
        return {"intent": "find"}
    if any(w in m for w in ("help", "what can you", "how do you")):
        return {"intent": "help"}
    return {"intent": "status"}


def _state_payload(st: StateData) -> JsonObject:
    """The state snapshot serialized to the same shape the AgentState response emits."""
    return {
        "status": st.status,
        "counts": st.counts,
        "today": st.today,
        "needs_you": st.needs_you,
        "governor": {
            ch: {"cap": g.cap, "sent": g.sent, "blocked": g.blocked}
            for ch, g in st.governor.items()
        },
        "campaigns": [
            {"id": c.id, "name": c.name, "status": c.status, "active": c.active}
            for c in st.campaigns
        ],
    }


async def handle_chat(
    session: AsyncSession, *, workspace_id: str, org_id: str, message: str
) -> ChatResult:
    """A bounded copilot: answers about state, explains a person, previews a search."""
    parsed = await _classify(message)
    intent = parsed["intent"]

    if intent == "help":
        return ChatResult(
            kind="help",
            reply="I'm your sourcing agent. Ask me things like: “what needs me today?”, "
            "“why did you skip <name>?”, or “find VPs of Sales in EU fintech”. "
            "I draft, send, and watch for replies on autopilot within your guardrails.",
            data=None,
        )

    if intent == "explain":
        contacts = (
            (await session.execute(select(Contact).where(Contact.workspace_id == workspace_id)))
            .scalars()
            .all()
        )
        low = message.lower()
        hit = next(
            (
                c
                for c in contacts
                if c.full_name.lower() in low
                or any(p and p.lower() in low for p in c.full_name.split())
            ),
            None,
        )
        if hit is None:
            return ChatResult(
                kind="explain",
                reply="Tell me who — e.g. “why did you skip Aisha Park?”",
                data=None,
            )
        if await suppression.is_suppressed(session, organization_id=org_id, email=hit.email):
            return ChatResult(
                kind="explain",
                reply=f"{hit.full_name} is on your do-not-contact list, so the agent skips them.",
                data={"contact": hit.full_name},
            )
        enr = (
            await session.execute(
                select(Enrollment)
                .where(Enrollment.workspace_id == workspace_id, Enrollment.contact_id == hit.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if enr is not None:
            why = enr.score_rationale or "fit against the campaign criteria"
            state_label = enr.state.value.replace("_", " ")
            reply = f"{hit.full_name} scored {enr.score}/100 — {why} (currently {state_label})."
            return ChatResult(
                kind="explain",
                reply=reply,
                data={"contact": hit.full_name, "score": enr.score},
            )
        return ChatResult(
            kind="explain",
            reply=f"{hit.full_name} is in Contacts but not yet ranked into a campaign.",
            data={"contact": hit.full_name},
        )

    if intent == "find":
        providers = await build_providers_for_org(session, org_id)
        targeting = Targeting(keywords=message)
        hits = await discovery.search_people(providers, targeting, limit=15)
        top = ", ".join(h.full_name for h in hits[:3])
        return ChatResult(
            kind="find",
            reply=f"Found {len(hits)} people"
            + (f" — top matches {top}. " if top else ". ")
            + "Open Find People to review and import them.",
            data={"count": len(hits), "names": [h.full_name for h in hits[:6]]},
        )

    # status (default)
    st = await aggregate_state(session, workspace_id=workspace_id)
    needs = st.needs_you
    in_seq = st.in_sequence
    parts = []
    if needs["approvals"]:
        parts.append(
            f"{needs['approvals']} draft{'s' if needs['approvals'] != 1 else ''} to approve"
        )
    if needs["hot_replies"]:
        parts.append(
            f"{needs['hot_replies']} repl{'ies' if needs['hot_replies'] != 1 else 'y'} waiting"
        )
    head = "You're all caught up — " if not parts else "You have " + " and ".join(parts) + ". "
    reply = (
        head
        + f"The agent has {in_seq} people in sequence and sent {st.today['sent']} today "
        + f"({st.today['replies']} replied, {st.today['handed_off']} handed off)."
    )
    return ChatResult(kind="status", reply=reply, data=_state_payload(st))
