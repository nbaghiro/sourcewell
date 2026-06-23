# Sourcewell — Product Specification

*The canonical description of how the product is intended to work. Not a plan — the spec.*

---

## 1. What Sourcewell is

Sourcewell is an AI-agent platform that automates **outbound cold-reach**. A user defines what they're
looking for; software agents continuously **find** matching people, **rank** them by fit, **write** a
personalized message to each, and **follow up** over days — with a human approving every message before
it sends, and replies landing in a unified inbox for the human to take over.

It is a **generic outbound-funnel engine over "contacts."** **Recruiting** is the first vertical
(find candidates for a role). The same engine serves **sales** (find leads for an offer) and
**marketing** (nurture contacts) by changing labels, criteria, and data sources — not the engine.

**Why it exists:** outbound sourcing is slow manual work — search, research each profile, write a
tailored note, send, remember to follow up, track replies, hundreds of times. Sourcewell runs that loop
in the background so people review the work instead of doing it, and it ties the **target (a role)**
directly to a **continuously refreshed, ranked, contacted pipeline** — one supervised system, not a
patchwork of tools.

---

## 2. Who it's for

Enterprise talent teams at large, regulated employers (banks and similar), plus staffing/sales agencies.
Economic buyer = TA/revenue leadership; **gatekeepers = security, compliance, legal** — so control and
compliance are first-class, not afterthoughts.

---

## 3. Core concepts (glossary)

| Concept | Meaning | Recruiting label | Sales label |
|---|---|---|---|
| **organization** | The customer / billing / SSO tenant | the company / agency | same |
| **workspace** | An isolated sub-unit within the org | a client or a department | a client / book of business |
| **user** + **role** | A team member (`org_admin` / `workspace_admin` / `member` / `compliance`) | recruiter, TA lead | SDR, sales lead |
| **connection** | A user's connected sending account (email / LinkedIn seat) | — | — |
| **contact** | The target person (reusable, deduped profile) | candidate | lead / prospect |
| **company** | The contact's employer (data) | employer | account |
| **campaign** | The active outreach effort + its criteria, sequence, and config | a "search" for a role | a sequence |
| **score** | A contact's fit for a campaign, with evidence | fit-to-role | ICP fit |
| **enrollment** | A contact being worked in a campaign, with state | the candidate in pursuit | prospect in sequence |
| **message / conversation** | A send or reply / the thread | InMail/email | email/LinkedIn |
| **suppression** | Never-contact rules (opt-outs, employees, DNC) | — | — |

In recruiting, a campaign carries the **role's criteria + scoring rubric** (parsed from a job
description). There is no separate "job" object in v1 — the campaign *is* the role's outreach effort.

---

## 4. Multi-tenancy (organization → workspace)

A customer is an **organization**; inside it are **workspaces** — the unit of **data isolation**.
The same structure cleanly covers three patterns:

- **Recruiting agency:** organization = the agency; workspace per **client company**. A candidate sourced
  for Client A never appears for Client B (clients are often competitors).
- **Sales agency:** organization = the agency; workspace per client; "leads/accounts" instead of candidates.
- **Enterprise, multi-department:** organization = the company; workspace per **department**. Departments
  are walled from each other; an **org-wide blocklist** (e.g., "don't contact current employees") is shared.

Isolation is enforced at the data layer (a user only sees rows in workspaces they're a member of;
`org_admin` sees all). Cross-workspace **sharing is the deliberate exception** (org-scoped suppression),
never the default. A user's sending **seat is shared across their workspaces**, so its daily limit is split
fairly across the clients they work.

---

## 5. Channels & the LinkedIn reality

- **Email is the compliant, scalable backbone.** Outreach sends from the **recruiter's own mailbox**
  (Gmail / Microsoft Graph) for deliverability and authenticity.
- **LinkedIn is an optional, per-recruiter accelerator.** There is **no official LinkedIn API** to search
  members or send InMail; the only way is through a recruiter's **own** authenticated session (via Unipile).
  So each recruiter connects **their own** Recruiter/Sales-Navigator/Premium seat, and Sourcewell operates
  it within strict, human-like limits (~100/day/seat), pausing automatically on any LinkedIn warning.
  This is ToS-gray and bears account risk — so it is opt-in, capped, and human-gated.
- **No platform-owned LinkedIn account.** One shared account serving many customers hits daily caps, breaks
  message authenticity, and is the pattern LinkedIn litigates against — explicitly rejected.
- **Email-only mode** exists for customers whose security forbids LinkedIn automation; everything else works.
- **Sourcing** comes from **licensed data providers** (e.g., People Data Labs / Coresignal) at the platform
  level — no per-customer account needed — plus optional LinkedIn search through a connected seat.

