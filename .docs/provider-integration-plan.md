# Provider & Integration Plan — Unipile‑first, multi‑provider‑ready

> Verified external API calls + the finalized build plan. Companion to `agent-architecture.md`
> (the agents call everything through the `data` / `channel` / `inbound` seams below).

## 0 · Principles
- **Unipile is the spine**: sign‑in, LinkedIn search (sourcing), email + LinkedIn send, inbound webhooks — one integration for the MVP.
- **Auth = Unipile hosted‑auth** (connect LinkedIn at login → identity via `member_urn` + the seat `account_id`). Retire WorkOS. Returning login = session cookie, `type:"reconnect"` on expiry. No separate OIDC.
- **Three provider *roles***: `DataProvider` (search/enrich), `ChannelProvider` (send/reply), `ConnectionProvider` (seats/webhooks). Unipile fills all three; PDL/Apollo/Hunter/Cognism = Data only; SMTP = Channel(email) floor.
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
| **Apollo** | `POST https://api.apollo.io/api/v1/mixed_people/search` · `POST /api/v1/people/match`, header **`x-api-key`** | ⚠️ fix base `/api/v1`, header auth (not body `api_key`), BYO‑only |
| **Cognism** | base `developers.cognism.com`, **`Authorization: Bearer`**, **Search→Redeem / Enrich→Redeem** | ⚠️ exact paths gated behind dev portal + Entitlements (TODO on access) |

## 2 · Corrections to fold in (bugs in current adapters)
1. **Apollo**: base `/api/v1`, `x-api-key` header, BYO‑only. (Current adapter 401s/404s today.)
2. **Unipile search**: `account_id` → query string; structured filters need `/linkedin/search/parameters` ID resolution.
3. **Unipile send**: multipart (not JSON) + `linkedin[inmail]`; add `POST /emails`; persist `chat_id` for reply‑in‑thread.
4. **Unipile enrich**: pass `public_identifier`, not the full URL.

## 3 · Build phases
**Track A — Unipile MVP spine**
1. **Seams + seat resolver** — role protocols in `ext/base.py`; `services/workspace/connections.py` resolves `account_id` from `Connection`; `UnipileProvider` takes `account_id`. *(unblocker)*
2. **Auth = Unipile connect** — `hosted/accounts/link` → notify receiver (provision User via `member_urn` + Connection) → session; retire WorkOS.
3. **Connection lifecycle** — account webhook (`CREDENTIALS`→needs‑reauth); health.
4. **Channel send** — multipart LinkedIn `POST /chats` + reply `/chats/{id}/messages` (+InMail/invite); email `POST /emails`; persist `chat_id`/thread on `Message`; SMTP fallback.
5. **Inbound webhooks** — register `messaging`+`email`; public signed receiver → map `account_id`+`chat_id`→Enrollment → `handle_reply`.
6. **Sourcing finalize** — `linkedin/search?account_id=` (keyword now; `/parameters`+Sales‑Nav next); enrich by `public_identifier`; `fetch_job_postings` via `category:jobs` (best‑effort).

**Track B — switch‑ready data providers**
7. Fix **Apollo** · keep **PDL/Hunter** · add **Cognism** (Bearer + Search→Redeem; paths on portal).
8. Provider selection + BYO key mgmt + cost controls (`ProviderUsage`).

## 4 · Model touches
`Connection.external_id` = Unipile account_id (used at last) · `Message.external_id` + `account_id` (reply mapping) · `Workspace.settings.providers` (selection) · config: `unipile_*` only (retire `workos_*`).

## 5 · Sources
Unipile: [send-messages](https://developer.unipile.com/docs/send-messages) · [send-email](https://developer.unipile.com/docs/send-email) · [retrieving-users](https://developer.unipile.com/docs/retrieving-users) · [invite-users](https://developer.unipile.com/docs/invite-users) · [linkedin-search](https://developer.unipile.com/docs/linkedin-search) · [hosted-auth](https://developer.unipile.com/docs/hosted-auth) · [webhooks](https://developer.unipile.com/docs/webhooks-2). Apollo: [people-search](https://docs.apollo.io/reference/people-api-search) · [auth](https://docs.apollo.io/reference/authentication). [PDL](https://docs.peopledatalabs.com/docs/person-search-api) · [Hunter](https://hunter.io/api-documentation) · [Cognism](https://developers.cognism.com/).
