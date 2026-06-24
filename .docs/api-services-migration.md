# Backend migration: `core / services / api` (+ `worker.py`)

Restructure `backend/app/` from domain-grouped one-file-per-feature into a three-layer
split: **infra (`core/`) · business logic (`services/`) · HTTP (`api/`)**, with the
autonomous engine consolidated into a root `worker.py`. No API routes, DB schema, or
frontend behaviour change — this is an internal reorganization.

## Target structure

```
app/
├── main.py              # web entrypoint — app factory, mounts every api/ router
├── worker.py            # background entrypoint — run_due + the poll loop (the driver)
├── models.py            # shared kernel — ALL ORM tables (unchanged, Alembic depends on it)
├── targeting.py         # shared kernel — scoring contract (mirrored on the frontend)
├── deps.py              # shared kernel — request DI: TenantContext, ContextDep, SessionDep, require_*
├── core/                # infra only — config db crypto signing logging llm types
├── services/            # all business logic
│   ├── outreach/    campaigns enrollment messaging governor
│   ├── people/      contacts suppression search
│   ├── sourcing/    people service agents usage + adapters/   # PROMOTED out of people/
│   ├── workspace/   auth tenancy settings notifications        # logic only (DI lives in app/deps.py)
│   ├── insights/    dashboard analytics audit
│   └── agent/       activity state chat                         # the 500-line router, split 3 ways
└── api/                 # FastAPI routers only (thin) — schemas + serializers + endpoints
    ├── campaigns enrollment messaging
    ├── contacts suppression search sourcing
    ├── auth tenancy settings notifications
    ├── dashboard analytics audit agent
    └── runtime          # admin/QA endpoints (run-due, fast-forward)
```

## Decisions (locked)

1. **Middle folder = `services/`.**
2. **DI kernel → `app/deps.py`** (root, peer to `models.py`/`targeting.py`). `ContextDep`/`SessionDep`/
   `TenantContext`/`require_workspace`/`require_org_admin` are framework plumbing imported by ~13
   modules — they must not live inside a *service*. Module-level it imports only `core` + `models`;
   `get_context` keeps its **lazy** `import auth` (call-time), so no `core→services` edge.
3. **Runtime = Option B.** `app/worker.py` holds only the driver (`run_due` + `_loop` + `main`);
   `governor.can_send_now` is **send-policy** → `services/outreach/governor.py` (where `enrollment`
   uses it); `admin_router.py` → `api/runtime.py`. This makes the dependency one-way
   (`worker → enrollment → governor → models`) with **no circular import and no deferred-import trick**.
4. **`sourcing` promoted to a peer of `people`** (it never imports `people.contacts`; it *feeds*
   contacts). Router → `api/sourcing.py` but **keeps the `/people` route prefix** (no API change).
5. **`agent/router.py` splits** into `services/agent/{activity,state,chat}.py` + thin `api/agent.py`.

## Placement rules (apply per file)

- **→ `api/<feature>.py`**: the `APIRouter` + endpoint handlers + request/response schemas (`*In`/`*Out`)
  + serializers (`dump`/`_dump`). The HTTP contract.
- **→ `services/<context>/<feature>.py`**: the logic functions + domain value objects. Services take
  plain args (`session`, `workspace_id`, ORM objects) and return ORM/values — never the HTTP schemas.
- **→ `app/deps.py`**: only the request-DI primitives.
- **stay at root**: `models.py`, `targeting.py` (shared kernel).
- **stay in `core/`**: config, db, crypto, signing, logging, llm, types (unchanged).

## File-by-file mapping

| Current | → `api/` (router + schemas) | → `services/…` (logic) | → other |
|---|---|---|---|
| `outreach/campaigns.py` | `api/campaigns.py` | `services/outreach/campaigns.py` | |
| `outreach/enrollment.py` | `api/enrollment.py` | `services/outreach/enrollment.py` | |
| `outreach/messaging.py` | `api/messaging.py` | `services/outreach/messaging.py` | |
| `people/contacts.py` | `api/contacts.py` | `services/people/contacts.py` | |
| `people/suppression.py` | `api/suppression.py` | `services/people/suppression.py` | |
| `people/search.py` | `api/search.py` | `services/people/search.py` *(extract inline logic)* | |
| `people/sourcing/router.py` | `api/sourcing.py` | — | |
| `people/sourcing/{people,service,agents,usage}.py` | — | `services/sourcing/*` | |
| `people/sourcing/adapters/*` | — | `services/sourcing/adapters/*` | |
| `workspace/tenancy.py` | `api/tenancy.py` (CRUD router) | `services/workspace/tenancy.py` (signup/ws/user CRUD) | **DI → `app/deps.py`** |
| `workspace/auth.py` | `api/auth.py` | `services/workspace/auth.py` | |
| `workspace/settings.py` | `api/settings.py` | `services/workspace/settings.py` | |
| `workspace/notifications.py` | `api/notifications.py` | `services/workspace/notifications.py` *(extract inline)* | |
| `insights/dashboard.py` | `api/dashboard.py` | `services/insights/dashboard.py` *(extract inline)* | |
| `insights/analytics.py` | `api/analytics.py` | `services/insights/analytics.py` *(extract inline)* | |
| `insights/audit.py` | `api/audit.py` | `services/insights/audit.py` (`record()` write-sink) | |
| `agent/router.py` | `api/agent.py` | `services/agent/{activity,state,chat}.py` | |
| `runtime/engine.py` | — | — | **→ `app/worker.py`** (`run_due`) |
| `runtime/worker.py` | — | — | **→ `app/worker.py`** (`_loop`, `main`) |
| `runtime/governor.py` | — | `services/outreach/governor.py` | |
| `runtime/admin_router.py` | `api/runtime.py` | — | |
| `main.py` `models.py` `targeting.py` `core/*` | — | — | unchanged (imports updated) |

