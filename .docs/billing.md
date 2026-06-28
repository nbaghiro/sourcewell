# Billing & Usage Limits — credits, plans, and Stripe

How accounts are metered, how plan limits are enforced, and how Stripe subscriptions drive the plan.
Two layers: an **enforcement layer** (always on, no Stripe needed) and a **Stripe layer** (key‑gated,
inert until configured). The whole thing degrades cleanly to a free tier with no keys.

The account is the **`Organization`** (workspaces belong to orgs; limits are org‑wide).

## 1 · The model — one pooled monthly credit balance

Each account gets a monthly **credit allowance** by plan. Three actions draw from the one pool at a
weight (`services/billing/credits.py`):

| Action | Weight | Counted from |
|---|---|---|
| Email sent | ×1 | `Message` (outbound, `status=sent`, `sent_at` in period) |
| LinkedIn InMail sent | ×2 | same, `channel=linkedin` |
| Candidate sourced | ×1 | `Enrollment` created in period |

`used = emails×1 + inmails×2 + sourced×1`, summed org‑wide. Allowances (`PLAN_CREDITS`):

| Plan | Credits / period |
|---|---|
| free | 200 |
| pro | 5,000 |
| premium | 25,000 |
| trial / demo / unknown | 5,000 (default) |

Weights + allowances are plain constants in `credits.py` — tune in one place.

## 2 · Enforcement — soft (meter + warn, allow overage)

Usage is **derived** from the source rows (`Message.sent_at`, `Enrollment.created_at`) — one source
of truth, no separate counter to drift. There is **no hard block**: sending and sourcing keep
working past the cap. `credit_status()` returns `used / allowance / over / pct / breakdown`; the UI
surfaces a near‑limit (≥80%) note and an over‑allowance banner. Overage is reconciled at billing
(see §8). Swapping to a hard block per‑plan later is a check at the send/source gates.

## 3 · The data model — `Organization`

```
plan: str                       # "free" | "pro" | "premium" | "trial" | "demo" | …
stripe_customer_id: str | None
stripe_subscription_id: str | None
current_period_start: datetime | None   # the billing-cycle anchor (set by the webhook)
current_period_end: datetime | None
```

`plan` is the single switch limits flow from. The Stripe columns are written **only by the webhook**
(migration `f3b8c1da9e20`).

## 4 · Endpoints

| Endpoint | Auth | What |
|---|---|---|
| `GET /settings/usage` | member | usage meter (used/allowance/over/pct/breakdown + `billing_enabled`) |
| `POST /billing/checkout {plan}` | org admin | → Stripe‑hosted Checkout URL |
| `POST /billing/portal` | org admin | → Stripe Customer Portal URL |
| `POST /webhooks/stripe` | none (signature‑verified) | the source of truth for plan + period |

All billing endpoints return `503 "not configured."` when `STRIPE_SECRET_KEY` is unset.

## 5 · Stripe — Checkout / Portal / webhook (`services/billing/subscriptions.py`)

**Stripe hosts all payment entry** — we never see card data. Checkout/Portal return a URL the client
redirects to. The **webhook is the source of truth**; it handles:

- `checkout.session.completed` → link `stripe_customer_id` + `stripe_subscription_id`, set `plan`
  from the session metadata.
- `customer.subscription.created|updated` → set `plan` from the price (reverse `_prices` map) +
  `current_period_start/end` from the subscription.
- `customer.subscription.deleted` → back to `free`, clear the subscription + period.

`handle_event()` is a pure dict function (no network) so it's unit‑tested against the real event
shapes (`tests/test_billing_stripe.py`). Stripe API calls run in a thread (the SDK is sync).

## 6 · The usage period

`credit_status` resets usage on `period_start`: the **calendar month** by default, or the org's
**`current_period_start`** (the Stripe billing cycle) once subscribed. So a Pro account's credits
roll over with its invoice, not the 1st of the month.

## 7 · Turning it on (Stripe setup)

1. **Stripe dashboard (test mode):** create **Pro** + **Premium** products, each a recurring monthly
   price → copy the `price_…` ids.
2. **`backend/.env`:**
   ```
   STRIPE_SECRET_KEY=sk_test_…
   STRIPE_PRICE_PRO=price_…
   STRIPE_PRICE_PREMIUM=price_…
   STRIPE_WEBHOOK_SECRET=whsec_…
   ```
3. **Webhook (local):** `stripe listen --forward-to localhost:8901/webhooks/stripe` → gives the
   `whsec_…`. Forward `checkout.session.completed` + `customer.subscription.*`.
4. Restart the backend → the **Upgrade / Manage billing** buttons light up on Settings → Plan & usage.
   Test with Stripe test cards.

Until keys are set, the UI shows "Billing isn't set up yet" and every billing endpoint 503s.

## 8 · Not built yet — overage billing

Because enforcement is soft (overage allowed), charging for overage means reporting usage past the
allowance to **Stripe metered billing**. Hold until the base subscription flow is validated with test
keys. Everything needed (the credit meter, the period anchor) already exists.

## 9 · Mental model

`Organization.plan` is the only switch. **Limits derive from rows** (no counter), **Stripe drives the
plan** (via the webhook), and the **plan drives the limits**. No Stripe, no problem — the app runs on
the free tier. The enforcement layer and the Stripe layer are independent: you can change weights,
allowances, or what "sourced" means without touching Stripe, and wire Stripe without touching limits.
