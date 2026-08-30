"""The label pack an API response carries so the UI can name things in the tenant's own words."""

from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.prompts import DEFAULT_VERTICAL, resolve_labels
from app.core import policy
from app.models import Workspace


class LabelPack(BaseModel):
    contact: str
    contact_plural: str
    campaign: str
    campaign_plural: str
    workspace: str
    goal: str


async def label_pack(session: AsyncSession, *, workspace: Workspace | None) -> LabelPack:
    """The resolved pack for a workspace, or the platform default when there isn't one yet."""
    if workspace is None:
        return LabelPack(**vars(resolve_labels(DEFAULT_VERTICAL)))
    vertical = (await policy.for_workspace(session, workspace_id=workspace.id)).get_str("vertical")
    return LabelPack(**vars(resolve_labels(vertical, workspace.kind)))