### Endpoints that need logic *extracted* (not just moved)
`search`, `dashboard`, `analytics`, and `notifications` keep their logic **inline in the route handler**
today. Splitting them means pulling that query/aggregation into a `services/.../*.py` function the thin
router calls. (For trivial single-read endpoints this is optional — judgment call per endpoint.)

## Cross-cutting updates
- **Import rewrites** (every cross-module import). Mapping for the scripted rewrite:
  - `app.outreach.X` → `app.services.outreach.X`
  - `app.people.sourcing` → `app.services.sourcing` ⟵ **do this before the next line**
  - `app.people.X` → `app.services.people.X`
  - `app.workspace.tenancy` (DI names) → `app.deps`; (CRUD) → `app.services.workspace.tenancy`
  - `app.workspace.X` → `app.services.workspace.X`
  - `app.insights.X` → `app.services.insights.X`
  - `app.agent.router` → `app.api.agent` / `app.services.agent.*`
  - `app.runtime.governor` → `app.services.outreach.governor`
  - `app.runtime.engine` / `app.runtime.worker` → `app.worker`
  - `app.models` / `app.targeting` / `app.core.*` — **unchanged**
- `main.py` mounts routers from `app.api.*`.
- **Makefile**: `make worker` → `python -m app.worker` (was `app.runtime.worker`). `make dev`/`make seed` unchanged.
- **Alembic** `migrations/env.py`: imports `app.models` — **unchanged** ✓
- **mypy/ruff/CI/pre-commit**: still target `app` + `tests` — **unchanged** ✓
- **Tests**: rewrite `from app.<ctx>` imports; `tests/seed/builder.py` if it imports a moved module.
- **`.docs/people-data-apis.md`**: update `app/people/sourcing/` → `app/services/sourcing/`.
- New `__init__.py` for `app/services/`, each `services/<ctx>/`, and `app/api/`.

## Phasing — each phase ends green (`mypy app tests` · `ruff` · `pytest -q`), one commit each

1. **`app/deps.py`** — extract the DI kernel from `workspace/tenancy.py`; rewrite the ~13 DI import sites. *(keystone, highest fan-out)*
2. **`app/worker.py` + `services/outreach/governor.py` + `api/runtime.py`** — dissolve `runtime/` (Option B). Update Makefile.
3. **outreach** → `services/outreach/{campaigns,enrollment,messaging}` + `api/{campaigns,enrollment,messaging}`.
4. **people** → `services/people/*` + `api/*` (extract `search` logic).
5. **sourcing** → `services/sourcing/*` + `api/sourcing.py` (promote out of `people/`).
6. **workspace** → `services/workspace/*` + `api/*` (3-way `tenancy` split: DI already done in P1; CRUD logic+router here).
7. **insights** → `services/insights/*` + `api/*` (extract `dashboard`/`analytics`; keep `audit.record`).
8. **agent** → `services/agent/{activity,state,chat}` + `api/agent.py`.
9. **`main.py` mount cleanup + docs + final sweep** — confirm zero stale `app.<oldctx>` imports, frontend untouched, full gate green.

## Risks / guards
- **Circular imports** — guarded by design (deps lazy-auth; Option B one-way runtime). Run `mypy` after every phase to catch any.
- **The 4 inline-logic extractions** are genuine refactors, not moves — scope them in their phases (4 and 7).
- **Import-rewrite ambiguity** — `app.people.sourcing` must be rewritten *before* `app.people.*`; verify with a final grep for any `app.outreach`/`app.runtime`/`app.workspace.tenancy`/`app.insights`/`app.agent`/`app.people` stragglers.
- **No behaviour change** — same routes, same DB, same tests; the suite + frontend build are the safety net.
```
