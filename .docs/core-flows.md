# Core flows — messaging + sourcing/fit (consolidated reference)

Distilled end-to-end understanding of the two core user flows, with current state and the
build backlog. Anchors are `file:line`-ish; the engine is **self-clocking** (no external scheduler):
`Enrollment.next_run_at` and `Campaign.next_source_at` are the clock, ticked by `app/worker.py`
(polls every 10s). Layering: `api/` (routers) → `services/{outreach,sourcing,workspace,insights,billing}`
→ `models.py` (one ORM module). LLM/providers degrade deterministically when unconfigured.

---

## Flow A — Messaging ↔ provider integration (send + reply)

### Path
- **Outbound**: `worker.run_due` → `enrollment.tick` state machine:
  `active` → `_draft_touchpoint` (render sequence step → `Message(status=draft)`; full-autonomy
  auto-approves → `scheduled`, stamps idempotency key) · `scheduled` → `_send_touchpoint` · `awaiting_reply`
  → next touchpoint due ? `active` : `completed`.
- **Send seam** `_send_touchpoint` (enrollment.py): suppression gate → `resolve_channel_seat(org,channel)`
  → **LinkedIn-no-seat fallback** (route to email if contact has one, else fail visibly — never a
  phantom "sent") → `governor.can_send_now` (window + workspace daily cap + warmup) → per-seat daily
  cap → `deliver_outbound`.
- **`deliver_outbound`** (services/outreach/messaging.py): resolves seat account, stamps idempotency
  key, sends via `ext/unipile.UnipileChannel` — LinkedIn as **InMail** or thread reply; email via
  Unipile `/emails` or SMTP fallback with `Message-ID`/`In-Reply-To` threading headers. Captures the
  provider thread id → `Message.external_id`; classifies hard (`PermanentSendError`) vs transient
  (`TransientSendError`); no-ops only under `settings.linkedin_dry_run`/`email_dry_run` (typed config,
  read from `EMAIL_DRY_RUN`/`LINKEDIN_DRY_RUN` env).
- **Inbound** `POST /webhooks/unipile` (api/messaging.py): HMAC signature or shared token → account
  events flip `Connection.needs_reauth`; message events extract text/thread-id/sender/`provider_message_id`,
  infer channel, **dedupe** (`already_ingested` + partial-unique index on `provider_message_id` →
  race-safe), resolve enrollment (by `external_id`, else sender email), `handle_reply`. Outreach agent
  (`agents/outreach.py`) picks reply/hand_off/opt_out; deterministic fallback classifies intent.
- **Manual reply** `POST /inbox/{id}/reply`: consumes pending draft, actually transmits via
  `deliver_outbound(reply=True)` (502 + rollback on failure), origin `ai|human`.
- **Billing**: usage derived (`services/billing/credits.py`): emails×1 + inmails×2 + sourced×1 vs plan
  allowance (free 200 / pro 5k / premium 25k), over Stripe window or calendar month. Plan change via
  real Stripe (checkout/portal/webhook) or self-serve `POST /billing/plan` (demo path).

### Current state (post-remediation — all shipped)
Seat-aware send through the real `UnipileChannel`; `external_id` captured; cold LinkedIn = real InMail;
idempotency key on send + reply; hard/soft error split; hard-bounce → suppress; per-seat daily cap;
LinkedIn→email fallback (default on); inbound dedupe via partial-unique index + savepoint; channel
preserved on inbound; manual reply really sends; webhook signature + replay/timestamp guard;
`*_DRY_RUN` promoted to typed `Settings`. Message carries `origin`, `idempotency_key`,
`provider_message_id`. Migrations through `e6f7a8b9c0d1`.

### Backlog (Tier-2, not yet built)
1. **Synchronous webhook handler** runs LLM + send before responding → Unipile timeout → retries.
   Fix: background the handler (`BackgroundTasks`/queue), return 202 immediately.
2. **Email inbound threading via `external_id` not wired** — `/webhooks/inbound` keys on
   from_email/enrollment_id, not `In-Reply-To` → multi-campaign contacts mis-route on email replies.
3. **`resolve_channel_seat` ignores workspace/campaign ownership** — multi-workspace org can send from
   the wrong seat (needs a per-campaign designated-sender model).
4. **`send_reply` sets `sent_at` before delivery** — an audit failure after a real send loses the record.
5. **Cross-campaign contact fatigue** — no guard against a contact being messaged by several campaigns.
6. Minor: per-seat cap undercounts legacy null `account_id`; `warmup_stage`/`daily_sent` columns unused;
   SMTP Message-ID reused as a Unipile thread id on transport mixing.

---

## Flow B — Sourcing → candidate fit / ranking

