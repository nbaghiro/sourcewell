# Sourcewell — Frontend Implementation Plan

Building the React dashboard on top of the working alpha backend (API `:8901`, worker, Postgres
`:8902`, Mailpit `:8904`). Every phase is **independently shippable**: it lands with its own
automated tests *and* a manual QA script, and `make web-check` + the phase's Playwright golden
path stay green before the next phase starts — the same discipline the backend phases used.

---

## Locked decisions

| Area | Choice | Why |
|---|---|---|
| Component foundation | **shadcn/ui** (Radix primitives + Tailwind) | Owned, copy-in components on accessible Radix primitives; themed purely via CSS variables → swap themes without touching components. One shared primitive set across every page. |
| Theming scope | **Token system, light + dark** | Ship two modes now; the token contract makes a third theme a new CSS block with zero component edits. |
| Default design language | **Refined enterprise** | Neutral slate, tight table rows, one restrained accent, small radius. Reads as a serious internal tool for bank/enterprise talent teams. |
| Build / lang | Vite + React 18 + TypeScript (port **8900**) | Already scaffolded. |
| Routing | React Router v6 (data router) | Standard, route-level guards + loaders. |
| Server state | TanStack Query | Caching, mutations, invalidation — no hand-rolled fetching. |
| Forms | react-hook-form + zod | shadcn `Form` integrates both; one validation story. |
| Tables | TanStack Table | Contacts / enrollments grids. |
| Icons | lucide-react | shadcn default. |
| Types | `openapi-typescript` from `/openapi.json` | FE types generated from the live FastAPI schema → FE/BE never drift. |
| Unit/component tests | Vitest + React Testing Library + MSW | Fast, network-free. |
| E2E tests | Playwright against the live docker stack | The UI equivalent of `scripts/smoke.py`. |

---

## Theming architecture (the swappable design language)

The rule that makes themes swappable: **components only ever use *semantic* Tailwind classes**
(`bg-background`, `text-muted-foreground`, `border-border`, `bg-primary`) — never literal colors
(`bg-slate-900`). Swapping a theme = swapping the values behind those names.

```
src/styles/tokens.css         ← single source of truth
  :root        { --background, --foreground, --card, --popover,
                 --primary, --primary-foreground, --secondary,
                 --muted, --muted-foreground, --accent, --border,
                 --input, --ring, --destructive, --success, --warning,
                 --radius }                          ← LIGHT (Refined enterprise)
  .dark        { ...same token names, dark values }  ← DARK
  [data-theme] { ...same token names }               ← future themes drop in here, no JS/TSX change

tailwind config → maps utilities to vars:
  colors.background = 'hsl(var(--background))', colors.primary = 'hsl(var(--primary))', …
  borderRadius.lg/md/sm = 'var(--radius)' derived

ThemeProvider (next-themes-style)
  toggles <html class="dark"> / data-theme, persists to localStorage, respects prefers-color-scheme
```

**Refined-enterprise default tokens** (illustrative, finalized in Phase 8):
neutral slate surfaces, near-black foreground, a single restrained accent (deep indigo/blue),
`--radius: 0.375rem`, dense spacing scale, subtle borders over shadows, fast/quiet motion.

**Enforcement:** a lint/test guard (Phase 0) scans `src/components` and `src/features` for
disallowed literal color utilities and fails the build — so the no-raw-color contract can't rot.

A short **design-language doc** (`frontend/DESIGN.md`) captures the token roles, type scale,
spacing, elevation, and motion so a new theme is a fill-in-the-blanks exercise.

---

## Directory layout (fits the existing scaffold)

```
frontend/src/
  app/            router, providers (Query · Theme · Auth), root layout (sidebar + topbar)
  components/ui/  shadcn primitives: button, card, table, dialog, dropdown-menu, input,
                  textarea, form, label, badge, tabs, toast, sheet, skeleton, select, switch,
                  tooltip, separator, avatar, command
  components/     app composites: DataTable, PageHeader, StatChip, ScoreBar, StateBadge,
                  EmptyState, ConfirmDialog, TokenTextarea (sequence body w/ {first_name} chips)
  features/
    auth/         dev sign-in, session store, workspace switcher
    workspaces/   list + create
    contacts/     list, import, sample
    campaigns/    list, create wizard (criteria + sequence builder), detail
    sourcing/     ranking review (campaign detail tab)
    approvals/    message approval queue + preview
    inbox/        threads, reply, hand-off
    pipeline/     enrollment board + runtime controls
    settings/     theme, autonomy, connections (stub)
  lib/api/        typed client, endpoint fns, query keys, generated types
  lib/hooks/      useAuth, useWorkspace, useTheme
  styles/         tokens.css, globals.css
```

