# UI Roadmap — remaining work to the final product vision

Everything the UI renders already comes from the API (demo data sits fully behind it; real backend
swaps in with no FE change). This is the remaining UI surface, ordered by impact, with the
backend/demo work each piece needs.

## ✅ Done
Auth (WorkOS + dev login) · app shell (pinned sidebar/topbar) · Dashboard · Contacts list +
**Contact detail** (hero, stats, activity timeline) · Campaigns list + **node-based builder**
(linear/multi-channel, audience estimate, preview, exit rules, reorder) · Approvals queue (approve
works) · **3-pane Inbox messenger** (channel indicators, context rail, suggested replies) ·
Pipeline board · Settings (static) · `/components` library.

**Tier 1 (complete):** Campaign detail + ranking review · **workspace switcher** (sidebar dropdown,
persisted) · wired actions (messenger send/hand-off/opt-out, contacts generate-sample + import
modal, contact add-to-campaign, campaign rank + approve) · **account menu**. New action endpoints:
`POST /inbox/{id}/reply`, `/enrollments/{id}/handoff`, `/enrollments/{id}/opt-out`,
`/campaigns/{id}/enroll`. Shared components extracted: `lib/format.ts`, `components/brand-icons.tsx`,
`components/ui/segmented.tsx`; workspace context in `lib/workspace.ts` + `WorkspaceProvider`.

---

## Tier 1 — ✅ DONE

| Component | What it is | Backend | Demo data |
|---|---|---|---|
| **Campaign detail + ranking review** | The page when you open a campaign: overview + read-only sequence flow + state tabs (Proposed/Active/Awaiting/Handed-off) + the **"Rank → review proposed candidates"** step with fit bars + multi-select approve. The missing link between *create* and *pipeline*. | exists: `/campaigns/{id}`, `/{id}/rank`, `/{id}/enrollments`, `/enrollments/{id}/approve` | have it |
| **Workspace switcher** | The sidebar `org · workspace` becomes a real dropdown that sets the active workspace (persisted). Affects every scoped page. | `/workspaces` exists; persist choice client-side | 2 workspaces seeded ✓ |
| **Wire the actions** | Make the visual buttons real: messenger **Send reply / Hand off / Mark not interested**, suggested-reply **Send/Edit/Discard**, approvals **Skip**, contact **Message / Add to campaign**, **Generate sample**. | need: send manual reply, hand-off, opt-out, enroll-contact endpoints; rest exist | have it |
| **Account menu** | Topbar avatar → dropdown (profile, settings, theme, sign out) instead of a bare Sign-out button. | uses `/auth/me`, `/auth/logout` | have it |
| **Contact import modal** | CSV paste / upload → `POST /contacts/import` (button exists, no modal). | `/contacts/import` exists | n/a |

## Tier 2 — Make it feel finished

| Component | Status |
|---|---|
| **Approvals → triage** | ✅ master-detail, **edit-before-send** (`PATCH /messages/{id}`), approve/skip |
| **Pipeline polish** | ✅ avatars on cards + admin **Run-due-now** (`POST /admin/run-due`) |
| **Settings (real)** | ✅ Connections + Members from API (`/settings/connections`, `/settings/members`); seeded teammates (Dana/Riley/Sam) + Gmail/LinkedIn connections. *Still visual:* connect/disconnect, invite, autonomy persistence. |
| **Empty / first-run** | ✅ empty **GTM Outreach** workspace → Dashboard onboarding (3 steps, generate-sample wired) |
| **Notifications** | ✅ topbar bell + `GET /notifications` (recent replies + hand-offs + approvals-waiting), unread badge |
| **States consistency** | ✅ `useWorkspaceData` now returns `error`; shared `<DataError onRetry>`; wired on dashboard/contacts/campaigns |

**Tier 2 is complete.** Remaining as visual-only (small backend follow-ups): connection connect/disconnect, member invite, autonomy persistence, deep-linking notifications to a specific conversation.

## Tier 3 — Premium / enterprise

| Component | Status |
|---|---|
| **Command palette (⌘K)** | ✅ global search (`GET /search`) across contacts / campaigns / conversations; ⌘K + topbar trigger, arrow-key nav |
| **Reporting & analytics** | ✅ `/analytics` page + endpoint: funnel (sourced→contacted→replied→handed-off), reply-rate by channel, per-campaign performance |
| **Audit log** | ✅ delivered as the Analytics **Activity** feed (recent sends + replies, `GET /analytics`) |
| **Sales-vertical mode** | ⬚ terminology toggle (candidate↔lead) — workspace setting |
| **Dark theme** | ⬚ deferred (token system is ready; add a `.dark` block + switcher) |
| **Responsive / mobile** | ⬚ sidebar collapse + messenger pane stacking on narrow widths |

---

## Backend touch-ups the UI needs (small, mostly)
- `PATCH /campaigns/{id}` + campaign actions (pause / resume / duplicate / archive); load-campaign-into-builder for **edit**.
- Conversation actions: send a manual outbound reply, **hand-off**, **mark not interested**, enroll-contact-into-campaign.
- `PATCH /messages/{id}` (edit a draft before approving).
- `POST /campaigns/estimate` (real audience count; currently client-side).
- AI surfaces as real endpoints (stub now → Claude later): **draft reply**, **conversation summary**, **suggested reply**.
- Connections CRUD · members/invite · settings persistence · notifications feed · search · analytics.

## Demo-data extensions
- **Seed teammates** (a few org users + memberships) → powers Members, assignment, "handed off to".
- **Seed connections** (Gmail connected, LinkedIn pending) → Settings looks real.
- **Seed notifications** + an **audit feed**.
- **Distinct names** — the statistical seed and hand-authored conversations share name pools, so duplicates appear (two "Ingrid Rossi"); give conversation contacts unique names.
- Optional: company logos, a deliberately **empty second workspace** to demo onboarding.

---

## Recommended build order
1. **Campaign detail + ranking review** (closes the core loop) → 2. **Workspace switcher** → 3. **Wire the actions** (messenger/approvals/contact) with the small new endpoints → 4. **Account menu + import modal** → 5. **Approvals triage + Pipeline polish** → 6. **Settings (real)** → 7. **Notifications + states** → 8. premium tier (search, analytics, audit).
