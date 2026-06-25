# Provider Architecture — how external providers are structured & used

> How the `app/ext/` provider layer is shaped and consumed across the system. Companion to
> `provider-integration-plan.md` (the verified API calls + the build status) and
> `agent-architecture.md` (the agents that drive it).

Everything lives in `app/ext/` and is **duck‑typed** — concrete adapters are plain classes that
*match* a `Protocol`; they don't inherit it.

## 1 · The contract — `ext/base.py`

Defines the three role protocols and the shared value types:

- **`SourceProvider`** (the Data role): `key`, `name`, `capabilities` + `search()`, `enrich()`,
  `verify_email()`, `verify_credentials()`.
- **`ChannelProvider`**: `channel` + `send()` (returns the thread/chat id), `reply()`.
- **`ConnectionProvider`**: `create_link()`, `profile()`, `register_webhooks()`.
- Value objects: **`PersonHit`** (the normalized person — name/title/company/email/email_status/
  linkedin_url/skills/score/confidence/…), **`EmailVerdict`**, **`SearchPage`** (`hits`, `total`,
  `cursor`), **`ProviderCapabilities`** (`search`/`enrich`/`verify_email` flags).
- JSON narrowers (`json_body`, `json_list`, `json_object`, `opt_str`, `opt_int`, `str_list`) — every
  adapter funnels untyped provider JSON through these to stay `Any`‑free.

## 2 · The adapters

| Adapter | Role(s) | Endpoints / auth | Notes |
|---|---|---|---|
| **`pdl.py`** `PDLProvider` | Data | `POST /v5/person/search` (an **Elasticsearch bool query** built by `_es_query` from `Targeting`) + `GET /v5/person/enrich`, `X-Api-Key` header | search + enrich, **no** verify_email; `confidence = likelihood×10`; no avatar (not theirs to redistribute) |
| **`apollo.py`** `ApolloProvider` | Data | `POST /api/v1/mixed_people/search` + `POST /api/v1/people/match`, **`x-api-key` header** | maps `Targeting` → `person_titles[]`/`person_seniorities[]`/etc.; `verify_email` piggybacks on enrich; **BYO‑only** per ToS |
| **`hunter.py`** `HunterProvider` | Data | `GET /v2/email-finder` + `GET /v2/email-verifier`, `api_key` query | **no people‑search** (`capabilities.search=False`); enrich needs name+company → finds an email; strong `verify_email` |
| **`unipile.py`** | **all three** | base `{dsn}/api/v1`, `X-API-KEY` | three classes (below) |
| **`demo.py`** `DemoProvider` | Data (all caps) | none | deterministic synthetic people seeded off *every* `Targeting` field; the registry's fallback so discovery works with zero keys |

**`unipile.py` is the spine — three separate classes:**

- **`UnipileProvider`** (Data, `key="linkedin"`): `search` → `POST /linkedin/search?account_id=`
  (keyword body, cursor pagination); `enrich` → `GET /users/{public_identifier}?account_id=`. Takes a
  per‑seat `account_id` (falls back to settings). Plus `fetch_job_postings` (best‑effort stub) and
  the shared `_public_identifier()` helper.
- **`UnipileChannel`** (Channel): `send` → resolve recipient `provider_id` → multipart `POST /chats`
  (+`linkedin[inmail]`) or `POST /emails`; `reply` → `POST /chats/{id}/messages`. *(Built and tested,
  but see the wiring caveat in §4.)*
- **`UnipileConnection`** (Connection): `create_link` (hosted‑auth wizard), `profile` (reads
  `member_urn`), `register_webhooks`. Factories `unipile_connection()` / `unipile_channel(channel)`
  are key‑gated.

## 3 · The registry — `ext/registry.py` (how a provider set gets built)

- **`PROVIDER_CATALOG`**: specs (`key`, `name`, `live`, `docs_url`) — pdl/apollo/hunter live;
  `linkedin` is platform‑Unipile, not a BYO key.
- **`_FACTORIES`**: `key → constructor` (only pdl/apollo/hunter/linkedin have one).
- **Resolution order, per provider** (`build_providers_for_org`): **BYO org credential** (a
  `ProviderCredential` row, secret `unseal`ed) → **platform key** (settings, via `_platform_keys`) →
  otherwise skipped. The **demo** provider is appended when `people_providers_demo` is on or nothing
  else resolved.
