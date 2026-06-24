# Sourcewell — Technical Specification

*The canonical description of how the implementation is intended to work. Not a plan — the spec.*

---

## 1. Architecture at a glance

A **modular monolith** in Python. One codebase, **two process types**:

- **api** — FastAPI app (HTTP + later WebSocket). Stateless request/response. On approvals and inbound
  webhooks it writes state and **injects immediacy** (marks an enrollment due now).
- **worker** — a single long-running process that **polls due enrollments** (`SELECT … FOR UPDATE SKIP
  LOCKED`), advances each one tick of the state machine, and runs periodic jobs (pipeline refill, seat
  health). Postgres *is* the queue — no separate scheduler or broker in v1.

The agents (LLM steps) and all external IO (data providers, email, LinkedIn) sit **behind interfaces**, so
the whole pipeline runs deterministically with fakes in tests and real providers in dev/staging.

```
[api 8901] ──writes state, "due now" on approvals/replies──┐
                                                            ▼
                              Postgres (state + queue)  ◀── [worker] poll due → tick → periodic jobs
                                                            │
            email (Mailpit/Gmail/Graph) · LinkedIn (Unipile) · data providers (PDL/Coresignal) · Claude
```

---

## 2. Stack

| Concern | Choice |
|---|---|
| Language / framework | Python 3.12 · FastAPI · Pydantic v2 |
| ORM / migrations | SQLAlchemy 2.0 (async) · Alembic |
| Database | PostgreSQL + **pgvector** (relational + JSON + vectors + RLS in one engine) |
| LLM | **Anthropic Claude** — Opus 4.8 (ranking), Sonnet 4.6 (writing), Haiku 4.5 (classification); structured outputs, prompt caching, Batches |
| Embeddings | Voyage AI (or open model) → pgvector |
| Email | recruiter mailbox via Gmail/Microsoft Graph; **Mailpit** locally |
| LinkedIn | **Unipile** (per-user session) — opt-in |
| Data providers | PDL / Coresignal (adapter) |
| Auth / SSO | WorkOS (SSO/SCIM); dev stub first |
| Queue / cache | Postgres-as-queue in v1; **Redis + arq** added later when one worker isn't enough |
| Frontend | React + TypeScript + Vite |
| Tooling | uv · ruff · mypy · pytest · pre-commit · Docker Compose |

---

## 3. Repository layout

```
backend/app/
├─ main.py                       # FastAPI factory; mounts module routers
├─ platform/                     # shared kernel (files, not folders)
│  ├─ config.py  db.py  rls.py  security.py  llm.py  audit.py  telemetry.py
├─ modules/                      # generic core — one bounded context each
│  ├─ tenancy/                   # organization, workspace, user, membership, connection (+ connections/ auth)
│  ├─ contacts/                  # contact, company, identity-resolution, embeddings
│  ├─ sourcing/                  # orchestrator + adapters/ + agents/ (Sourcer + Evaluator: find + rank)
│  ├─ campaigns/                 # campaign(+criteria), step, template, JD-parse
│  ├─ enrollment/                # states.py, machine.py, service.py  ← the state machine
│  ├─ messaging/                 # conversation, message, channels/, agents/ (Writer + Responder), webhooks, approvals, guardrails
│  └─ suppression/               # blocklist + check
├─ runtime/                      # worker.py (poll → tick → periodic) · governor.py (send pacing)
└─ migrations/                   # Alembic (one Base, models imported from modules)
frontend/src/                    # app, features/{workspaces,campaigns,candidates,inbox,settings}, components, lib, stores, styles
infra/                           # docker/ · local/      (k8s/terraform added at deploy time)
```

Module shape: `router.py · service.py · models.py · schemas.py · tests/` (+ `adapters/`/`agents/`/`channels/`
where relevant). **No repository layer** — services use the session directly; add a repo only where queries
get complex. Modules call each other **directly** (no event bus in v1).

---

## 4. Modules & responsibilities