---

## 6. End-to-end product flows

**6.1 Onboarding.** Admin signs in (SSO). Each recruiter connects their **work mailbox** and, optionally,
their **LinkedIn** account. The team uploads approved **templates** and a **brand-voice** note (the bounds
the message agent writes within) and sets ground rules: approval-required by default, do-not-contact lists,
sending caps, quiet hours.

**6.2 Requirement intake.** Upload or paste a **job description** (or pull a req from an ATS). Sourcewell
parses it into structured **criteria** + a scoring **rubric**; the recruiter reviews and edits. This is what
makes ranking accurate, so it's explicit.

**6.3 Campaign setup.** A short wizard: confirm **targeting** (criteria + how big a pipeline to keep),
choose **sources** (data providers; LinkedIn if a seat is connected) and routing (bulk vs targeted), build
the **sequence** (ordered touches: channel, delay, template), pick **channels + sending seat**, and set
**autonomy** (approve-each default) + guardrails (caps, quiet hours, score threshold). The recruiter sets
**policy**, not prompts — changing policy changes agent behavior immediately.

**6.4 Sourcing.** The Sourcer agent turns the criteria into queries and runs them across the configured
sources several ways (exact title, adjacent titles, skills, competitor companies). Results are normalized
to one canonical contact and **deduped/merged** across sources; do-not-contacts and already-in-pipeline are
dropped. The system keeps refilling whenever the qualified pipeline runs low — continuously.

**6.5 Ranking & proposed leads.** Every contact is scored against the rubric with a plain-language reason
citing their background. The recruiter sees a **ranked shortlist**, each with a fit score and a short "why."

**6.6 Approvals (two light gates).** First, approve **who to pursue** (individually or in bulk above a
score). Then, for each touch, approve **what gets sent** — approve / edit / reject / snooze, in a batch.
Nothing sends on its own; unapproved drafts hold.

**6.7 Outreach & follow-ups.** Approved messages send through the recruiter's own email or LinkedIn within
safe, human-like limits, and follow up on schedule over several days until the person replies or opts out.
If LinkedIn flags an account, Sourcewell pauses it and asks the recruiter to reconnect.

**6.8 Unified inbox.** Replies from any channel land in **one thread per person**. An agent classifies each
(interested / not now / question / opt-out…), pauses automation and drafts a warm reply when someone's
interested, and honors opt-outs immediately.

**6.9 Handoff & ATS sync.** Once a candidate engages, the recruiter takes over; status and notes sync to the
ATS (the system of record). Sourcewell fills the funnel; it doesn't replace the ATS.

**6.10 Analytics.** Per-campaign and per-seat funnels (sourced → contacted → replied → positive → handed-off),
which messages/sequences convert, and seat health.

---

## 7. Autonomy & control

Default is **approve-each-message** — a human approves every send. Teams can graduate to *approve-the-plan-
then-auto* or *full-auto* per campaign/segment once comfortable. A **kill switch** pauses any campaign or the
whole tenant instantly.

---

## 8. Compliance & trust (the enterprise moat)

- **Audit trail** of every decision, draft, send, and reply — who/what/when/why; exportable.
- **Fair hiring (EEOC/OFCCP):** candidates judged only on job-related factors; protected characteristics
  never used; selection reasoning retained.
- **Privacy (GDPR/CCPA):** opt-out/deletion honored, data-residency option, regional rules.
- **Outbound law (CAN-SPAM/CASL):** sender identification + working unsubscribe on email.
- **Account safety:** LinkedIn sending stays within safe limits and auto-pauses on trouble; email-only fallback.
- **Security:** SSO/SCIM, per-tenant isolation, encrypted credentials, SOC 2 target.
- Communications **archiving** is not required for recruiting at target buyers — deferred / on-demand.

---

## 9. The campaign (config → behavior)

A campaign is the **control surface**: the user configures targeting, sources, sequence, channels, autonomy,
and guardrails; each setting drives a specific part of the system (criteria → sourcing + ranking; pipeline
target → when to source more; steps → the cadence; templates + voice → message writing; caps + quiet hours →
send pacing). A campaign moves through **draft → active → paused → completed**; pausing is a kill switch that
holds in-flight work; edits apply to not-yet-sent steps.

---

## 10. Non-goals (v1 / alpha)

LinkedIn outreach (fast-follow after email), multi-provider sourcing, rich analytics, deep ATS integration,
the sales/marketing verticals, and a browser extension are **out of scope for the first release**. v1 is
**email-first, approve-each-message, single-workspace-per-partner**, with sourcing via manual import or one
data provider — the smallest thing that does the real job safely.
