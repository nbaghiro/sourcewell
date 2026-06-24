"""End-to-end: sample contacts -> campaign -> rank -> approve -> draft -> send -> reply.

Drives the runtime by hand via /admin/run-due (one transition per call). EMAIL_DRY_RUN=1
(set in conftest) means the send path runs without touching SMTP.
"""

import pytest
from httpx import AsyncClient


async def _setup(client: AsyncClient, slug: str) -> dict[str, str]:
    signup = await client.post(
        "/organizations",
        json={
            "org_name": f"Org {slug}",
            "slug": slug,
            "admin_email": f"admin@{slug}.com",
            "admin_name": "Admin",
        },
    )
    assert signup.status_code == 201
    uid = signup.json()["admin_user_id"]

    ws = await client.post(
        "/workspaces", json={"name": "Team", "kind": "team"}, headers={"X-User-Id": uid}
    )
    assert ws.status_code == 201
    return {"X-User-Id": uid, "X-Workspace-Id": ws.json()["id"]}


_SEQUENCE = [
    {
        "channel": "email",
        "delay_days": 0,
        "subject": "Hi {first_name}",
        "body": "Role at {company}?",
    },
    {
        "channel": "email",
        "delay_days": 0,
        "subject": "Following up {first_name}",
        "body": "Still keen?",
    },
]
_CRITERIA = {"skills": ["python"], "titles": ["engineer"]}


@pytest.mark.db
async def test_full_loop_approve_each(db_client: AsyncClient) -> None:
    h = await _setup(db_client, "acme")

    assert (await db_client.post("/contacts/sample", json={"count": 5}, headers=h)).json()[
        "created"
    ] == 5

    campaign = await db_client.post(
        "/campaigns",
        json={
            "name": "Backend hire",
            "criteria": _CRITERIA,
            "sequence": _SEQUENCE,
            "autonomy_mode": "approve_each",
        },
        headers=h,
    )
    assert campaign.status_code == 200
    cid = campaign.json()["id"]

    ranked = await db_client.post(f"/campaigns/{cid}/rank", headers=h)
    assert ranked.json()["proposed"] == 5
    top = ranked.json()["enrollments"][0]
    assert top["score"] == 100  # python + engineer title + email

    eid = top["id"]
    approved = await db_client.post(f"/enrollments/{eid}/approve", headers=h)
    assert approved.json()["state"] == "active"

    # Tick 1: draft the first touchpoint (manual mode -> awaiting_approval, no auto-send).
    assert (await db_client.post("/admin/run-due", headers=h)).json()["processed"] >= 1
    drafts = (await db_client.get("/approvals", headers=h)).json()
    assert len(drafts) == 1
    assert drafts[0]["status"] == "draft"
    assert drafts[0]["subject"] == "Hi Jane"  # template filled with first name

    approve_msg = await db_client.post(f"/messages/{drafts[0]['id']}/approve", headers=h)
    assert approve_msg.json()["status"] == "approved"

    # Tick 2: send the approved touchpoint.
    assert (await db_client.post("/admin/run-due", headers=h)).json()["processed"] >= 1
    thread = (await db_client.get(f"/enrollments/{eid}/messages", headers=h)).json()
    assert any(m["status"] == "sent" for m in thread)

    # Inbound reply -> handed off to the recruiter.
    reply = await db_client.post(
        "/webhooks/reply",
        json={"enrollment_id": eid, "text": "Interested, let's talk!"},
        headers=h,
    )
    assert reply.json()["intent"] == "interested"
    handed = (
        await db_client.get(f"/campaigns/{cid}/enrollments?state=handed_off", headers=h)
    ).json()
    assert eid in [e["id"] for e in handed]


@pytest.mark.db
async def test_auto_mode_sends_without_message_approval(db_client: AsyncClient) -> None:
    h = await _setup(db_client, "globex")
    await db_client.post("/contacts/sample", json={"count": 3}, headers=h)

    cid = (
        await db_client.post(
            "/campaigns",
            json={
                "name": "Auto",
                "criteria": _CRITERIA,
                "sequence": _SEQUENCE,
                "autonomy_mode": "auto",
            },
            headers=h,
        )
    ).json()["id"]

    ranked = await db_client.post(f"/campaigns/{cid}/rank", headers=h)
    eid = ranked.json()["enrollments"][0]["id"]
    await db_client.post(f"/enrollments/{eid}/approve", headers=h)

    # Tick 1 drafts + auto-approves; tick 2 sends. No manual approval in between.
    await db_client.post("/admin/run-due", headers=h)
    assert (await db_client.get("/approvals", headers=h)).json() == []
    await db_client.post("/admin/run-due", headers=h)

    thread = (await db_client.get(f"/enrollments/{eid}/messages", headers=h)).json()
    assert any(m["status"] == "sent" for m in thread)

    inbox = (await db_client.get("/inbox", headers=h)).json()
    assert any(item["enrollment_id"] == eid for item in inbox)
