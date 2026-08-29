"""Dev CLI: simulate Stripe billing webhooks against the demo org — no Stripe account needed.

Drives the *real* webhook handler (`subscriptions.handle_event`) with synthetic events so you can
watch the demo org's plan, allowance, and usage meter change live in the app. This intentionally
bypasses Stripe's network + signature layer (Checkout, Portal, signature verification) — to exercise
those, use real Stripe test keys + `stripe listen`. Here we cover the plan → allowance → meter state
machine end to end.

    python -m tests.seed.billing_sim status          # show the demo org's current plan + usage
    python -m tests.seed.billing_sim upgrade pro      # simulate a completed Pro checkout
    python -m tests.seed.billing_sim upgrade premium  # simulate a completed Premium checkout
    python -m tests.seed.billing_sim cancel           # simulate a cancellation → back to free
"""

import asyncio
import sys
from datetime import UTC, datetime

from sqlalchemy import select

import app.models  # noqa: F401  (register every ORM table)
from app.core.config import get_settings
from app.core.db import SessionLocal
from app.core.types import JsonObject
from app.models import Organization
from app.services.billing import subscriptions
from app.services.billing.credits import credit_status
from tests.seed.builder import DEMO_ORG_SLUG

# Stable synthetic Stripe ids for the demo org (so repeated runs update, not duplicate).
_CUSTOMER = "cus_demo_sim"
_SUBSCRIPTION = "sub_demo_sim"


def _checkout_event(org_id: str, plan: str) -> JsonObject:
    """The `checkout.session.completed` Stripe fires once payment succeeds."""
    return {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "customer": _CUSTOMER,
                "subscription": _SUBSCRIPTION,
                "client_reference_id": org_id,
                "metadata": {"organization_id": org_id, "plan": plan},
            }
        },
    }


def _cancel_event() -> JsonObject:
    """The `customer.subscription.deleted` Stripe fires when a subscription ends."""
    return {"type": "customer.subscription.deleted", "data": {"object": {"customer": _CUSTOMER}}}


async def _run(action: str, plan: str | None) -> None:
    s = get_settings()
    async with SessionLocal() as session:
        org = (
            await session.execute(select(Organization).where(Organization.slug == DEMO_ORG_SLUG))
        ).scalar_one_or_none()
        if org is None:
            sys.exit(f"demo org {DEMO_ORG_SLUG!r} not found — run `python -m tests.seed` first")

        if action == "upgrade":
            if plan not in subscriptions.PAID_PLANS:
                sys.exit(f"plan must be one of {subscriptions.PAID_PLANS}")
            result = await subscriptions.handle_event(session, s, _checkout_event(org.id, plan))
            print(f"[billing-sim] event applied → {result}")
        elif action == "cancel":
            result = await subscriptions.handle_event(session, s, _cancel_event())
            print(f"[billing-sim] event applied → {result}")
        elif action != "status":
            sys.exit("usage: billing_sim <status | upgrade pro | upgrade premium | cancel>")

        await session.commit()
        st = await credit_status(
            session,
            organization_id=org.id,
            plan=org.plan,
            now=datetime.now(UTC),
            period_start_at=org.current_period_start,
        )
        flag = "  ⚠ OVER" if st.over else ""
        print(
            f"[billing-sim] plan={org.plan}  used={st.used}  allowance={st.allowance}  "
            f"pct={st.pct}%{flag}"
        )


def main() -> None:
    args = sys.argv[1:]
    action = args[0] if args else "status"
    plan = args[1] if len(args) > 1 else None
    asyncio.run(_run(action, plan))


if __name__ == "__main__":
    main()
