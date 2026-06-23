# People‑data sourcing (Rail B)

How Sourcewell finds people to put into a workspace using **licensed people‑data APIs** — the
landscape, the legal boundary, the architecture decision, what's built, and how to operate it.

> **TL;DR** — There are two sourcing rails. **Rail A** is the user's own connected LinkedIn
> Recruiter/Sales seat (per‑seat, ToS‑bound, requires connecting an account). **Rail B** (this doc)
> is licensed third‑party people‑data APIs where *we* (or the customer) hold an API key, so users can
> search public B2B people data on day one with nothing connected. We do **pass‑through** to those
> providers and persist **only the people a user imports** — we never mirror a provider's database.

---

## 1. The two sourcing rails

| | Rail A — LinkedIn seat | Rail B — people‑data APIs (this doc) |
|---|---|---|
| Trigger | User connects their Recruiter / Sales Navigator seat (Unipile) | Sourcewell or customer holds a provider API key |
| Available | Only after a seat is linked | Day one, nothing connected |
| Strength | Rich profile + InMail / connection messaging | Search/enrich public B2B data at scale |
| Constraint | Per‑seat, rate‑limited, ToS‑bound | Per‑record cost, licensing terms |
| Shared | Both produce/act on the same `Contact`; `linkedin_url` lets a Rail‑B find be actioned later via Rail A |

---

## 2. The legal boundary (why this rail looks the way it does)

In 2025 LinkedIn won against the scrapers. **Proxycurl** (the dominant "LinkedIn profile API") was
sued for running fake logged‑in accounts and **shut down permanently on 2025‑07‑04**; its domain now
points to a successor that explicitly doesn't scrape LinkedIn. Courts protect scraping of *truly
public, unauthenticated* data but punish circumventing logins / impersonation.

**Consequence for Rail B:** build on **licensed compiled databases** — vendors who maintain their own
person/company graph (opt‑in contributory networks, public web, data partnerships, verification) and
who indemnify you and run GDPR/CCPA processes. Do **not** build on live‑LinkedIn‑scraper APIs.
**Clearbit** is also no longer a standalone option — HubSpot acquired it and folded it into "Breeze
Intelligence" (HubSpot‑gated).

---

## 3. Architecture decision — pass‑through, not background sync

**Decision: live pass‑through to provider APIs; persist only the people a user imports.** We do not
sync a provider's corpus into our own searchable index.

### Why not background‑sync into our own DB
1. **The provider terms forbid it (decisive).** Every vendor prohibits bulk caching / building a
   derivative or competing database; ZoomInfo is strictest (seat‑licensed, no redistribution).
   Mirroring their data is the "shadow copy" behavior that ends contracts, voids indemnity, and
   invites litigation (the Proxycurl lesson).
2. **The economics are absurd.** Pricing is per‑record (PDL ≈ $0.28/match). Hoarding billions of
   records you'll never contact would cost on the order of hundreds of millions — to hold data you
   don't use.
3. **It's stale immediately.** People change jobs/titles/emails constantly; a snapshot decays within
   weeks, so you'd re‑sync forever. A live query is always current.
4. **No single corpus is complete.** Coverage varies by provider and region; live multi‑provider
   search with dedupe beats any one mirror.
5. **Compliance favors holding less.** GDPR rewards data minimization — hold only the records a
   customer is actively working, with provenance and a deletion path.

### What pass‑through means concretely
- **Discovery = live, ephemeral.** Criteria → adapter → provider search API → normalized,
  fit‑scored, ranked → returned to the request and discarded. Never stored as a corpus.
- **Persistence = only on import.** The people a user explicitly imports become `Contact` rows
  (source‑attributed). That table *is* our internal searchable DB, but it only holds working sets.
- **Cache = transient, performance only.** Optional short‑TTL response cache keyed by query hash so
  pagination/re‑lookups don't re‑bill. Expires; not a corpus.
- **Enrich/verify = waterfall at import** for anything missing an email/phone.

> One line for the team: **we don't host a people database — we broker live searches and keep only
> what each customer imports.**

