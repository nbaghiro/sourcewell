"""AI agents fall back to deterministic behavior when no Claude key is configured (the test env)."""

from app.models import Contact
from app.services.outreach.messaging import (
    classify_reply_intent,
    draft_message,
    draft_reply_text,
    summarize_thread,
)
from app.services.sourcing.scoring import evaluate_llm


def _contact() -> Contact:
    return Contact(
        full_name="Mia Park",
        title="VP of Sales",
        company="Acme",
        location="Berlin, DE",
        skills=["Salesforce"],
        email="mia@acme.com",
        source="x",
        tags=[],
    )


async def test_draft_message_falls_back_to_template() -> None:
    subject, body = await draft_message(
        _contact(), {"subject": "Hi {first_name}", "body": "Saw your work at {company}"}
    )
    assert "Mia" in subject
    assert "Acme" in body


async def test_classify_reply_intent_falls_back() -> None:
    assert await classify_reply_intent("not interested, please unsubscribe") == "opted_out"
    assert await classify_reply_intent("sounds good, tell me more") == "interested"
    assert await classify_reply_intent("who is this?") == "neutral"


async def test_draft_reply_and_summary_fall_back() -> None:
    text = await draft_reply_text(_contact(), "what's the comp range?")
    assert "Mia" in text
    summary = await summarize_thread("handed_off", None)
    assert "hand" in summary.lower()


async def test_evaluate_llm_falls_back_to_deterministic() -> None:
    score, rationale = await evaluate_llm(
        _contact(), {"skills": ["Salesforce"], "titles": ["VP of Sales"]}
    )
    assert score > 0 and rationale