- **Selection**: `provider_selection(workspace.settings)` reads an ordered allow‑list from
  `Workspace.settings["providers"]`; `_apply_selection` filters + orders the built list (falls back
  to all if it would empty the set).
- Entry points: `build_providers()` (no org), `build_providers_for_org(session, org_id, *, selection)`,
  `build_one(key, api_key)` (single, for credential verification).

## 4 · How they're used across the system

### Data role — the discovery orchestration (`services/sourcing/discovery.py`)

The hub every Data consumer goes through:

- **`search_people(providers, targeting)`** — fans out to every `capabilities.search` provider,
  **dedupes** across them (`dedupe_key`: email → linkedin → name+company), **fit‑scores** each hit
  with the same `evaluate()` the ranking uses, sorts best‑first, briefly caches (process‑local, 120s
  — cost only, *not* a corpus).
- **`enrich_ref(...)`** — **waterfalls** enrich‑capable providers, returns the first record that
  yields an email or LinkedIn URL.
- **`verify_hits(...)`** — fills `email_status` via the first verify‑capable provider.
- **`import_hits(...)`** — the **only step that writes**: persists selected hits as workspace
  `Contact`s (`source = provider`), deduped against existing. Search/enrich are otherwise live
  pass‑through and discarded.

Consumers:

- **Sourcing agent** (`agents/sourcing.py`): `run_sourcing` / `deterministic_source` call
  `build_providers_for_org(... selection=provider_selection(workspace.settings))`; the agent's
  `search`/`enrich`/`import` tools wrap the discovery functions. The `search` tool **meters** each
  real provider via `usage.record(... kind="search")` (skips demo).
- **Manual discovery API** (`api/discovery.py`): search/enrich endpoints over the same functions,
  with `usage.record` + a usage `summary` endpoint.
- **Chat** (`services/agent/chat.py`): builds providers to preview an audience.
- **Settings API** (`api/settings.py`): BYO‑key management — `/settings/data-providers`
  list/set/delete/**verify**, iterating `PROVIDER_CATALOG` and using
  `build_one(...).verify_credentials()`; secrets are `seal`ed at rest, only `last4` + status returned.

### Connection role — auth / sign‑in (`services/workspace/auth.py`, `api/auth.py`)

`start_linkedin_login` → `UnipileConnection.create_link` (wizard URL); the Unipile notify →
`complete_linkedin_notify` reads `UnipileConnection.profile()` (`member_urn`) → `provision_from_linkedin`
(`services/workspace/connections.py`) → upserts the **`Connection` seat** (`external_id = account_id`).
`seat_account_id()` is how the rest of the system resolves a user's Unipile account.

### Inbound — webhooks (`api/messaging.py`)

`POST /webhooks/unipile` maps an inbound message by `chat_id` → `Message.external_id` → `Enrollment`
(or by sender email) → `handle_reply`; account `CREDENTIALS`/disconnect events flip the `Connection`
to `needs_reauth`.

### Channel role — sending (one important caveat)

`UnipileChannel` (per‑seat, multipart, reply‑in‑thread) **exists and is tested but is not yet wired
into the live send path**. Today `services/outreach/messaging.py` still sends via the **legacy**
`send_via_channel → send_linkedin` (single global `settings.unipile_account_id`, JSON, starts a new
chat) and `send_email` (SMTP). Routing that through the seat resolver → `UnipileChannel` is the one
remaining **live‑send cutover** item (see `provider-integration-plan.md` §3a).

## 5 · The mental model

- **Capability‑gated**: orchestrators skip providers whose `capabilities` flag is off, so the same
  `search_people` / `enrich_ref` / `verify_hits` loop works no matter which providers are active.
- **Graceful**: every adapter returns an empty `SearchPage` / `None` on any error or 4xx — a bad key
  or a down provider degrades, never throws into the flow.
- **Live pass‑through**: provider data is never stored except the `Contact`s a user explicitly imports.
- **Per‑seat vs key‑or‑demo**: Unipile/LinkedIn resolves a per‑user `account_id` from `Connection`;
  PDL/Apollo/Hunter resolve BYO‑cred → platform‑key → (else) the demo provider stands in.

## 6 · The flow of a hit (end to end)

```
provider.search(targeting)            # live call, per adapter
  → PersonHit (normalized via base.py narrowers)
  → search_people: dedupe across providers + evaluate() fit-score + rank
  → (returned to caller, otherwise discarded — nothing stored)
  → import_hits(selected)             # the ONLY write
  → Contact (source = provider) in the workspace
```