### The one legitimate "sync"
Background jobs that operate on **records we already own** are fine: e.g., a periodic re‑enrich of a
customer's *imported* pipeline (job‑change detection, email re‑verification), or first‑party
connectors (the customer's CRM/ATS). The line: refreshing *your imported working set* = OK;
pre‑ingesting *the provider's whole catalog to search it* = not OK.

---

## 4. The provider landscape

| Provider | Role | Coverage | Email / phone | Access & price | Best fit for us |
|---|---|---|---|---|---|
| **People Data Labs** | Search + enrich (raw) | ~3B profiles¹, 70M+ cos | Work email good; mobile thin | **API‑only, no UI**; Free 100/mo · Pro $98/mo · **~$0.28/match** (lower at volume) | **Platform engine** — build on it |
| **Apollo.io** | Search + enrich + verify | 275M+ contacts, 30M+ cos | Broad but **~65% real acc.²** (US 80–88%), bounce 15–25% → verify | Self‑serve API, credits; $49–119/seat/mo | Cheap breadth / SMB; also a competitor app |
| **ZoomInfo** | Search + enrich + intent | ~235M+ contacts, 104M cos, **70M+ direct dials** | Best **direct‑dial** + verified email | **Sales‑gated, seat‑licensed, redistribution‑restricted**; ~$15k–40k+/yr | Enterprise **BYO key** (not platform key) |
| **Cognism / Lusha** | Search + enrich | Strong **EMEA** | **Mobile‑verified**, GDPR‑first | API; mid price | **EU phone / GDPR** coverage |
| **Hunter / AnyMailFinder** | Email find + verify | Domain‑based | Email only, ~95–97% find | Simple API; cheap per‑lookup | Cheap email backfill |
| **Clay / FullEnrich / BetterContact** | **Waterfall** orchestrators | Cascade 15–80+ sources | Highest fill (~98% email) | API; per‑credit | Max‑coverage enrichment fill |
| **NeverBounce / ZeroBounce** | Verification only | — | Validates deliverability | API; per‑check | Pre‑send hygiene (protect domain) |

¹ PDL's "3B+" counts historical/duplicate/thin records; the *actionable verified* set is much smaller (~70M+ companies is the firm number).
² Apollo *markets* ~97% email, but third‑party 2026 tests show **~65% overall** (higher in the US) with 15–25% bounce — which is why every provider's output is **verified before send**.

**Excluded on purpose:** LinkedIn‑scraper APIs (Proxycurl, shut down) and Clearbit (now HubSpot Breeze, gated).

### Recommended stack (phased)
1. **People Data Labs** — primary engine (platform‑metered credits). One API does search + enrich, self‑serve, storage‑friendly.
2. **A verifier** (NeverBounce / ZeroBounce) — deliverability before any send.
3. **BYO key for ZoomInfo / Apollo** — enterprise customers paste their own (don't resell ZoomInfo under a platform key).
4. **Cognism / Lusha** — EU mobile / GDPR, later.
5. **A waterfall** (FullEnrich / BetterContact) — fill‑rate booster, later.

---

## 5. The adapter module (what's built)

Everything lives in `backend/app/modules/sourcing/`. Adding a provider = one adapter file; nothing
else changes (the same swap‑behind‑one‑interface pattern as the client's `appFetch`/`DEMO_MODE`).

```
sourcing/
  adapters/
    base.py       # contract: PeopleQuery, PersonHit, EmailVerdict, SearchPage,
                  #           ProviderCapabilities, SourceProvider (Protocol)
    demo.py       # DemoProvider — synthetic, zero-key fallback (deterministic)
    pdl.py        # PDLProvider — live POST /person/search + GET /person/enrich, normalized
    registry.py   # PROVIDER_CATALOG, build_providers(), build_providers_for_org()
  people.py       # dedupe_key, search_people, enrich_ref, import_hits
  router.py       # /people/search, /people/import, /people/enrich, /people/providers
  agents.py       # evaluate(Candidate, criteria) + FIT_THRESHOLD=40
```

### Normalization — one shape for every provider
Each adapter maps its payload to **`PersonHit`**, whose fields mirror the `Contact` table (plus
provenance + scoring): `provider, external_id, full_name, title, company, location, email,
email_status, linkedin_url, avatar_url, skills, company_size, industry, phone, confidence, score,
rationale, raw`. (`raw` and the API key are never returned to the client.) On import each hit becomes
a `Contact` with `source = "pdl" | "apollo" | …`.

### Same fit model as ranking
`evaluate()` scores any `Candidate` (a small Protocol = `skills/title/location/email`), so a search
hit is scored with the **exact** model the audience/rank pipeline uses (skills 50 / title 30 /
location 20, normalized over specified categories; +10 for a reachable email; `FIT_THRESHOLD = 40`).
No second scoring path.

### Flow
```
criteria ──▶ build_providers_for_org(session, org) ──▶ [PDLProvider?, …, DemoProvider]
         search (live, fan-out) ─▶ dedupe (email → linkedin → name+company) ─▶ evaluate() score ─▶ rank
         user selects ─▶ import_hits() ─▶ Contact rows (source=provider) ─▶ audience → rank → enroll
```
`search`/`enrich` never touch the DB; **`import_hits` is the only writer.**

---

## 6. BYO credentials + Settings UI

Customers can bring their own provider keys (required for ZoomInfo/Apollo; optional everywhere).

- **UI:** Settings → **Data providers** tab (`frontend/src/pages/settings-page.tsx`). Lists the
  catalog (PDL = *Connect*; Apollo/Cognism/Hunter = *Coming soon*), a write‑only key dialog with a
  docs link, status badges, and Remove. Keys are masked to `····last4`.
- **Model:** `ProviderCredential` (org‑scoped, unique per provider) in `tenancy/models.py`;
  migration `f1a2b3c4d5e6`.
- **At rest:** `app/platform/crypto.py` `seal/unseal` — **Fernet** when `session_cookie_password` is
  set (`enc:…`), plaintext‑marker fallback for local dev (`plain:…`). The API returns only
  `configured/enabled/last4/status`, never the key.
- **Resolution order** (`build_providers_for_org`): **BYO org key → platform env key → demo
  fallback**. Saving a key brings a provider online with no redeploy.
- **Access:** create/update/delete are **org‑admin only**.

---

## 7. Compliance & data handling

- **Lawful basis:** legitimate interest for B2B outreach; be region‑aware (EU). Keep a workspace
  **suppression / do‑not‑contact** list; honor unsubscribe.
- **Provenance:** every imported contact stores `source` (+ timestamp). Support deletion.
- **Storage/redistribution terms vary:** PDL/Apollo permissive within limits; **ZoomInfo strict** —
  don't store/redistribute under a platform key, use BYO.
- **Minimization:** we only persist imported working sets, not provider corpora (see §3).
- **Deliverability:** verify emails before send to protect domain reputation (Apollo bounce 15–25%).

---

## 8. API reference

People discovery (`/people`, workspace‑scoped):

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/people/search` | Live multi‑provider search → `{ results: PersonHit[], providers: string[] }` |
| `POST` | `/people/import` | Persist selected hits → `{ imported, contact_ids }` (deduped) |
| `POST` | `/people/enrich` | Waterfall enrich one ref → `{ hit }` |
| `GET`  | `/people/providers` | Providers that would run for this org (capabilities) |

Credentials (`/settings/data-providers`, **org‑admin**):

| Method | Path | Purpose |
|---|---|---|
| `GET`    | `/settings/data-providers` | Catalog + per‑provider status (configured/enabled/last4) |
| `PUT`    | `/settings/data-providers/{provider}` | Set/update a BYO key (sealed; returns masked) |
| `DELETE` | `/settings/data-providers/{provider}` | Remove a key |

---

## 9. Configuration

| Setting | Default | Effect |
|---|---|---|
| `PDL_API_KEY` | `""` | Platform‑key mode for PDL. Set to use the real provider org‑wide. |
| `people_providers_demo` | `true` | Keep the synthetic demo provider as a fallback. |
| `session_cookie_password` | `""` | When set, provider keys are Fernet‑encrypted at rest (`enc:`); else `plain:`. |

**Go live with a real provider:** set `PDL_API_KEY` (platform‑wide) **or** save a key in Settings →
Data providers (per org). Same endpoints, same UI; the demo provider stays as fallback.

---

## 10. Status & roadmap

**Built:** adapter contract, PDL + demo adapters, registry (catalog + org‑aware resolution),
search/enrich/import orchestration, `/people/*` endpoints, BYO credentials + Settings UI + at‑rest
sealing. Verified end‑to‑end; backend gate green.

**Next:**
- "Find people" search UI (search → preview with scores → select → import).
- **Verify‑key** action (call provider, flip status ok/invalid) + enable/disable toggle.
- Verify‑on‑import waterfall (enrich + email verify before persisting).
- More adapters: **Apollo**, Cognism, Hunter; a waterfall orchestrator.
- Transient response cache + per‑org **credits/usage** metering.
- "Refresh owned contacts" job (the legitimate §3 sync) and a suppression list.
- Demo‑mock handlers for `/people/*` and `/settings/data-providers` (standalone `VITE_DEMO=1` build).

---

## Sources

- Proxycurl shutdown — [startuphub.ai](https://www.startuphub.ai/ai-news/startup-news/2025/the-1-linkedin-scraping-startup-proxycurl-shuts-down) · [Social Media Today](https://www.socialmediatoday.com/news/linkedin-wins-legal-case-data-scrapers-proxycurl/756101/)
- PDL coverage/pricing — [SyncGTM review](https://syncgtm.com/blog/people-data-labs-review)
- ZoomInfo size — [Cleanlist 2026](https://www.cleanlist.ai/blog/2026-03-05-zoominfo-database-size-2026) · [ZoomInfo IR (235M)](https://www.zoominfo.com/newsroom/press-releases/zoominfo-grows-global-business-contacts-235-million)
- Apollo real accuracy — [Prospeo](https://prospeo.io/s/apollo-io-accuracy)
- Provider comparison — [Starnus: ZoomInfo vs Apollo vs PDL](https://starnus.com/blog/best-b2b-data-providers-zoominfo-apollo-pdl)
- Waterfall enrichment — [Persana](https://persana.ai/blogs/waterfall-enrichment-tools)