---

## Phases

Each phase below states: **Goal · Build · Endpoints · Automated tests · Manual QA · Done when**.

### Phase 0 — Foundation, theming, CORS
*No product features yet — but fully QA-able via a themed kitchen-sink page.*
- **Build:** Tailwind v4 + shadcn init; `tokens.css` (light/dark) + Tailwind var mapping;
  `ThemeProvider` + toggle; Query/Router/Auth providers; typed API client (injects
  `X-User-Id`/`X-Workspace-Id`); app shell (collapsible sidebar + topbar); `openapi-typescript`
  wired; no-raw-color guard; `/_kitchen-sink` route rendering every primitive.
- **Backend:** add CORS middleware (allow origin `:8900`, headers `X-User-Id`,`X-Workspace-Id`).
- **Automated:** Vitest — toggling theme sets `.dark`; api client attaches headers (MSW);
  guard test fails on a literal color. Typecheck on generated types.
- **Manual QA:** open `:8900` → themed shell renders; flip light/dark in the topbar (instant, no
  flicker, persists on reload); `/_kitchen-sink` looks right in both themes.
- **Done when:** theme swaps with zero component edits; client reaches `/health`; guard is green.

### Phase 1 — Dev auth + workspace switching
- **Build:** sign-in screen (create org *or* paste existing user id), session persisted to
  localStorage, route guard, topbar workspace switcher that sets `X-Workspace-Id`.
- **Endpoints:** `POST /organizations`, `GET /me`, `GET /workspaces`, `POST /workspaces`.
- **Automated:** RTL sign-in flow (MSW); guard redirects unauthenticated; switching workspace
  updates the outgoing header (client spy).
- **Manual QA:** sign up org → land in app → create + switch workspace → reload keeps session.
- **Done when:** the rest of the app runs authenticated and workspace-scoped.

### Phase 2 — Contacts
- **Build:** contacts `DataTable` (sortable, skills chips); import form (paste/CSV); "Generate
  sample" button; empty state.
- **Endpoints:** `GET /contacts`, `POST /contacts/import`, `POST /contacts/sample`.
- **Automated:** table renders rows; import/sample mutations invalidate + refetch the list.
- **Manual QA:** Generate 5 sample → rows appear; import a custom contact → shows up.
- **Done when:** a workspace can be populated with contacts from the UI.

### Phase 3 — Campaigns (create + sequence builder)
- **Build:** campaign list; "New campaign" wizard — name, criteria (skills/titles chip inputs),
  autonomy toggle (`approve_each`/`auto`), `from_email`, and an ordered **sequence builder**
  (add/remove/reorder touches: channel, delay_days, subject, body with `{first_name}` token
  chips); campaign detail header (status, counts).
- **Endpoints:** `POST /campaigns`, `GET /campaigns`, `GET /campaigns/{id}`.
- **Automated:** zod validation; sequence add/remove/reorder; create payload shape matches API.
- **Manual QA:** build an `approve_each` campaign with a 2-step sequence → listed + on detail.
- **Done when:** campaigns are fully configurable from the UI.

### Phase 4 — Sourcing / ranking review
- **Build:** campaign-detail "Rank" action → proposed enrollments list with `ScoreBar` +
  rationale + contact; approve a lead (and bulk-approve selection).
- **Endpoints:** `POST /campaigns/{id}/rank`, `GET /campaigns/{id}/enrollments?state=proposed`,
  `POST /enrollments/{id}/approve`.
- **Automated:** rank populates list sorted by score; approve removes the row from proposed.
- **Manual QA:** rank → review scored leads → approve top → it leaves the proposed tab.
- **Done when:** human-in-the-loop lead selection works end to end.

### Phase 5 — Approvals queue (message review)
- **Build:** approvals page — drafts awaiting approval with contact + campaign context, message
  preview (subject/body), approve; empty state. *(Optional: edit-before-send, see backend note.)*
