# Manual QA — driving the loop

This walks the alpha backend end to end: **import contacts → rank into a campaign → approve →
draft → send → reply → hand-off**. Everything runs locally on the 89xx port band.

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

`autonomy_mode`:
- `approve_each` — every drafted message waits in the approval queue (`GET /approvals`).
- `auto` — drafts auto-approve and send on the next tick (no manual message approval).

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
    "autonomy_mode":"approve_each",
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

## 6. Simulate a reply → hand-off

There's no inbound email poller yet, so post the reply directly:

```bash
# "interested" / "let's talk" -> handed_off ;  "not interested" / "unsubscribe" -> opted_out
curl -sX POST localhost:8901/webhooks/reply \
  -H 'Content-Type: application/json' -H 'X-User-Id: <UID>' -H 'X-Workspace-Id: <WS>' \
  -d '{"enrollment_id":"<EID>","text":"Interested, let'\''s talk!"}'

# the inbox view across enrollments
curl -s localhost:8901/inbox -H 'X-User-Id: <UID>' -H 'X-Workspace-Id: <WS>'
```

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
