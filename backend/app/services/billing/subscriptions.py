"""Stripe subscription gateway — Checkout, Customer Portal, and webhook handling.

Key-gated like the LLM providers: with no `STRIPE_SECRET_KEY` the billing endpoints report "not
configured" and the app stays on the free tier. **Stripe hosts all payment entry** (Checkout +
Portal) — we never touch card data. The webhook is the source of truth: it sets `Organization.plan`
+ the billing-period window, and the usage limits flow from the plan string (see `credits.py`).

Stripe API calls run in a thread (the SDK is sync); the webhook handler is a pure dict function so
it's testable without a live Stripe.
"""

import asyncio
from datetime import UTC, datetime

import stripe
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.types import JsonObject
from app.models import Organization

# Plans that map to a paid Stripe price. "free" is the implicit default (no subscription).
PAID_PLANS = ("pro", "premium")


def is_enabled() -> bool:
    return bool(get_settings().stripe_secret_key)


def _prices(s: Settings) -> dict[str, str]:
    """plan → Stripe Price ID, for the configured paid plans."""
    out: dict[str, str] = {}
    if s.stripe_price_pro:
        out["pro"] = s.stripe_price_pro
    if s.stripe_price_premium:
        out["premium"] = s.stripe_price_premium
    return out


def plan_for_price(s: Settings, price_id: str) -> str | None:
    """Reverse the price map (used by subscription webhooks to name the plan)."""
    for plan, pid in _prices(s).items():
        if pid == price_id:
            return plan
    return None


# --- Stripe-hosted flows (network) -------------------------------------------


async def create_checkout_url(
    s: Settings, *, org: Organization, plan: str, email: str, success_url: str, cancel_url: str
) -> str:
    """Create a Stripe Checkout Session for a paid plan; return its hosted URL. Reuses (or creates)
    the org's Stripe customer so the Portal + future checkouts don't duplicate it."""
    price_id = _prices(s).get(plan)
    if price_id is None:
        raise ValueError(f"no Stripe price configured for plan {plan!r}")
    stripe.api_key = s.stripe_secret_key
    if org.stripe_customer_id is None:
        customer = await asyncio.to_thread(
            stripe.Customer.create, email=email, name=org.name, metadata={"organization_id": org.id}
        )
        org.stripe_customer_id = customer.id
    meta = {"organization_id": org.id, "plan": plan}
    session = await asyncio.to_thread(
        stripe.checkout.Session.create,
        mode="subscription",
        customer=org.stripe_customer_id,
        line_items=[{"price": price_id, "quantity": 1}],
        success_url=success_url,
        cancel_url=cancel_url,
        client_reference_id=org.id,
        metadata=meta,
        subscription_data={"metadata": meta},
    )
    return session.url or ""


async def create_portal_url(s: Settings, *, org: Organization, return_url: str) -> str:
    """Create a Stripe Customer Portal session (manage/upgrade/cancel) for the org's customer."""
    if org.stripe_customer_id is None:
        raise ValueError("organization has no Stripe customer yet")
    stripe.api_key = s.stripe_secret_key
    session = await asyncio.to_thread(
        stripe.billing_portal.Session.create,
        customer=org.stripe_customer_id,
        return_url=return_url,
    )
    return session.url or ""


def parse_event(s: Settings, payload: bytes, sig_header: str) -> JsonObject:
    """Verify the webhook signature; return the event as a plain dict (raises on a bad sig)."""
    event = stripe.Webhook.construct_event(  # type: ignore[no-untyped-call]
        payload, sig_header, s.stripe_webhook_secret
    )
    result = event.to_dict_recursive()
    return result if isinstance(result, dict) else {}


# --- Webhook handling (pure dict logic — no network, fully testable) ---------


def _ts(v: object) -> datetime | None:
    if isinstance(v, int | float) and not isinstance(v, bool):
        return datetime.fromtimestamp(int(v), tz=UTC)
    return None


def _price_id_from_subscription(obj: JsonObject) -> str | None:
    items = obj.get("items")
    data = items.get("data") if isinstance(items, dict) else None
    first = data[0] if isinstance(data, list) and data else None
    price = first.get("price") if isinstance(first, dict) else None
    pid = price.get("id") if isinstance(price, dict) else None
    return pid if isinstance(pid, str) else None


async def _org_by_id_or_customer(
    session: AsyncSession, *, org_id: object, customer: object
) -> Organization | None:
    if isinstance(org_id, str):
        org = await session.get(Organization, org_id)
        if org is not None:
            return org
    if isinstance(customer, str):
        return (
            await session.execute(
                select(Organization).where(Organization.stripe_customer_id == customer)
            )
        ).scalar_one_or_none()
    return None


def _apply_subscription(s: Settings, org: Organization, obj: JsonObject) -> None:
    if isinstance(obj.get("id"), str):
        org.stripe_subscription_id = str(obj["id"])
    if isinstance(obj.get("customer"), str):
        org.stripe_customer_id = str(obj["customer"])
    price_id = _price_id_from_subscription(obj)
    if price_id is not None:
        plan = plan_for_price(s, price_id)
        if plan is not None:
            org.plan = plan
    org.current_period_start = _ts(obj.get("current_period_start"))
    org.current_period_end = _ts(obj.get("current_period_end"))


async def handle_event(session: AsyncSession, s: Settings, event: JsonObject) -> str:
    """Apply a (verified) Stripe event to the org's plan + billing window. Returns a status."""
    etype = event.get("type")
    data = event.get("data")
    obj = data.get("object") if isinstance(data, dict) else None
    if not isinstance(etype, str) or not isinstance(obj, dict):
        return "ignored"

    if etype == "checkout.session.completed":
        meta = obj.get("metadata")
        org_id = meta.get("organization_id") if isinstance(meta, dict) else None
        org = await _org_by_id_or_customer(
            session, org_id=org_id or obj.get("client_reference_id"), customer=obj.get("customer")
        )
        if org is None:
            return "no org"
        if isinstance(obj.get("customer"), str):
            org.stripe_customer_id = str(obj["customer"])
        if isinstance(obj.get("subscription"), str):
            org.stripe_subscription_id = str(obj["subscription"])
        if isinstance(meta, dict) and isinstance(meta.get("plan"), str):
            org.plan = str(meta["plan"])
        return "checkout applied"

    if etype in ("customer.subscription.created", "customer.subscription.updated"):
        meta = obj.get("metadata")
        org = await _org_by_id_or_customer(
            session,
            org_id=meta.get("organization_id") if isinstance(meta, dict) else None,
            customer=obj.get("customer"),
        )
        if org is None:
            return "no org"
        _apply_subscription(s, org, obj)
        return "subscription applied"

    if etype == "customer.subscription.deleted":
        org = await _org_by_id_or_customer(session, org_id=None, customer=obj.get("customer"))
        if org is None:
            return "no org"
        org.plan = "free"
        org.stripe_subscription_id = None
        org.current_period_start = None
        org.current_period_end = None
        return "subscription canceled"

    return "ignored"
