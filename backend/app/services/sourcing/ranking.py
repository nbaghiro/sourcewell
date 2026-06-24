"""Ranking: score a workspace's contacts against a campaign into 'proposed' enrollments.

(Sourcing adapters that pull external contacts plug in alongside this later; for the alpha
we rank the contacts already imported into the workspace.)
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Campaign, Contact, Enrollment, EnrollmentState
from app.services.sourcing.targeting import FIT_THRESHOLD, evaluate


async def rank_campaign(
    session: AsyncSession, *, workspace_id: str, campaign: Campaign
) -> list[Enrollment]:
    contacts = (
        (await session.execute(select(Contact).where(Contact.workspace_id == workspace_id)))
        .scalars()
        .all()
    )
    already = set(
        (
            await session.execute(
                select(Enrollment.contact_id).where(Enrollment.campaign_id == campaign.id)
            )
        )
        .scalars()
        .all()
    )

    created: list[Enrollment] = []
    for contact in contacts:
        if contact.id in already:
            continue
        score, rationale = evaluate(contact, campaign.criteria or {})
        if score < FIT_THRESHOLD:
            continue  # only propose contacts that actually match the audience
        enrollment = Enrollment(
            workspace_id=workspace_id,
            campaign_id=campaign.id,
            contact_id=contact.id,
            state=EnrollmentState.proposed,
            score=score,
            score_rationale=rationale,
        )
        session.add(enrollment)
        created.append(enrollment)

    await session.flush()
    created.sort(key=lambda e: e.score, reverse=True)
    return created
