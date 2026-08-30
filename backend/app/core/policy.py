"""The settings policy chain.

Five levels, nearest wins: platform default → partner → organization → workspace → campaign. Every
send-policy, voice, vertical and provider read goes through a resolved `Policy` instead of reading
one level's JSONB directly, so a reseller or an org can set a default that a workspace or a single
campaign is free to override.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import JsonObject
from app.models import Campaign, Organization, Partner, Workspace

PLATFORM_DEFAULTS: JsonObject = {
    "daily_cap_email": 120,
    "daily_cap_linkedin": 80,
    "sending_window_enabled": False,
    "send_window_start": 8,
    "send_window_end": 18,
    "send_weekdays_only": True,
    "warmup_enabled": False,
    "brand_voice": "",
    "vertical": "recruiting",
    "providers": [],
    "autonomy_default": "assisted",
}


@dataclass(frozen=True)
class Policy:
    """A resolved settings view. `layers` runs nearest-first and always ends in the platform
    defaults, so every documented key resolves to something.
    """

    layers: tuple[JsonObject, ...]

    def _lookup(self, key: str) -> object:
        for layer in self.layers:
            if key in layer:
                return layer[key]
        return None

    def get_int(self, key: str) -> int:
        return _as_int(self._lookup(key), _as_int(PLATFORM_DEFAULTS.get(key), 0))

    def get_bool(self, key: str) -> bool:
        value = self._lookup(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, int | float):
            return bool(value)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on")
        return bool(PLATFORM_DEFAULTS.get(key))

    def get_str(self, key: str) -> str:
        value = self._lookup(key)
        if isinstance(value, str):
            return value
        fallback = PLATFORM_DEFAULTS.get(key)
        return fallback if isinstance(fallback, str) else ""

    def get_str_list(self, key: str) -> list[str]:
        value = self._lookup(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, str)]
        return []

    def effective(self) -> JsonObject:
        """The whole chain flattened — what the settings UI shows as the workspace's live config."""
        merged: JsonObject = {}
        for layer in reversed(self.layers):
            merged.update(layer)
        return merged


def _as_int(value: object, default: int) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return default
    return default


async def _upper_layers(session: AsyncSession, *, workspace_id: str) -> list[JsonObject]:
    """Workspace → organization → partner → platform, nearest first."""
    layers: list[JsonObject] = []
    workspace = await session.get(Workspace, workspace_id)
    if workspace is not None:
        layers.append(workspace.settings or {})
        org = await session.get(Organization, workspace.organization_id)
        if org is not None:
            layers.append(org.settings or {})
            if org.partner_id is not None:
                partner = await session.get(Partner, org.partner_id)
                if partner is not None:
                    layers.append(partner.settings or {})
    layers.append(PLATFORM_DEFAULTS)
    return layers


async def for_workspace(session: AsyncSession, *, workspace_id: str) -> Policy:
    return Policy(layers=tuple(await _upper_layers(session, workspace_id=workspace_id)))


async def for_campaign(session: AsyncSession, *, campaign: Campaign) -> Policy:
    upper = await _upper_layers(session, workspace_id=campaign.workspace_id)
    return Policy(layers=(campaign.constraints or {}, *upper))
