# Sourcewell Agent System — Architecture & Implementation Plan

> Canonical blueprint for the agent‑native outreach system. Agent‑first, UI last.

## 0 · Vision & principles
- **Generic agentic outreach platform**; recruiting is one **vertical** (a prompt/knowledge pack, hardcoded in code for now). Industry‑agnostic core — never recruiting in the schema.
- Product flow: **describe a goal / upload a JD → the agent designs & runs the campaign → the user supervises**, with a manual leg that coexists via **per‑field provenance**.
- **Deterministic pipeline is the floor**; agents are the enhanced path, never a hard dependency (fallback everywhere).
- **State‑driven choreography**, not a god‑orchestrator — agents coordinate through shared `Campaign`/`Enrollment`/thread state; the autonomy dial flips the gates.
- Builds on existing primitives: `ext/` providers, `discovery`/`targeting`/`messaging`/`suppression`, `worker.py`, `agent/` feed + chat.
- Every phase ships **green** (ruff + mypy strict/no‑`Any` + pytest), committed (single‑line message).

## 1 · The three agents (by altitude) + the calls
| | **Main agent** | **Sourcing agent** | **Outreach agent** |
|---|---|---|---|
| **Altitude** | campaign / strategy | execution / pool | message / thread |
| **Owns / writes** | the Campaign strategy | Enrollments (candidates) | Messages, threads, enrollment state |
| **Triggers** | ① cold‑start design (from JD) ② scheduled review (daily) ③ user chat | `source_due` scheduler (continuous) | send engine (cold draft) · inline action (rewrite) · reply webhook (conversation) |
| **Type** | reasoning agent (3 modes) | reasoning agent (multi‑step) | reasoning agent (conversation) + single‑call ops |
| **Tools** | `estimate_audience` · `read_funnel` · `update_criteria` · `revise_sequence` · `set_cadence` · `pause` · `flag` · `suggest` | `search` · `enrich` · `score` · `import` · `list_existing` · `check_suppressed` | `get_thread` · `send_reply`/`draft_reply` · `answer_from_context` · `propose_next_step`/`schedule` · `hand_off` · `suppress` · `set_relationship_status` |

- **Main** = Designer folded in (cold‑start = t=0 run) + supervisor (ongoing) + **chat** (3rd trigger). The user‑facing **face**; steers the others **indirectly via Campaign state**.
- **Outreach** owns: cold draft (call), one‑off rewrite (call), live conversation (agentic loop, HITL). Reasoning follows signal — the loop wakes on a **reply**.
- **Calls (not agents):** `parse_brief` (JD → objective + Targeting + facts), and the cold‑sequence drafting op of Outreach.

The user perceives **one assistant**: chat → Main, rewrite a message → Outreach, "found 12 overnight" → Sourcing.

## 2 · Agent runtime (shared infra — `core/agent.py`)
- **`anthropic` SDK** for the tool‑use loop (keep `complete_json` for single‑shot calls).
- **Tool abstraction:** `{ name, json_schema, execute_fn }` + dispatch registry.
- **Bounded loop:** ≤12 steps · ~50k tokens · 60s timeout · allow‑list · validated inputs. Per‑campaign: ~500k tokens/day; on hit → pause agent work for that campaign, fall back to deterministic, surface in the feed.
- **Prompt composition:** `BASE[role] + VERTICALS[workspace.vertical].prompts[role] + render(campaign, constraints) + recalled Memory`.
- **Tracing:** persist `AgentRun`/`AgentStep` per episode → feed + budgets + debugging.
- **Fallback:** disabled / over‑budget / error → deterministic path.
- **`FakeLLM` harness:** scripts tool‑call sequences + completions → deterministic agent tests (no live API in CI).

## 3 · Tools — thin wrappers over existing, tested primitives
`search/enrich/score/import/list_existing/check_suppressed/estimate_audience` → `discovery`/`ext`/`targeting`/`suppression`/`ranking`. `draft/rewrite/send_reply/hand_off/suppress` → `messaging`. Strategy tools → `Campaign` edits. `recall/remember` → `Memory`.

## 4 · Data model (extend `Campaign`; one additive migration)
- **`Campaign`** + `objective` · `autonomy_level (manual|assisted|full)` · `constraints (json)` · `authored_by (human|agent)` · `field_owners (json)` · `next_source_at` · `brief_source {origin, raw_text, ref}`.
- **`Workspace`** + `vertical: str = "recruiting"` (pointer; prompt packs hardcoded in `app/agents/verticals.py`). No `Vertical` table.
- **`Memory`** — `scope (workspace|vertical|campaign|contact)` · `scope_id` · `content` · `embedding (nullable, pgvector — keyed recall now, vector later)` · `metadata` · `created_by_run`.
- **`AgentRun`/`AgentStep`** — episode + step traces.
- **`Enrollment`** + `next_action` · `signals` · `relationship_status (active|parked|nurture|handed_off|declined)` · `park_until`.
- **`Contact`** + `attributes (json)`.
- **Suggestions** → reuse approvals/notifications (no table): a notification of type `suggestion` carrying `{field, proposed_value, rationale}`.
- **Migration backfill:** existing campaigns → `authored_by=human`, `field_owners={all:human}`, `autonomy_level` from old mode; workspaces → `vertical=recruiting`.

