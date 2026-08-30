# Tenancy

Two data levels, `Organization` → `Workspace`. Identity, seats, resale and policy are not levels of
that hierarchy; they are orthogonal dimensions that attach to it. Nested workspaces are out of scope.

## Where a resource lives

| Concern | Where it lives |
|---|---|
| Identity | `User`, global. Nothing owns it. |
| Tenant | `Organization`: plan/billing, DNC list, provider keys, audit, memory, provider usage |
| Isolation boundary | `Workspace`: contacts, campaigns, enrollments, messages, agent runs |
| Access | `Membership` (user × org, role) + `SpaceGrant` (user × workspace, role) |
| Sending seat | `Connection`, owned by a `User`, selected per campaign |
| Resale | `Partner`, a nullable dimension on `Organization`. Not a parent. |
| Settings | Policy chain: platform → partner → org → workspace → campaign |
| Vertical | Config pointer (`vertical` policy key) + label pack |

## Identity and access

A `User` is global: a unique email, an optional `sso_subject`, an optional password hash. No column
ties a user to an organization, so the same person can belong to several.

`Membership` is the only user↔org link, unique on (user, organization), with a role of `org_admin`,
`member` or `compliance`. `SpaceGrant` is the only per-workspace grant, unique on (user, workspace),
with a role of `admin` or `member`. Org admins and compliance reach every workspace in their
organization implicitly, so we never write grant rows for them; a plain member reaches exactly the
workspaces they are granted.

`api/context.py::get_context` builds the request's `TenantContext`:

1. `allowed_workspace_ids` is every workspace in an org where the user is `org_admin` or
   `compliance`, unioned with the workspaces named by their `SpaceGrant` rows. An `X-Workspace-Id`
   outside that set is a 403.
2. The organization is resolved from a valid `X-Workspace-Id` if one is present, else from the user's
   sole `Membership` if they have exactly one, else from `X-Organization-Id`. A user in several orgs
   who sends neither header gets a 400 naming the header it needs.
3. `roles` and `is_org_admin` describe the user in the resolved organization only.

The frontend mirrors both headers outside React (`lib/api/tenant.ts`), seeded from localStorage so
the first request of a session already names the tenant the user last worked in. A selection the
server no longer accepts is dropped and `/auth/me` is retried clean.

## Seats

A `Connection` is one person's connected mailbox or LinkedIn account, owned by a `User` and scoped to
an organization for queries. Campaigns carry `seat_id` (the designated sender) and
`created_by_user_id`.

`resolve_channel_seat(campaign, channel)` returns the campaign's seat when it is healthy and matches
the channel, else the creator's healthy seat for that channel, else nothing. It never returns an
unhealthy seat and never falls back to an unrelated colleague's account; a campaign with no resolvable
seat fails its send visibly rather than sending from the wrong mailbox. Per-seat daily caps count sent
messages by `Message.account_id` across every workspace, because the budget belongs to the mailbox.

## Partners

A `Partner` is a reseller or white-label operator: a name, a slug, `settings`, `theme`, and a status.
`Organization.partner_id` is a nullable foreign key with `ON DELETE SET NULL`, so removing a partner
never cascades into tenant data. A partner owns no contacts, campaigns or messages; its only runtime
effect today is contributing a settings layer to the policy chain.

## The policy chain

`app/core/policy.py` resolves settings across five levels, nearest wins:

    platform defaults → partner.settings → organization.settings → workspace.settings → campaign.constraints

`policy.for_workspace(...)` and `policy.for_campaign(...)` load the chain; `get_int`, `get_bool`,
`get_str` and `get_str_list` read a key with coercion and a platform-default fallback, and
`effective()` flattens the whole chain for the settings UI. Keys currently on the chain:

`daily_cap_email`, `daily_cap_linkedin`, `sending_window_enabled`, `send_window_start`,
`send_window_end`, `send_weekdays_only`, `warmup_enabled`, `brand_voice`, `vertical`, `providers`,
`autonomy_default`.

Nothing reads a single level's JSONB directly. The send governor, the touchpoint drafter's brand
voice, the sourcing provider allow-list, the agents' vertical prompt overlay, and the autonomy level a
new campaign starts at all go through the resolver.

## Suppression

`Suppression` carries a nullable `workspace_id`: NULL means org-wide, which is the default and what
reply opt-outs, unsubscribes and hard bounces write. An explicitly workspace-scoped entry sets it.
Two partial unique indexes enforce both shapes, because a plain unique constraint over a nullable
column would let duplicate org-wide rows through.

`is_suppressed(session, organization_id, email, workspace_id)` matches an org-wide row or a row for
that workspace, and every caller passes the workspace it is acting in. Removing an address clears it
everywhere in the organization.

## Verticals and labels

`app/agents/prompts.py` holds the hardcoded vertical packs. Each carries per-agent prompt overlays and
a `Labels` pack: `contact`, `contact_plural`, `campaign`, `campaign_plural`, `workspace`, `goal`.
Recruiting reads candidate / candidates / role / roles / client / role; sales reads lead / leads /
sequence / sequences / client / offer.

The workspace noun comes from `Workspace.kind` first (`client` → "Client", `department` →
"Department", `team` → "Team") and falls back to the vertical pack. The resolved pack is on the
`/auth/me` response and on workspace settings; the frontend reads it through `useLabels()`.

## Known gap

A user who belongs to more than one organization and arrives with no stored tenant selection (a fresh
browser, or cleared site data) has no way to name an organization, so `/auth/me` answers 400. Closing
it needs either a bootstrap endpoint that lists memberships without a resolved org, or a relaxation of
rule 2 in `get_context` to fall back to the oldest membership.
