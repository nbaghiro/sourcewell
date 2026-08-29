"""Billing HTTP endpoints — Stripe Checkout + Customer Portal + the subscription webhook.

Checkout/Portal return a Stripe-hosted URL the client redirects to; the webhook is the source of
truth for plan changes. Key-gated (503 when Stripe isn't configured). Checkout/Portal are org-admin
only; the webhook is unauthenticated (Stripe calls it) but verified by signature.
"""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from app.api.context import ContextDep, SessionDep
from app.api.guards import require_org_admin
from app.core.config import get_settings
from app.models import Organization, User
from app.services.billing import subscriptions
from app.services.billing.credits import monthly_allowance

router = APIRouter(tags=["billing"])

# Plans a user can self-select on the non-Stripe path (free + the paid tiers).
SELF_SERVE_PLANS = ("free", *subscriptions.PAID_PLANS)


class CheckoutIn(BaseModel):
    plan: str  # "pro" | "premium"


class PlanIn(BaseModel):
    plan: str  # "free" | "pro" | "premium"


class PlanOut(BaseModel):
    plan: str
    allowance: int


class UrlOut(BaseModel):
    url: str


class WebhookOut(BaseModel):
    status: str


@router.post("/billing/checkout", response_model=UrlOut)
async def checkout(body: CheckoutIn, ctx: ContextDep, session: SessionDep) -> UrlOut:
    """Start a Stripe Checkout for a paid plan; returns the hosted URL to redirect to."""
    require_org_admin(ctx)
    s = get_settings()
    if not subscriptions.is_enabled():
        raise HTTPException(status_code=503, detail="Billing is not configured.")
    if body.plan not in subscriptions.PAID_PLANS:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {body.plan!r}")
    org = await session.get(Organization, ctx.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")
    user = await session.get(User, ctx.user_id)
    try:
        url = await subscriptions.create_checkout_url(
            s,
            org=org,
            plan=body.plan,
            email=user.email if user else "",
            success_url=f"{s.frontend_url}/settings?billing=success",
            cancel_url=f"{s.frontend_url}/settings?billing=canceled",
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return UrlOut(url=url)


@router.post("/billing/plan", response_model=PlanOut)
async def change_plan(body: PlanIn, ctx: ContextDep, session: SessionDep) -> PlanOut:
    """Self-serve upgrade/downgrade for the non-Stripe path: set the org's plan directly so the
    plan-change experience works end-to-end without a payment provider. When Stripe is configured,
    paid changes go through Checkout / the Customer Portal instead (this returns 409)."""
    require_org_admin(ctx)
    if subscriptions.is_enabled():
        raise HTTPException(
            status_code=409, detail="Billing runs through Stripe — use checkout or the portal."
        )
    if body.plan not in SELF_SERVE_PLANS:
        raise HTTPException(status_code=400, detail=f"Unknown plan: {body.plan!r}")
    org = await session.get(Organization, ctx.org_id)
    if org is None:
        raise HTTPException(status_code=404, detail="organization not found")
    org.plan = body.plan
    # A direct change has no Stripe billing window — usage tracks the calendar month.
    org.current_period_start = None
    org.current_period_end = None
    return PlanOut(plan=org.plan, allowance=monthly_allowance(org.plan))


@router.post("/billing/portal", response_model=UrlOut)
async def portal(ctx: ContextDep, session: SessionDep) -> UrlOut:
    """Open the Stripe Customer Portal (manage/upgrade/cancel) for the org's billing account."""
    require_org_admin(ctx)
    s = get_settings()
    if not subscriptions.is_enabled():
        raise HTTPException(status_code=503, detail="Billing is not configured.")
    org = await session.get(Organization, ctx.org_id)
    if org is None or org.stripe_customer_id is None:
        raise HTTPException(status_code=400, detail="No billing account yet — subscribe first.")
    url = await subscriptions.create_portal_url(s, org=org, return_url=f"{s.frontend_url}/settings")
    return UrlOut(url=url)


@router.post("/webhooks/stripe", response_model=WebhookOut)
async def stripe_webhook(request: Request, session: SessionDep) -> WebhookOut:
    """Stripe-called (unauthenticated; verified by signature). Updates the org's plan + period."""
    s = get_settings()
    if not subscriptions.is_enabled():
        # Acknowledge with 200, not 5xx — Stripe treats 5xx as a delivery failure and retries the
        # event for up to 72h. A transient missing key shouldn't trigger a retry storm.
        return WebhookOut(status="ignored")
    payload = await request.body()
    sig = request.headers.get("Stripe-Signature", "")
    try:
        event = subscriptions.parse_event(s, payload, sig)
    except Exception as exc:  # bad signature / malformed payload → reject
        raise HTTPException(status_code=400, detail="invalid webhook signature") from exc
    status = await subscriptions.handle_event(session, s, event)
    return WebhookOut(status=status)