## 5 · Autonomy, provenance & human‑in‑the‑loop
- **`autonomy_level`** flips the **3 gates** (campaign‑activate · candidate‑approve · message‑send) between human queue and auto.
- **Provenance:** human edit → field pins (`owner=human`); agents write only agent‑owned; suggestion (notification) for proposed changes to human fields; "let AI manage" unpins.
- **Outreach HITL (3 layers):** ① per‑response gate (autonomy) ② user‑defined handoff rules ③ always‑handoff safety triggers (negotiation / out‑of‑scope / escalation / uncertainty). Handoffs deliver a warm, summarized thread.

## 6 · Brief intake
Create‑flow step 0, three sources → `objective`: paste/upload JD · pull from LinkedIn (`ext/unipile.fetch_job_postings` — **stubbed** now, real later) · describe (free‑text). **`parse_brief(text, vertical)` → {objective, Targeting, facts}** (single structured call) pre‑fills the Audience section; Main designs the rest.

## 7 · Orchestration & schedulers (extend `worker.py`)
`source_due` (Sourcing) · `main_review_due` (Main, daily) · existing `run_due` (send) · reply **webhook** (Outreach). Self‑clocking via `next_*_at` + `SKIP LOCKED`. No central orchestrator.

## 8 · UI — campaign cockpit (frontend, last)
- Tabbed page + live status header (funnel rollup) on all tabs.
- **Structure tab** (default): per‑section cards (Goal · Audience · Sequence & Channels · Messaging · Autonomy & Limits), each **🔒 manual or ✨ AI** with set/generate/regenerate/take‑over/let‑AI‑manage + inline suggestions.
- **Agent Activity tab:** narrated `AgentRun` feed, grouped/filterable, revert on autonomous changes.
- **Candidates tab:** per‑candidate journeys + timelines.
- **Campaign chat panel → Main agent**; **inline message actions → Outreach agent**.
- List view: living cards. Autonomy‑adaptive (queues in assisted/manual). Built on Wellspring/shadcn.

## 9 · Build phases (each green + committed; deterministic fallback intact)
1. **Data model + migration** (+ unit/integration tests).
2. **Agent runtime** (tool‑use loop + guardrails + tracing + `FakeLLM` harness) on `anthropic` SDK.
3. **Sourcing agent + tools** — tested vs demo provider + scripted sequences.
4. **`source_due` scheduler** + funnel/activity APIs.
5. **Main agent** — cold‑start design + `parse_brief` + intake (Unipile stub) + provenance + scheduled review + strategy/suggestion(notification) APIs.
6. **Outreach agent** — cold draft + rewrite + conversation loop + HITL + reply webhook.
7. **Main‑agent chat** (3rd trigger) wired to `agent/chat`.
8. **Cockpit UI** — tabs, sections, chat, activity, candidates, intake.
9. **Full‑autonomy E2E** + per‑campaign budgets + observability + the E2E test suite.

## 10 · Test strategy (unit / integration / e2e)
Linchpin: the **`FakeLLM` harness** (scripts tool‑call sequences). With `respx` (provider HTTP) + the demo provider + `ASGITransport` (API) + a test DB:
- **Unit:** tools, runtime guardrails (step/token/timeout/allow‑list/validation), provenance transitions, autonomy gate resolution, prompt composition, keyed Memory recall, scheduler due‑selection.
- **Integration:** scripted agent episodes → side‑effects (Sourcing→enrollments, Main design/review, Outreach conversation); API endpoints w/ tenancy scoping; scheduler ticks; trace persistence.
- **E2E:** full lifecycle (intake→design→source→outreach→handoff→review) under each autonomy level; provenance (pin → review leaves it + suggests); multi‑tenancy isolation; deterministic fallback when LLM disabled.
CI runs all three; no live API in CI.

## 11 · Resolved decisions
anthropic SDK · keyed memory (embedding‑ready, nullable column) · suggestions via notifications · recruiting vertical hardcoded in code · `Workspace.vertical` string pointer · stubbed Unipile pull · one migration · budgets 50k/episode + 500k/campaign‑day.