### Path (criteria → DB → sourcing → ranking)
1. **Criteria** = one `Targeting` object (titles/skills/seniorities/functions/locations/companies/
   industries/company_sizes/technologies/keywords/exclude_*), edited in the composer's `TargetingEditor`.
2. **Stored** verbatim on **`Campaign.criteria`** (JSONB). Same object is posted to `/people/search` —
   audience == search.
3. **Sourcing pass** (`worker.run_source_due` when `Campaign.next_source_at` due → `run_sourcing` LLM or
   `deterministic_source`): `as_targeting(criteria)` → each provider `search(targeting)` maps every field
   onto its DSL (PDL/Apollo; PDL is most complete) → `PersonHit[]` → `import_hits` persists **new** hits
   as `Contact` rows (dedupe by email→linkedin→name+company). Sourced signals seniority/function/
   technologies land in `Contact.attributes`.
4. **Ranking** (tail of every pass) `rank_campaign` (services/sourcing/ranking.py): scores **all**
   workspace contacts (existing pool + newly imported) with `evaluate(contact, criteria)`, skips
   already-enrolled, inserts `Enrollment(state=proposed, score, rationale)` for `fit ≥ FIT_THRESHOLD(40)`.

### Scoring model (post Phase 1+2 — shipped)
`app/targeting.py::evaluate` — **deterministic**, byte-mirrored by `frontend/src/lib/targeting.ts`
(17 canonical cases pinned in `tests/test_targeting.py`; verified 17/17 identical):
- **Fit 0-100** = weighted fraction of *specified* scorable criteria matched. WEIGHTS: titles30, skills30,
  companies20, **seniorities20**, industries15, locations15, **functions10**, company_sizes10.
- **Matching**: positive titles/companies = permissive substring (VP ⊇ SVP); **excludes = word-boundary**
  (intern ≠ International); seniority/function via synonym taxonomy (`_SENIORITY_ALIASES`/`_FUNCTION_ALIASES`);
  skills = word-boundary fractional overlap; locations via region aliases.
- **No email bonus** — Fit is match-quality only. **Reachability** is a separate axis
  (`reachability()` → verified|reachable|needs_enrichment; shown as `ReachabilityChip`).
- **Search-only floor**: audience of only technologies/keywords → neutral 50 (never "ranks nobody").
- **LLM role**: shapes inputs (Strategy agent designs `criteria`) + orchestrates (Sourcing agent's
  search/enrich/score/import tools; its `score` tool is advisory). The only LLM-produced score is
  `evaluate_llm` (scoring.py) on **single-enroll** only, clamped, deterministic fallback. Bulk ranking
  + the composer estimate are deterministic on purpose (the mirror invariant).

### Backlog (not yet built)
1. **Lifecycle consolidation — create == live today** (`create_campaign` hardcodes `status=active` +
   `next_source_at=now`; `draft` unreachable; no activate endpoint). Proposed: real **draft → launch**
   (`POST /campaigns/{id}/launch` sets status=active + next_source_at=now); composer offers "Save draft"
   vs "Create & launch"; launch is the review gate.
2. **Per-campaign sourcing cadence** — replace the global `_SOURCE_INTERVAL_HOURS=6` with
   `Campaign.source_interval_hours` (presets: aggressive ~1h / standard 6h / slow 24h / one-shot) +
   optional `source_target` stop-condition (keep ~N live proposed candidates, then skip provider search).
   The "two sources" (existing pool via `rank_campaign` + new sourcing) are already wired — formalize.
3. **Phase 3 — one canonical score policy**: deterministic-first everywhere; an LLM **refinement** pass
   re-scores top-N with the *full* criteria+candidate; store `score`, `score_rule`, `scored_by: rule|ai`
   so bulk/single divergence is explicit, not path-dependent. (`evaluate_llm` currently sees a reduced
   view — title/skills/location/email only.)
4. **Phase 4 — tunable weights + freshness**: optional per-campaign `weights`; `Campaign.criteria_version`
   + `Enrollment.scored_version`; re-rank stale `proposed` on criteria change; score only new contacts
   incrementally (replace the full-workspace rescan in `rank_campaign`).
5. Storage: seniority/function live in `Contact.attributes` (no migration); could promote to first-class
   columns if we want SQL-queryable audience counts.

---

## Invariants / conventions to preserve
- **targeting.py ↔ targeting.ts byte-for-byte** — change both + the `test_targeting.py` case table together.
- **Deterministic bulk scoring** (so the composer's live "~N match" == server ranking).
- **Everything degrades without keys** — no LLM → deterministic design/sourcing/scoring; no provider /
  dry-run → sends simulate.
- Demo runs offline in dry-run; `demo@sourcewell.ai` / `testpass`.
