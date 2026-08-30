# Manual QA — driving the loop

This walks the alpha backend end to end: **import contacts → rank into a campaign → approve →
draft → send → reply → hand-off**. Everything runs locally on the 89xx port band.

## 0. Signing in

Three doors, all landing on the same user and the same sealed session cookie:

| Door | What it needs | Where a new user ends up |
|---|---|---|
| Email + password | nothing (Mailpit catches the mail) | `/verify-email` → click the link → app |
| Continue with Google | `WORKOS_*` + Google OAuth configured in WorkOS | `/signup` completion form → app |
| Continue with Microsoft | `WORKOS_*` + Microsoft OAuth configured in WorkOS | `/signup` completion form → app |

LinkedIn is **not** a way in. It is connected from Settings → Connections as a *sending seat*, by
someone already signed in.

**Invited teammates** are a fourth arrival, not a fourth door: Settings → Members → Invite creates
a *pending* seat and emails a signed link (7 days). Clicking it is what proves the address — until
then the seat cannot be signed in to, and cannot be linked to a Google/Microsoft identity. Inviting
the same pending address again re-sends the link. Worth checking: invite someone, then try
"Continue with Google" as that person *without* clicking the link — they must land in their own new
org, not the inviting one.

Once they *have* accepted, "Forgot password?" will send them a **"Set your Sourcewell password"**
link even though they never had one — that is how a teammate with no Google or Microsoft account
gets back in after their first session expires. It is refused for an address nobody has proven, so
an unaccepted invite still gets nothing.

`POST /organizations` (the unauthenticated org bootstrap used below) is **local only** and 404s
anywhere else. Real accounts come from `/auth/signup` or an OAuth sign-in.

### The OAuth signup flow

`/auth/login/{google|microsoft}` → WorkOS → `/auth/callback`. The callback decides where you land:

- **Returning user** — matched on `sso_subject` (the WorkOS user id). Straight into the app.
- **First time** — provisioned right there: a user with `email_verified_at` set (the provider
  proved the address, so no confirmation mail), an org named off the email domain as a
  *placeholder*, a default workspace, and an org-admin membership. `profile_completed_at` is left
  null, which is what sends the browser to `/signup`.

That form is the same page as email signup, in completion mode: email prefilled and read-only, no
password fields, first/last name and avatar prefilled from the provider where it gave them. It
posts to `POST /auth/complete-profile` (authenticated), which fills in the profile and **renames
the org** from the company name typed there. Until it's posted, `GET /auth/me` reports
`profile_complete: false` and the app keeps routing back to the form.

Worth checking by hand:

- Sign in with Google twice — the second time must go straight in, with **one** org, not two.
- Sign up with a password at `x@acme.com`, then "Continue with Google" as `x@acme.com` — it links
  to the existing account rather than making a second one.
- Invite a teammate, then have them sign in with Google — they join *your* org and are never asked
  for a company name.
- Abandon the completion form and reload — you land back on it, still signed in.

## 0. Bring up infra + API

```bash
make up        # Postgres :8902, Mailpit :8904 (web) / :8905 (smtp)
make install   # uv sync
make migrate   # alembic upgrade head
make dev       # API on http://localhost:8901   (interactive docs at /docs)
```

Optional, in a second terminal:

```bash
make worker    # background runtime: polls due enrollments every 10s and ticks them
```

You do **not** need the worker for QA — the `POST /admin/run-due` endpoint advances the runtime
by hand (one transition per call), which makes each step observable. The worker is the same
`run_due` loop on a timer.

Useful URLs:
- API docs (try every endpoint here): http://localhost:8901/docs
- Mailpit (every sent email lands here): http://localhost:8904

## Dev auth (no login yet)

Auth is two headers:
- `X-User-Id` — returned by `POST /organizations` (the admin user). Identifies the caller.
- `X-Workspace-Id` — the workspace you're acting in. Required by every contact/campaign endpoint.

An org admin can act in any workspace of their org; a plain member only in workspaces they're
assigned to.

## 1. Sign up an org + create a workspace

```bash
# returns admin_user_id  ->  use as X-User-Id
curl -sX POST localhost:8901/organizations -H 'Content-Type: application/json' \
  -d '{"org_name":"Acme","slug":"acme","admin_email":"admin@acme.com","admin_name":"Admin"}'

# returns the workspace id  ->  use as X-Workspace-Id
curl -sX POST localhost:8901/workspaces \
  -H 'Content-Type: application/json' -H 'X-User-Id: <UID>' \
  -d '{"name":"Backend Hiring","kind":"team"}'
```

## 2. Load contacts

```bash
# fastest: generate sample contacts for QA
curl -sX POST localhost:8901/contacts/sample \
  -H 'Content-Type: application/json' -H 'X-User-Id: <UID>' -H 'X-Workspace-Id: <WS>' \
  -d '{"count":5}'

# or import your own
curl -sX POST localhost:8901/contacts/import \
  -H 'Content-Type: application/json' -H 'X-User-Id: <UID>' -H 'X-Workspace-Id: <WS>' \
  -d '{"contacts":[{"full_name":"Jane Doe","title":"Senior Backend Engineer","company":"Acme","email":"jane@example.com","skills":["python","postgres"]}]}'
```

## 3. Create a campaign

`criteria` is what the Evaluator scores against; `sequence` is the touchpoints. Use `delay_days: 0`
on every step so you can step the whole sequence by hand without waiting real days.