- **platform/** — `config` (settings), `db` (async engine, session, declarative `Base` + mixins), `rls`
  (tenant scoping predicate), `security` (auth + KMS token encryption), `llm` (Anthropic wrapper + base
  Agent), `audit` (write the event log), `telemetry` (logging + Sentry).
- **tenancy** — org/workspace/user/membership/connection; the connect flows (Unipile hosted auth, mailbox OAuth).
- **contacts** — canonical contact + company; identity-resolution (merge across sources); profile embeddings.
- **sourcing** — the orchestrator (pick adapters per campaign, run within rate budgets, normalize → contacts),
  source **adapters** (PDL, Coresignal, LinkedIn-via-Unipile, manual/CSV), and the **Sourcer** + **Evaluator** agents (find + rank).
- **campaigns** — campaign (targeting + criteria + autonomy + caps), steps (the sequence), templates,
  source config; the **JD-parser** agent that fills `campaign.criteria`.
- **enrollment** — the durable **state machine** (states, transition rules, `tick()`).
- **messaging** — conversation + message (drafts, sends, replies all live here), **channels** (email, LinkedIn),
  the **Writer** + **Responder** agents, the **guardrail** pipeline, inbound **webhooks**, and the **approvals** surface.
- **suppression** — the blocklist (org- and workspace-scoped) + the check used in sourcing and before every send.

---

## 5. Data model

Generic core, multi-tenant. Every tenant-scoped table carries `workspace_id` (+ denormalized `org_id`),
protected by **Row-Level Security**. IDs are ULIDs; `created_at`/`updated_at` everywhere; JSONB for
semi-structured data; pgvector column for contact embeddings.

| Table | Owns | Key fields |
|---|---|---|
| **organization** | tenant | name, plan, sso_config, data_region |
| **workspace** | sub-unit | organization_id, name, kind(client\|department\|team), settings, brand_voice |
| **user** | member | organization_id, email, name, status |
| **membership** | access | user_id, scope(org\|workspace), workspace_id?, role |
| **connection** | sending seat | organization_id, user_id, provider, external_id, seat_type, status, daily_sent, warmup_stage, token_ref(KMS) |
| **contact** | the person | workspace_id, name, title, company, location, skills/experience jsonb, linkedin_url, email, email_status, sources jsonb, embedding vector |
| **company** | employer | workspace_id, name, domain, … |
| **campaign** | the program | workspace_id, name, status, criteria jsonb, channels, autonomy_mode, target_pipeline_size, score_threshold, daily_cap |
| **step** | a sequence touchpoint | campaign_id, order, channel, delay, template_id, conditions |
| **template** | message template | workspace_id, name, channel, subject, body, approved |
| **score** | fit | workspace_id, campaign_id, contact_id, overall, dimensions jsonb, rationale, recommended_action |
| **enrollment** | state machine | workspace_id, campaign_id, contact_id, connection_id, **state**, current_step, **next_run_at**, reply_pending, outcome · UNIQUE(campaign_id, contact_id) |
| **message** | sends + replies | workspace_id, enrollment_id, conversation_id, step_id?, direction, channel, **status**(draft\|approved\|sent\|failed\|bounced\|received), body, **idempotency_key**, provider_message_id |
| **conversation** | thread | workspace_id, contact_id, owner_user_id, status, last_message_at |
| **blocklist** | suppression | scope(org\|workspace), scope_id, match_type, value, reason |
| **event** | audit | organization_id, actor, action, entity_type, entity_id, payload jsonb |

Relationships: `org → workspace → (contact, campaign, …)`; `campaign → step/template/score`;
`campaign + contact → enrollment`; `enrollment → message → conversation`. Approvals are `message.status =
draft`; the audit log is the `event` table. The recruiting "job" is folded into `campaign.criteria`.

---

## 6. The agents

Each agent is a constrained Claude call with a **Pydantic output schema** (structured outputs), built on
`platform/llm`, and **fakeable** in tests.

| Agent | Module | Model | Output |
|---|---|---|---|
| **Sourcer** | sourcing | Sonnet 4.6 | query strategies → candidates |
| **Evaluator** | sourcing | Opus 4.8 (cached rubric) | score + dimensions + evidence + action |
| **Writer** | messaging | Sonnet 4.6 | personalized message (grounded in real profile data) |
| **Responder** | messaging | Haiku→Sonnet | reply intent + suggested draft |
| **JD-parser** | campaigns | Sonnet 4.6 | structured criteria + rubric from a JD |

Cost controls: model tiering, **prompt caching** on the criteria/rubric prefix, the **Batches API** for bulk
scoring. Every generated message passes a deterministic **guardrail pipeline** (schema/length, factuality vs
profile, banned content, brand voice, compliance/unsubscribe, dedup) before it can be queued.

---

## 7. Sourcing

Ports & adapters. `SourceProvider` interface (`search`, `enrich`, `capabilities`). Adapters: data providers
(platform-licensed, always on) + LinkedIn-via-seat (only when a seat is connected; rate-capped; targeted).
The orchestrator selects adapters per campaign, runs within each one's rate budget, normalizes to canonical
contacts, and **dedupes/merges** across sources by `linkedin_url → email → name+company`. The LinkedIn seat
is reserved for targeted pulls/enrichment; data providers do bulk volume.

---

## 8. Channels & account safety

`Channel` interface (`send`, `fetch_replies`, `capabilities`). Email sends from the recruiter's mailbox
(Mailpit in dev). LinkedIn goes through the recruiter's Unipile session (InMail or connect-then-message).
The **governor** (`runtime/governor.py`) gates every send: business-hours window, daily cap, warmup ramp,
human-like jitter, seat health, kill switch — and splits one seat's daily budget across the recruiter's
workspaces. It's minimal for email v1 and becomes load-bearing for LinkedIn (~100 safe actions/day/seat).

---

## 9. The runtime engine (DB state machine)

The durable backbone — chosen over Temporal because the per-contact flow is a simple, mostly-waiting state
machine and that state *is* product data we query for the UI anyway.

- **Source of truth:** `enrollment.state` + `enrollment.next_run_at`. Two kinds of wait: **timer**
  (`next_run_at = now + delay`) and **signal** (`next_run_at = NULL`, woken by the api on approval/reply).
- **The worker loop:** poll due enrollments (`SKIP LOCKED`), and for each run `enrollment.service.tick()` —
  one state-machine transition: draft (Writer + guardrails → `message(draft)`) · gated send (governor →
  channel → record) · wait · advance/complete. Periodic jobs (pipeline refill = the Coordinator, seat-health,
  reconcile) run on the same loop.
- **Concurrency & safety:** one tick per enrollment via a Postgres advisory lock; **idempotent sends** via
  `UNIQUE(enrollment_id, step)` + provider idempotency; a crash mid-tick rolls back and is re-dispatched;
  `reconcile` catches stale claims. Effectively-once with the DB as the durable record.
- **Enrollment states:** `pending_first_touchpoint → awaiting_message_approval → scheduled_send → awaiting_reply →
  { handed_off | suppressed | completed_no_response }`, with reply/approval signals jumping the wakeup earlier.

When one worker isn't enough, add Redis + arq (queue, retries, concurrency) and split the poller into a
scheduler — a mechanical upgrade, no redesign.

---

## 10. LinkedIn / Unipile integration

Per-recruiter, session-based — **never a platform account**. The recruiter connects via Unipile **hosted
auth** (handles 2FA/checkpoints); we store an opaque `account_id` (token stays server-side at Unipile).
Capabilities scale with the seat (Premium → messages/connects; Sales Nav/Recruiter → search + InMail).
Unipile assigns a fixed proxy per account to reduce flagging; we still run our own safety governor. Inbound
replies arrive via Unipile webhooks. It's ToS-gray and account-risk-bearing, so it's opt-in and capped.

---

## 11. Multi-tenancy & security

`organization → workspace`; tenant data scoped by `workspace_id` with **Postgres RLS** — a user sees a row
only if its workspace is in their memberships (org-admins see all). Connection tokens are encrypted with KMS
envelope encryption. Auth via WorkOS (SSO/SCIM). Every meaningful action writes to the `event` audit log.
Suppression is scoped org- or workspace-level (the union is enforced).

---

## 12. Local development

Host-published ports use the **89xx** band (containers keep standard ports internally, so nothing clashes
with other repos):

| Host | Service |
|---|---|
| 8901 | API |
| 8900 | Frontend (Vite) |
| 8902 | Postgres + pgvector (→5432) |
| 8903 | Redis (reserved; not run in v1) |
| 8904 / 8905 | Mailpit web / SMTP |
| 8906 | Adminer/pgweb |

`docker compose` runs **Postgres + Mailpit** (Redis added later). Processes: `api` (uvicorn) and `worker`
(`python -m app.runtime.worker`). `.env`: `DATABASE_URL=…@localhost:8902/sourcewell`, `SMTP_URL=…localhost:8905`.
Make targets: `up/down · dev · test · test-fast · lint · typecheck · migrate · seed`.

---

## 13. Testing

**Fakes-first, deterministic.** Pyramid: fast **unit** (state machine, guardrails, governor,
identity-resolution — no IO, injected clock/ids) → **service/DB** (real test Postgres, each test in a
transaction rolled back) → **integration** (API via httpx ASGITransport; the runtime loop driven through
ticks with `FakeLLM` / `FakeChannel` / `FakeSourceProvider` / `FakeClock`; RLS isolation) → tiny **live
smoke** gated by `RUN_LIVE=1`. Harness: `conftest` fixtures, `tests/fakes/`, `tests/factories/`. Every change
ships ruff+mypy clean with tests; pre-commit runs the fast set, CI runs the full suite + coverage gate.

---

## 14. Deployment & extensibility

- **Deploy:** one artifact, two process types. Start on **Fly/Render** + managed Postgres (pgvector); path
  to **AWS** for enterprise/residency. Observability: Sentry + OpenTelemetry + Langfuse (LLM traces).
- **New vertical (sales/marketing):** reuse the generic core — set `campaign.criteria` semantics, add the
  vertical's **source/CRM adapters**, and **UI labels**. Introduce a `verticals/` layer only if a vertical
  needs a different context shape.
- **New adapter/channel:** implement the `SourceProvider` / `Channel` interface; no engine change.
- **Scale-out:** add Redis + arq (queue/retries) and a dedicated scheduler when a single worker saturates;
  add an event bus at a module seam if/when you extract a service.
