# Provider & Integration Plan — Unipile‑first, multi‑provider‑ready

> Verified external API calls + the finalized build plan. Companion to `agent-architecture.md`
> (the agents call everything through the `data` / `channel` / `inbound` seams below).

## 0 · Principles
- **Unipile is the spine**: sign‑in, LinkedIn search (sourcing), email + LinkedIn send, inbound webhooks — one integration for the MVP.
- **Auth = Unipile hosted‑auth** (connect LinkedIn at login → identity via `member_urn` + the seat `account_id`). Retire WorkOS. Returning login = session cookie, `type:"reconnect"` on expiry. No separate OIDC.
- **Three provider *roles***: `DataProvider` (search/enrich), `ChannelProvider` (send/reply), `ConnectionProvider` (seats/webhooks). Unipile fills all three; PDL/Apollo/Hunter = Data only; SMTP = Channel(email) floor. *(Cognism was evaluated and dropped — its endpoints sit behind a gated dev portal we can't confirm.)*
- **Per‑seat, not global**: resolve `account_id` from the `Connection` model per user (never `settings.unipile_account_id`).
- **Switch‑ready**: every data provider is a full, respx‑tested adapter, toggled by platform key / BYO key / per‑workspace selection. **Apollo = BYO‑only** (ToS). **PDL + Hunter = platform‑level OK**. **LinkedIn/Unipile = per‑user seat** (can't be platform‑hidden).

## 1 · Verified external API calls (each confirmed)

### Unipile — base `{dsn}/api/v1`, header `X-API-KEY`
| Purpose | Call |
|---|---|
| Connect seat / sign‑in | `POST /hosted/accounts/link` `{type, providers:["LINKEDIN"], api_url, expiresOn, notify_url, success/failure_redirect_url, name}` → `{url}`; notify → `{status, account_id, name}` |
| Identity | `GET /users/me?account_id=` → `{provider_id, public_identifier, member_urn, first_name, last_name, headline}` (**member_urn = identity key**) |
| Accounts | `GET /accounts`, `GET /accounts/{id}` |
| Search | `POST /linkedin/search?account_id={id}` body `{api, category:"people", keywords, …filters}` — **account_id in QUERY STRING** |
| Resolve filter IDs | `GET /linkedin/search/parameters` (text→LinkedIn IDs; needed for structured Sales‑Nav filters) |
| Enrich | `GET /users/{public_identifier}?account_id=` |
| Send LinkedIn (new) | `POST /chats` **multipart/form-data** `account_id, attendees_ids, text, linkedin[api]=classic, linkedin[inmail]=true` |
| Reply in chat | `POST /chats/{chat_id}/messages` multipart `text` |
| Invitation | `POST /users/invite` `{provider_id, account_id, message?}` |
| Send email | `POST /emails` multipart `account_id, to:[{display_name, identifier}], subject, body, cc?` |
| Register webhook | `POST /webhooks` `{request_url, source:"messaging"\|"email"\|"account"}` |
| Inbound events | messaging `message_received`; email `email.received`; account `CREDENTIALS`/connected |

### Data providers
| Provider | Call | Note |
|---|---|---|
| **PDL** | `POST /v5/person/search` `{query:<ES>, size}` · `GET /v5/person/enrich`, header `X-Api-Key` | ✅ correct as‑is |
| **Hunter** | `GET /v2/email-finder?domain&first_name&last_name&api_key` · `GET /v2/email-verifier` | ✅ correct as‑is |
| **Apollo** | `POST https://api.apollo.io/api/v1/mixed_people/search` · `POST /api/v1/people/match`, header **`x-api-key`** | ✅ fixed (base `/api/v1`, header auth), BYO‑only |

## 2 · Corrections to fold in (bugs in current adapters)
1. **Apollo**: base `/api/v1`, `x-api-key` header, BYO‑only. (Current adapter 401s/404s today.)
2. **Unipile search**: `account_id` → query string; structured filters need `/linkedin/search/parameters` ID resolution.
3. **Unipile send**: multipart (not JSON) + `linkedin[inmail]`; add `POST /emails`; persist `chat_id` for reply‑in‑thread.
4. **Unipile enrich**: pass `public_identifier`, not the full URL.

## 3 · Build phases

> **Status:** the data/channel/connection layer is ✅ built and CI‑green on `main` (143 tests), and
> **WorkOS is fully retired** in favour of LinkedIn‑only sign‑in via Unipile. What each phase shipped
> is noted inline. The **only** remaining work is the **live‑send cutover** (§3a) — staged for when
> manual QA pauses, since it rewrites the live send path. dev‑login still covers local/QA.

**Track A — Unipile MVP spine**
1. ✅ **Seams + seat resolver** — `ChannelProvider`/`ConnectionProvider` protocols in `ext/base.py`; `services/workspace/connections.py` resolves `account_id` from `Connection`; `UnipileProvider` takes `account_id`.
2. ✅ **Auth = Unipile sign‑in** — hosted‑auth `/auth/login → /auth/linkedin/notify → /auth/callback`, Fernet‑sealed session, `provision_from_linkedin` (identity via `member_urn`); **WorkOS fully retired** (dep, config, `workos_org_id` dropped). dev‑login kept for local/QA.
3. ✅ **Connection lifecycle** — account webhook (`CREDENTIALS`/disconnect → `needs_reauth`) in the inbound receiver.
4. ◑ **Channel send** — `UnipileChannel` (multipart `POST /chats` + reply + InMail + email `POST /emails`) + `Message.external_id`/`account_id` **built**; wiring it into the live `send_via_channel` is the **cutover** (§6).
5. ✅ **Inbound webhooks** — public token‑verified `POST /webhooks/unipile` → maps `account_id`+`chat_id`→Enrollment → `handle_reply`. (Webhook registration call happens at connect — cutover.)
6. ✅ **Sourcing finalize** — `account_id` in query, `api`/`category` body + cursor; enrich by `public_identifier`. (`fetch_job_postings` stays a best‑effort stub.)

**Track B — switch‑ready data providers**
7. ✅ **Apollo** fixed (`/api/v1` + `x-api-key`) · **PDL/Hunter** verified. *(Cognism dropped — gated endpoints unconfirmable.)*
8. ✅ **Provider selection** (`Workspace.settings.providers`, ordered allow‑list) + BYO‑key mgmt (pre‑existing) + usage metering (`ProviderUsage`, now wired into the agent search path).

## 3a · The remaining cutover (for when QA pauses)
- ✅ **Auth retired to LinkedIn‑only via Unipile** — done (`/auth/login → notify → callback`, Fernet session, WorkOS removed).
- **Live send**: route `send_via_channel` through the seat resolver → `UnipileChannel` (persist `external_id`), SMTP as fallback.
- **Register webhooks on connect**: call `UnipileConnection.register_webhooks` with the public receiver URL.

## 4 · Model touches
`Connection.external_id` = Unipile account_id · `Message.external_id` + `account_id` (reply mapping) · `Workspace.settings.providers` (selection) · `LoginAttempt` (sign‑in correlation) · `Organization.workos_org_id` dropped · config: `unipile_*` only (`workos_*` removed).

## 5 · Sources
Unipile: [send-messages](https://developer.unipile.com/docs/send-messages) · [send-email](https://developer.unipile.com/docs/send-email) · [retrieving-users](https://developer.unipile.com/docs/retrieving-users) · [invite-users](https://developer.unipile.com/docs/invite-users) · [linkedin-search](https://developer.unipile.com/docs/linkedin-search) · [hosted-auth](https://developer.unipile.com/docs/hosted-auth) · [webhooks](https://developer.unipile.com/docs/webhooks-2). Apollo: [people-search](https://docs.apollo.io/reference/people-api-search) · [auth](https://docs.apollo.io/reference/authentication). [PDL](https://docs.peopledatalabs.com/docs/person-search-api) · [Hunter](https://hunter.io/api-documentation).
