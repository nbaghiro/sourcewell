"""Stripe subscription gateway — price mapping + webhook event handling (no live Stripe)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.services.billing import subscriptions
from tests.factories import make_org


def _settings() -> Settings:
    return Settings(
        stripe_secret_key="sk_test_x",
        stripe_price_pro="price_pro",
        stripe_price_premium="price_premium",
        stripe_webhook_secret="whsec_x",
    )


def test_plan_for_price_round_trips() -> None:
    s = _settings()
    assert subscriptions._prices(s) == {"pro": "price_pro", "premium": "price_premium"}
    assert subscriptions.plan_for_price(s, "price_pro") == "pro"
    assert subscriptions.plan_for_price(s, "price_premium") == "premium"
    assert subscriptions.plan_for_price(s, "price_unknown") is None


def test_is_enabled_false_without_key() -> None:
    # The test environment has no STRIPE_SECRET_KEY → billing is disabled.
    assert subscriptions.is_enabled() is False


@pytest.mark.db
async def test_webhook_checkout_then_subscription_then_cancel(db_session: AsyncSession) -> None:
    s = _settings()
    org = await make_org(db_session, slug="billing")

    # 1) checkout completes → link customer/subscription, set plan from session metadata.
    await subscriptions.handle_event(
        db_session,
        s,
        {
            "type": "checkout.session.completed",
            "data": {
                "object": {
                    "customer": "cus_123",
                    "subscription": "sub_123",
                    "metadata": {"organization_id": org.id, "plan": "pro"},
                }
            },
        },
    )
    await db_session.flush()
    assert org.stripe_customer_id == "cus_123"
    assert org.stripe_subscription_id == "sub_123"
    assert org.plan == "pro"

    # 2) subscription updated → plan from the price + the billing window (found via customer).
    start_ts, end_ts = 1_750_000_000, 1_752_592_000
    await subscriptions.handle_event(
        db_session,
        s,
        {
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "id": "sub_123",
                    "customer": "cus_123",
                    "items": {"data": [{"price": {"id": "price_premium"}}]},
                    "current_period_start": start_ts,
                    "current_period_end": end_ts,
                }
            },
        },
    )
    await db_session.flush()
    assert org.plan == "premium"
    assert org.current_period_start == datetime.fromtimestamp(start_ts, tz=UTC)
    assert org.current_period_end == datetime.fromtimestamp(end_ts, tz=UTC)

    # 3) subscription canceled → back to free, window cleared.
    await subscriptions.handle_event(
        db_session,
        s,
        {"type": "customer.subscription.deleted", "data": {"object": {"customer": "cus_123"}}},
    )
    await db_session.flush()
    assert org.plan == "free"
    assert org.stripe_subscription_id is None
    assert org.current_period_start is None
