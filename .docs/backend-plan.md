# Plan — finish the backend API + connect the client

**Decisions (locked)**
- Scope: **complete the API surface + wire every client action**; agents (Evaluator/Writer/Responder)
  and channels (email/LinkedIn) stay **deterministic stubs behind clean interfaces** — real Claude /
  Gmail / Unipile drop in later with no shape change.
- Client: **generate types from `/openapi.json`** (`openapi-typescript` + `openapi-fetch`) +
  **TanStack Query** for caching / invalidation / mutations — replacing the hand-written `api` +
  `useWorkspaceData`.

**The keystone:** to get a *good* generated client, the API must return **typed Pydantic response
models**, not `dict[str, Any]`. So Phase 1 is "type the responses + extract interfaces" — it makes
both the backend cleaner and the generated client fully typed.

---

## Part A — Backend API surface — ✅ DONE

The full surface is built (57 paths / 65 operations, gate-green, all verified end-to-end). Inventory
below; ✅ = done. Remaining `⬚` items are explicitly optional/deferred and noted at the end.

**Shipped this phase:** campaign lifecycle (`PATCH` · pause/resume/archive/duplicate · `DELETE` ·
`estimate`) · contacts `PATCH`/`DELETE` + list `q`/`source`/`limit`/`offset` · `enrollments/bulk-approve`
· inbox AI `POST /inbox/{id}/draft` + `GET /inbox/{id}/summary` + `POST /inbox/{id}/read` (real unread
via `enrollment.last_read_at`) · settings `GET/PATCH /settings/workspace`, connection
connect/disconnect/reauth, member invite/role/remove (org-admin gated) · `POST /notifications/read`
(unread vs `user.notifications_seen_at`) · **persisted `audit_event`** + `GET /audit`, written on
approve/send/handoff/opt-out/bulk-approve · agent seams `draft_reply` + `summarize` (stubs).
Migration `a611023b1e40` adds `audit_event`, `enrollment.last_read_at`, `app_user.notifications_seen_at`.

### Auth — ✅ complete
`POST /auth/login` · `GET /auth/callback` · `GET /auth/me` · `POST /auth/logout` · `POST /auth/dev-login`

### Workspaces / tenancy
✅ `POST /organizations` · `GET /workspaces` · `POST /workspaces` · `GET /workspaces/{id}`
(rename now lives on `PATCH /settings/workspace`)

### Contacts
✅ `GET /contacts` (+ `q`/`source`/`limit`/`offset`) · `GET /contacts/{id}` · `POST /contacts/import` · `POST /contacts/sample` · `PATCH /contacts/{id}` · `DELETE /contacts/{id}`
⬚ notes / tags columns — optional, deferred

### Campaigns
✅ `POST /campaigns` · `GET /campaigns` · `GET /campaigns/{id}` · `PATCH /campaigns/{id}` · `POST /{id}/pause|resume|archive|duplicate` · `DELETE /{id}` · `GET /{id}/estimate` · `POST /{id}/rank` · `GET /{id}/enrollments` · `POST /{id}/enroll`
⬚ `POST /campaigns/{id}/source` (stub SourceAdapter) — deferred (rank covers ranking)

### Enrollment
✅ `POST /enrollments/{id}/approve|handoff|opt-out` · `POST /enrollments/bulk-approve`
⬚ `POST /{id}/snooze` — optional, deferred

### Messaging / Inbox
✅ `GET /approvals` · `POST /messages/{id}/approve` · `PATCH /messages/{id}` · `GET /inbox` · `GET /inbox/{id}` · `POST /inbox/{id}/reply` · `POST /inbox/{id}/draft` · `GET /inbox/{id}/summary` · `POST /inbox/{id}/read` · `GET /enrollments/{id}/messages` · `POST /webhooks/reply`

### Settings
✅ `GET /settings/members` · `POST /settings/members/invite` · `PATCH /settings/members/{id}` · `DELETE /settings/members/{id}`
✅ `GET /settings/connections` · `POST /settings/connections/{provider}/connect` · `POST /settings/connections/{id}/disconnect` · `POST /settings/connections/{id}/reauth`
✅ `GET/PATCH /settings/workspace` (autonomy default, sending window, daily caps, brand voice → `Workspace.settings`)

### Read surfaces — ✅ complete
`GET /dashboard/summary` · `GET /analytics` · `GET /search` · `GET /notifications` · `POST /notifications/read`

### Audit (compliance) — ✅
✅ persisted `audit_event` table, written on approve/send/handoff/opt-out/bulk-approve · `GET /audit` (org-scoped)

### Runtime / admin — ✅ exists
`POST /admin/run-due` · `POST /admin/enrollments/{id}/fast-forward`

### Agents & channels — seams ✅ (stubs)
- `messaging.agents`: `write_message` · `classify_reply` · **`draft_reply`** · **`summarize`** — single import-point seam; real Claude replaces the bodies (or a config-selected factory) with no caller change.
- `platform/channels`: SMTP→Mailpit + stub LinkedIn today. A formal `get_channel()` factory + `platform/agents` Protocol/factory remains a light follow-up (the function seam already makes swapping mechanical).

---

## Part B — Client connection (OpenAPI types + TanStack Query)

**STATUS — essentially complete (gate-green both sides):**
- **Keystone done:** typed Pydantic response models on **every** module (contacts, campaigns,
  enrollment, messaging/inbox, dashboard, analytics, notifications, settings, audit, search) — all
  verified byte-identical to the old dicts. OpenAPI is fully typed; `npm run gen:api` →
  `src/lib/api/schema.d.ts` (3.9k lines).