- **Endpoints:** `GET /approvals`, `POST /messages/{id}/approve`.
- **Backend (optional):** `PATCH /messages/{id}` to edit a draft before approval.
- **Automated:** queue renders drafts; approve removes from queue; empty state when none.
- **Manual QA:** with the worker running, approve a lead → draft lands in Approvals → approve →
  worker sends → **email appears in Mailpit**.
- **Done when:** the approve-each gate is operable from the UI.

### Phase 6 — Inbox / threads / replies
- **Build:** inbox list (`GET /inbox`) grouped by enrollment with last message + `StateBadge`;
  thread view; a "simulate reply" control (for QA) posting to the reply webhook; hand-off badge.
- **Endpoints:** `GET /inbox`, `GET /enrollments/{id}/messages`, `POST /webhooks/reply`.
- **Automated:** thread renders messages in order; reply intent flips the state chip.
- **Manual QA:** open inbox → thread → post an "interested" reply → enrollment → `handed_off`.
- **Done when:** conversations + outcomes are visible.

### Phase 7 — Pipeline & runtime visibility + autonomy controls
- **Build:** campaign **pipeline board** (columns by `EnrollmentState`, counts, `next_run_at`);
  runtime controls — admin-only "Run due now" + per-lead "fast-forward"; autonomy switch.
- **Endpoints:** `GET /campaigns/{id}/enrollments` (all states), `POST /admin/run-due`,
  `POST /admin/enrollments/{id}/fast-forward`.
- **Automated:** board groups by state with correct counts; admin-only controls hidden for
  non-admins (role from `GET /me`).
- **Manual QA:** watch leads move across columns as the worker ticks; fast-forward a delayed touch.
- **Done when:** operators can see and nudge the state machine.

### Phase 8 — Theming polish + design-language doc
- **Build:** finalize Refined-enterprise tokens, dark parity, focus rings, density, motion; theme
  control in settings + topbar; write `frontend/DESIGN.md` token contract (new theme = new block).
- **Automated:** Playwright visual snapshots of key pages in light + dark; axe a11y scan; contrast
  checks; the no-raw-color guard over the whole tree.
- **Manual QA:** toggle light/dark on every page; confirm no leaked hardcoded colors.
- **Done when:** cohesive, accessible, swap-ready theming.

### Phase 9 — E2E hardening & states
- **Build:** loading skeletons, empty states, error toasts, optimistic updates, 401/403 handling,
  retries; a full Playwright golden path through the whole funnel against the live stack.
- **Automated:** Playwright golden path green in CI (docker + api + worker + vite); component
  coverage threshold.
- **Manual QA:** run the entire `qa-guide.md` funnel — signup → contacts → campaign → rank → approve →
  send → reply → hand-off — **entirely through the UI**, watching Mailpit.
- **Done when:** alpha-quality dashboard.

---

## Cross-cutting testing strategy
- **Component/unit:** Vitest + RTL + MSW — every change. `make web-test`.
- **Type contract:** regenerate `lib/api/types` from `/openapi.json`; typecheck catches FE/BE drift.
- **E2E:** Playwright against the real docker stack; one golden path per product phase.
- **Theming guard:** ESLint rule / Vitest scan blocks literal color utilities in components.
- **Per phase:** ships its own component tests + a manual QA script appended to a `FRONTEND-QA.md`.

## Backend touch-ups (tracked alongside the FE phases)
- **Phase 0:** CORS middleware.
- **Phase 1:** optional `GET /users` for a nicer dev-login picker.
- **Phase 3/5:** optional `PATCH /campaigns/{id}` and `PATCH /messages/{id}` (edit before approve).
- **Ongoing:** pagination on list endpoints as data grows.
- **Post-alpha:** real auth (sessions/SSO), connections endpoints (LinkedIn/email OAuth),
  suppression enforcement, LinkedIn channel.

## Tooling / commands (extend the Makefile + CI)
- `web-install`, `web` (vite dev), `web-test` (vitest), `web-e2e` (playwright), `web-lint`,
  `web-typecheck`, `web-check` (lint + typecheck + test).
- CI: a frontend job (lint + typecheck + vitest) and an e2e job (boot docker + api + worker + vite,
  run Playwright golden path).

---

## Suggested order & rationale
Foundation (0) and auth (1) are prerequisites; **2→6 follow the product funnel** so each phase
adds a usable slice you can demo; **7** makes the autonomous engine observable; **8–9** harden look
and reliability. The earliest end-to-end "wow" (a real email from a UI click) lands at **Phase 5**.
