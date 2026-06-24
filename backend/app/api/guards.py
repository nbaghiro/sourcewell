"""Route access guards — assert tenancy on the request's TenantContext."""

from fastapi import HTTPException

from app.api.context import TenantContext


def require_org_admin(ctx: TenantContext) -> None:
    if not ctx.is_org_admin:
        raise HTTPException(status_code=403, detail="organization admin required")


def require_workspace(ctx: TenantContext) -> str:
    """Resolve the workspace the request operates on (X-Workspace-Id, already access-checked)."""
    if ctx.current_workspace_id is None:
        raise HTTPException(status_code=400, detail="missing X-Workspace-Id")
    return ctx.current_workspace_id