- **Client foundation:** `openapi-typescript` + `openapi-fetch` + `@tanstack/react-query`; typed
  `client` with an **X-Workspace-Id middleware** mirrored from `WorkspaceProvider`;
  `QueryClientProvider` in `App`.
- **Hooks** (`src/lib/api/queries.ts`): every resource — queries + mutations with workspace-namespaced
  keys and cross-resource invalidation.
- **All pages migrated** off `useWorkspaceData`/`workspacePost`: dashboard, contacts, contact-detail,
  campaigns, campaign-builder, campaign-detail, inbox, approvals, pipeline, analytics, settings,
  notifications-bell, command-palette. (grep confirms zero remaining usages.)
- **Wired (previously visual/looped):** bulk-approve, connection connect/disconnect/reauth, member
  invite/remove, workspace-settings persistence, contact add-to-campaign, create-campaign — all real.
- **Polling:** inbox (25s) + notifications (30s) via React Query `refetchInterval`.

**Remaining (enhancements, not blockers):** net-new UI affordances whose hooks/endpoints already
exist — campaign lifecycle buttons (pause/resume/duplicate/delete/edit), AI "Draft with AI" button in
the inbox composer (`useDraftReply`), inline contact edit (`useUpdateContact`), an Audit page
(`useAudit`), member-role change UI. Plus the cross-cutting **error envelope** (FastAPI exception
handler → toast). Everything else in Part B is done.

### Original plan

1. **Generate types** — add `openapi-typescript` (dev) + `openapi-fetch` (runtime). Script
   `npm run gen:api` → `src/lib/api/schema.d.ts` from `http://localhost:8901/openapi.json`. A single
   typed `client` (openapi-fetch) with **middleware** that injects `X-Workspace-Id` from the workspace
   context and `credentials: include`.
2. **TanStack Query** — add `@tanstack/react-query` + `QueryClientProvider`. One hook module per
   resource (`useContacts`, `useContact`, `useCampaign(id)`, `useInbox`, `useConversation(id)`,
   `useApprovals`, `useAnalytics`, `useSettings…`) wrapping the typed client, with stable **query keys**
   that include the workspace id (so switching workspaces refetches automatically).
3. **Mutations + invalidation** — `useApprove`, `useSendReply`, `useHandoff`, `useRankCampaign`,
   `useEnroll`, `useSaveCampaign`, `useEditDraft`, `useConnect`, `useInvite`, etc. Each invalidates the
   right keys (approve → inbox + approvals + analytics + notifications). Optimistic where it helps
   (approve/skip, handoff).
4. **Migrate pages** off `useWorkspaceData` / raw `api` → the query hooks. `DataError` + skeletons
   collapse into one `<Async>` wrapper driven by Query's `isLoading/isError`.
5. **Wire the currently-visual actions**: campaign edit/pause/duplicate · contact edit/delete · bulk
   approve · connection connect/disconnect · member invite/role · autonomy persistence · workspace
   create (switcher) · AI "Draft with AI" + conversation summary (call the new endpoints) ·
   notification read + deep-link to the conversation.
6. **Realtime** — React Query `refetchInterval` polling for inbox + notifications (~20–30s). SSE/
   WebSocket is a later upgrade.

---

## Part C — Cross-cutting (alpha-scoped)

- **Typed response models everywhere** (Phase 1) — the foundation for the generated client.
- **Pagination / filter / sort** on list endpoints (`limit/offset` + filters) — contacts, campaigns,
  enrollments, inbox, audit.
- **Error envelope** — exception handlers → `{ "error": { code, message } }`, surfaced as toasts.
- **AuthZ** — consistent workspace scoping + `require_org_admin` on settings/admin writes; **RLS stays
  deferred** to a dedicated hardening pass.
- **Governor** — wire daily caps + business-hours window from `Workspace.settings` (interface already
  there; real LinkedIn caps later).
- **Tests** — pytest for every new endpoint (happy + auth + cross-workspace 404/403); keep `make check`
  green per phase.
- **OpenAPI hygiene** — explicit `operation_id`s + tags so the generated client reads well.

---

## Part D — Sequencing (each phase shippable, gate-green)

1. **Type the API + extract interfaces** — Pydantic response models on all endpoints; `platform/agents`
   + `platform/channels` protocols + factories. *No behavior change; richer OpenAPI.*
2. **Client foundation** — `gen:api`, `openapi-fetch` client + workspace middleware, TanStack Query
   provider; migrate the **read** pages to query hooks. *No behavior change; typed + cached.*
3. **Write endpoints + wiring** (the bulk) — campaign lifecycle, contacts CRUD, connections, members,
   settings persistence, bulk approve, AI draft/summary, workspace create — backend endpoint + client
   mutation, wired into the existing UI, phase-by-phase per module.
4. **Cross-cutting** — pagination/filter, error envelope, audit table, notifications read-state,
   governor caps, role checks.
5. **Realtime + polish** — polling, optimistic updates, unified async/empty/error states.
6. **Tests + e2e + CI** — endpoint tests; a Playwright **golden path** (login → onboarding → sample →
   campaign → rank → approve → send → reply → hand-off → analytics); `gen:api` drift-check in CI.

**Deferred (explicit, post-alpha):** real Claude agents · Gmail/Microsoft OAuth + real send · LinkedIn
via Unipile + inbound webhooks · external sourcing providers · Postgres RLS · observability/metrics ·
SSE/WebSocket realtime · sales-vertical terminology · dark theme · responsive/mobile.

**Recommended start:** Phase 1 (type the responses + interfaces) — it unblocks the generated client and
is pure cleanup with the test suite as the guardrail.