`autonomy_level`:
- `manual` / `assisted` — every drafted message waits in the approval queue (`GET /approvals`).
- `full` — drafts auto-approve and send on the next tick (no manual message approval).

```bash
curl -sX POST localhost:8901/campaigns \
  -H 'Content-Type: application/json' -H 'X-User-Id: <UID>' -H 'X-Workspace-Id: <WS>' \
  -d '{
    "name":"Backend hire",
    "criteria":{"skills":["python"],"titles":["engineer"]},
    "sequence":[
      {"channel":"email","delay_days":0,"subject":"Hi {first_name}","body":"Saw your work at {company} — open to a chat?"},
      {"channel":"email","delay_days":0,"subject":"Following up, {first_name}","body":"Still keen?"}
    ],
    "autonomy_level":"assisted",
    "from_email":"recruiter@acme.com"
  }'
```

Templates `{first_name}`, `{name}`, `{company}`, `{title}` fill from the contact.

## 4. Rank → review → approve the lead

```bash
# scores every workspace contact into 'proposed' enrollments (returns them, best first)
curl -sX POST localhost:8901/campaigns/<CID>/rank -H 'X-User-Id: <UID>' -H 'X-Workspace-Id: <WS>'

# the proposed pipeline (filter by state)
curl -s 'localhost:8901/campaigns/<CID>/enrollments?state=proposed' -H 'X-User-Id: <UID>' -H 'X-Workspace-Id: <WS>'

# approve a lead into the active sequence
curl -sX POST localhost:8901/enrollments/<EID>/approve -H 'X-User-Id: <UID>' -H 'X-Workspace-Id: <WS>'
```

## 5. Drive the runtime: draft → approve → send

`POST /admin/run-due` processes every due enrollment once. Each call = one transition.

```bash
curl -sX POST localhost:8901/admin/run-due -H 'X-User-Id: <UID>' -H 'X-Workspace-Id: <WS>'   # active -> draft a touchpoint

# approve_each only: the draft now waits here
curl -s localhost:8901/approvals -H 'X-User-Id: <UID>' -H 'X-Workspace-Id: <WS>'
curl -sX POST localhost:8901/messages/<MID>/approve -H 'X-User-Id: <UID>' -H 'X-Workspace-Id: <WS>'

curl -sX POST localhost:8901/admin/run-due -H 'X-User-Id: <UID>' -H 'X-Workspace-Id: <WS>'   # scheduled -> SEND
```

Open **http://localhost:8904** — the email is there. The send is a real SMTP delivery to Mailpit.

```bash
# the full thread for an enrollment
curl -s localhost:8901/enrollments/<EID>/messages -H 'X-User-Id: <UID>' -H 'X-Workspace-Id: <WS>'
```

## 6. Feed in a reply → hand-off

Replies arrive on the public, HMAC-signed receiver — the same door a provider uses, so QA
exercises the real path rather than a simulator. Set `INBOUND_WEBHOOK_SECRET` in `backend/.env`
first (any string), then sign the exact request body:

```bash
SECRET=your-inbound-webhook-secret
# "interested" / "let's talk" -> handed_off ;  "not interested" / "unsubscribe" -> opted_out
BODY='{"enrollment_id":"<EID>","text":"Interested, let'\''s talk!"}'
SIG=$(printf '%s' "$BODY" | openssl dgst -sha256 -hmac "$SECRET" | awk '{print $2}')
curl -sX POST localhost:8901/webhooks/inbound \
  -H 'Content-Type: application/json' -H "X-Signature: $SIG" -d "$BODY"
# -> {"status":"queued"}
```

The receiver only *records* the reply. Classification and the hand-off run on the worker, so
route it before checking the inbox — `/admin/run-due` handles parked replies first, then ticks:

```bash
curl -sX POST localhost:8901/admin/run-due -H 'X-User-Id: <UID>' -H 'X-Workspace-Id: <WS>'

# the inbox view across enrollments
curl -s localhost:8901/inbox -H 'X-User-Id: <UID>' -H 'X-Workspace-Id: <WS>'
```

LinkedIn replies come in the same way via `/webhooks/unipile` (token in the query string), which
Unipile is subscribed to automatically at boot — see `services/outreach/receiving.py`.

## State machine (what each tick does)

```
proposed --approve--> active --tick--> (draft touchpoint)
   approve_each: -> awaiting_approval --approve msg--> scheduled
   auto:         -> scheduled
scheduled --tick--> SEND, advance step --> awaiting_reply
awaiting_reply --tick--> more steps? back to active : completed
inbound reply --> handed_off (interested) | opted_out (opt-out)
```

`state` + `next_run_at` are the source of truth; no external scheduler. With `delay_days: 0`,
repeated `run-due` calls step the whole sequence. If you set real delays, use
`POST /admin/enrollments/<EID>/fast-forward` to pull a future touchpoint into the present.

## Shortcut

`python3 scripts/smoke.py` runs the whole auto-mode loop and asserts an email reached Mailpit
(the same script used to verify this build).

## Notes / known alpha gaps
- Agents (Evaluator/Writer/Responder) are deterministic stubs behind real interfaces — Claude slots in later.
- LinkedIn channel is stubbed; email is the only live channel.
- Postgres row-level security is deferred to a hardening pass; access is enforced in the app layer.
