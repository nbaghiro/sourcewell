/**
 * Routes a parsed request against the in-memory demo store and returns the SAME JSON shapes the
 * real FastAPI backend returns (the generated response models). Reads derive from entities; writes
 * mutate the store and return the derived entity — so the demo is fully interactive.
 */
import { evaluateFit, toTargeting, type Targeting } from "../../targeting";
import type { components } from "../schema";
import { isoAgo, nextId, resetDemo, store, type DContact, type DEnrollment, type DMessage } from "./data";

type S = components["schemas"];

const WORKSPACE_DEFAULTS: Record<string, unknown> = {
  autonomy_default: "approve_each",
  sending_window: "Mon-Fri, 08:00-18:00, recipient local",
  daily_cap_email: 120,
  daily_cap_linkedin: 80,
};

// Demo session — persisted in localStorage so a reload keeps you signed in (like the real cookie),
// and a sign-out genuinely returns you to the login page.
const SESSION_KEY = "sw_demo_session";
const isAuthed = () => typeof localStorage !== "undefined" && localStorage.getItem(SESSION_KEY) === "1";
function setAuthed(v: boolean) {
  if (typeof localStorage === "undefined") return;
  if (v) localStorage.setItem(SESSION_KEY, "1");
  else localStorage.removeItem(SESSION_KEY);
}

// ---- lookups ----
const ctById = (id: string) => store.contacts.find((c) => c.id === id);
const camById = (id: string) => store.campaigns.find((c) => c.id === id);
const enrById = (id: string) => store.enrollments.find((e) => e.id === id);
const msgsOf = (enrId: string) =>
  store.messages.filter((m) => m.enrollment_id === enrId).sort((a, b) => a.created_at.localeCompare(b.created_at));
const lastInbound = (ms: DMessage[]) => [...ms].reverse().find((m) => m.direction === "inbound")?.body ?? null;

// ---- demo-only collections for the newer endpoints (Rail B providers, suppression) ----
const DATA_PROVIDER_CATALOG = [
  { key: "pdl", name: "People Data Labs", live: true, docs_url: "https://docs.peopledatalabs.com" },
  { key: "apollo", name: "Apollo.io", live: true, docs_url: "https://docs.apollo.io" },
  { key: "hunter", name: "Hunter", live: true, docs_url: "https://hunter.io/api-documentation" },
  { key: "cognism", name: "Cognism", live: false, docs_url: "https://www.cognism.com/api" },
  { key: "linkedin", name: "LinkedIn search (Unipile)", live: false, docs_url: "https://www.unipile.com/" },
];
const demoProviderKeys = new Map<string, { last4: string; enabled: boolean; status: string }>();
let demoSuppressions: Array<{ id: string; email: string; reason: string; note: string | null; created_at: string }> = [];
const dumpDemoProvider = (s: (typeof DATA_PROVIDER_CATALOG)[number]) => {
  const cred = demoProviderKeys.get(s.key);
  return { key: s.key, name: s.name, live: s.live, docs_url: s.docs_url, configured: !!cred, enabled: cred?.enabled ?? false, last4: cred?.last4 ?? null, status: cred?.status ?? "not_configured" };
};

// ---- dumps (entity -> response model) ----
function dumpContact(c: DContact): S["ContactOut"] {
  return { id: c.id, full_name: c.full_name, title: c.title, company: c.company, location: c.location, email: c.email, email_status: "unverified", linkedin_url: c.linkedin_url, avatar_url: c.avatar_url, skills: c.skills, source: c.source, notes: c.notes, tags: c.tags, company_size: c.company_size, industry: c.industry };
}
function dumpCampaign(c: (typeof store.campaigns)[number]): S["CampaignOut"] {
  return { id: c.id, name: c.name, status: c.status, autonomy_mode: c.autonomy_mode, from_email: c.from_email, criteria: c.criteria, sequence: c.sequence };
}
function dumpEnrollment(e: DEnrollment): S["EnrollmentOut"] {
  return { id: e.id, campaign_id: e.campaign_id, contact_id: e.contact_id, state: e.state, score: e.score, score_rationale: e.score_rationale, current_step: e.current_step, next_run_at: e.next_run_at, outcome: e.outcome };
}
function enrollmentRow(e: DEnrollment): S["EnrollmentRowOut"] {
  const c = ctById(e.contact_id);
  return { ...dumpEnrollment(e), contact_name: c?.full_name ?? "", contact_title: c?.title ?? null, contact_company: c?.company ?? null, contact_avatar: c?.avatar_url ?? null };
}
function dumpMessage(m: DMessage): S["MessageOut"] {
  return { id: m.id, enrollment_id: m.enrollment_id, direction: m.direction, channel: m.channel, status: m.status, subject: m.subject, body: m.body, sent_at: m.sent_at, scheduled_at: m.scheduled_at, created_at: m.created_at };
}

function contactDetail(c: DContact): S["ContactDetailOut"] {
  const enrs = store.enrollments.filter((e) => e.contact_id === c.id);
  const enrollments = enrs.map((e) => ({ id: e.id, campaign_id: e.campaign_id, campaign_name: camById(e.campaign_id)?.name ?? "", state: e.state, score: e.score, current_step: e.current_step }));
  // Sent + received messages, plus queued (scheduled) next-sends.
  const acts = store.messages
    .filter((m) => enrs.some((e) => e.id === m.enrollment_id) && (m.status !== "draft" || m.scheduled_at))
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
    .slice(0, 40);
  const activity = acts.map((m) => ({ id: m.id, direction: m.direction, channel: m.channel, status: m.status, subject: m.subject, body: m.body, created_at: m.created_at, scheduled_at: m.scheduled_at, campaign_name: camById(enrById(m.enrollment_id)?.campaign_id ?? "")?.name ?? "" }));
  const sentRecv = acts.filter((m) => m.status !== "draft");
  return {
    ...dumpContact(c),
    enrollments,
    activity,
    stats: {
      best_score: enrs.reduce((mx, e) => Math.max(mx, e.score), 0),
      campaigns: enrollments.length,
      replies: sentRecv.filter((m) => m.direction === "inbound").length,
      last_activity_at: sentRecv[0]?.created_at ?? null,
    },
  };
}

function inboxItem(enrId: string): S["InboxItemOut"] {
  const e = enrById(enrId)!;
  const c = ctById(e.contact_id);
  const ms = msgsOf(enrId);
  const last = ms[ms.length - 1];
  const unread = last.direction === "inbound" && (!e.last_read_at || last.created_at > e.last_read_at);
  return {
    enrollment_id: enrId,
    contact_name: c?.full_name ?? null,
    contact_title: c?.title ?? null,
    contact_company: c?.company ?? null,
    contact_avatar: c?.avatar_url ?? null,
    state: e.state,
    outcome: e.outcome,
    channel: ms[0].channel,
    message_count: ms.length,
    unread,
    last_at: last.created_at,
    last_message: dumpMessage(last),
  };
}

function conversation(e: DEnrollment): S["ConversationOut"] {
  const c = ctById(e.contact_id);
  const cam = camById(e.campaign_id);
  const ms = msgsOf(e.id);
  return {
    enrollment: { id: e.id, state: e.state, score: e.score, current_step: e.current_step, outcome: e.outcome },
    contact: { id: c?.id ?? null, name: c?.full_name ?? null, title: c?.title ?? null, company: c?.company ?? null, location: c?.location ?? null, email: c?.email ?? null, linkedin_url: c?.linkedin_url ?? null, avatar_url: c?.avatar_url ?? null, skills: c?.skills ?? [] },
    campaign: { id: cam?.id ?? null, name: cam?.name ?? null, steps: cam?.sequence.length ?? 0 },
    channel: ms.length ? ms[ms.length - 1].channel : "email",
    messages: ms.map(dumpMessage),
  };
}

function dashboard(ws: string): S["DashboardSummary"] {
  const cams = store.campaigns.filter((c) => c.workspace_id === ws);
  const enrs = store.enrollments.filter((e) => e.workspace_id === ws);
  const msgs = store.messages.filter((m) => m.workspace_id === ws);
  const weekAgo = isoAgo(7 * 24 * 60);
  return {
    stats: {
      active_campaigns: cams.filter((c) => c.status === "active").length,
      contacts: store.contacts.filter((c) => c.workspace_id === ws).length,
      awaiting_approval: enrs.filter((e) => e.state === "awaiting_approval").length,
      replies_7d: msgs.filter((m) => m.direction === "inbound" && m.created_at >= weekAgo).length,
    },
    campaigns: cams.map((c) => {
      const ce = enrs.filter((e) => e.campaign_id === c.id);
      return { id: c.id, name: c.name, status: c.status, autonomy_mode: c.autonomy_mode, sourced: ce.length, awaiting: ce.filter((e) => e.state === "awaiting_approval").length, replies: ce.filter((e) => msgs.some((m) => m.enrollment_id === e.id && m.direction === "inbound")).length };
    }),
    approvals: msgs
      .filter((m) => m.status === "draft")
      .map((m) => ({ m, e: enrById(m.enrollment_id)! }))
      .sort((a, b) => b.e.score - a.e.score)
      .slice(0, 6)
      .map(({ m, e }) => ({ enrollment_id: e.id, message_id: m.id, contact_name: ctById(e.contact_id)?.full_name ?? "", contact_avatar: ctById(e.contact_id)?.avatar_url ?? null, title: ctById(e.contact_id)?.title ?? null, subject: m.subject, score: e.score })),
    recent_replies: msgs
      .filter((m) => m.direction === "inbound")
      .sort((a, b) => b.created_at.localeCompare(a.created_at))
      .slice(0, 6)
      .map((m) => { const e = enrById(m.enrollment_id)!; return { contact_name: ctById(e.contact_id)?.full_name ?? "", snippet: m.body.length > 80 ? m.body.slice(0, 80) + "…" : m.body, state: e.state }; }),
  };
}

function analytics(ws: string): S["AnalyticsOut"] {
  const enrs = store.enrollments.filter((e) => e.workspace_id === ws);
  const msgs = store.messages.filter((m) => m.workspace_id === ws);
  const sentEnr = (ch?: string) => new Set(msgs.filter((m) => m.direction === "outbound" && m.status === "sent" && (!ch || m.channel === ch)).map((m) => m.enrollment_id));
  const replyEnr = (ch?: string) => new Set(msgs.filter((m) => m.direction === "inbound" && (!ch || m.channel === ch)).map((m) => m.enrollment_id));
  const rate = (n: number, d: number) => (d ? Math.round((n / d) * 1000) / 1000 : 0);
  const cams = store.campaigns.filter((c) => c.workspace_id === ws);
  return {
    funnel: { sourced: enrs.length, contacted: sentEnr().size, replied: replyEnr().size, handed_off: enrs.filter((e) => e.state === "handed_off").length },
    channels: (["email", "linkedin"] as const).map((ch) => {
      const sent = msgs.filter((m) => m.direction === "outbound" && m.status === "sent" && m.channel === ch).length;
      const replied = msgs.filter((m) => m.direction === "inbound" && m.channel === ch).length;
      return { channel: ch, sent, replied, reply_rate: rate(replied, sent) };
    }),
    campaigns: cams.map((c) => {
      const ce = enrs.filter((e) => e.campaign_id === c.id);
      const replied = ce.filter((e) => msgs.some((m) => m.enrollment_id === e.id && m.direction === "inbound")).length;
      return { id: c.id, name: c.name, status: c.status, sourced: ce.length, replied, handed_off: ce.filter((e) => e.state === "handed_off").length, reply_rate: rate(replied, ce.length) };
    }),
    activity: msgs
      .filter((m) => m.status !== "draft")
      .sort((a, b) => b.created_at.localeCompare(a.created_at))
      .slice(0, 24)
      .map((m) => { const e = enrById(m.enrollment_id)!; const c = ctById(e.contact_id); return { id: m.id, type: m.direction === "inbound" ? "reply" : "sent", title: m.direction === "inbound" ? `${c?.full_name} replied` : `Sent to ${c?.full_name}`, body: m.body.slice(0, 80), campaign_name: camById(e.campaign_id)?.name ?? "", channel: m.channel, created_at: m.created_at }; }),
  };
}

function notifications(ws: string): S["NotificationsOut"] {
  const msgs = store.messages.filter((m) => m.workspace_id === ws);
  const items = [
    ...msgs.filter((m) => m.direction === "inbound").map((m) => { const e = enrById(m.enrollment_id)!; const c = ctById(e.contact_id); return { id: m.id, type: "reply", title: `${c?.full_name} replied`, body: m.body.slice(0, 80), contact_name: c?.full_name ?? "", contact_avatar: c?.avatar_url ?? null, enrollment_id: m.enrollment_id, created_at: m.created_at }; }),
    ...store.enrollments.filter((e) => e.workspace_id === ws && e.state === "handed_off").map((e) => { const c = ctById(e.contact_id); return { id: e.id, type: "handoff", title: `${c?.full_name} handed off`, body: "Interested — ready for your team", contact_name: c?.full_name ?? "", contact_avatar: c?.avatar_url ?? null, enrollment_id: e.id, created_at: e.updated_at }; }),
  ].sort((a, b) => (b.created_at ?? "").localeCompare(a.created_at ?? "")).slice(0, 10);
  const seen = store.notificationsSeenAt;
  return { items, approvals_waiting: msgs.filter((m) => m.status === "draft").length, unread: items.filter((i) => !seen || (i.created_at ?? "") > seen).length };
}

function search(ws: string, q: string): S["SearchOut"] {
  const t = q.toLowerCase();
  return {
    contacts: store.contacts.filter((c) => c.workspace_id === ws && [c.full_name, c.company, c.title].some((v) => (v ?? "").toLowerCase().includes(t))).slice(0, 6).map((c) => ({ id: c.id, full_name: c.full_name, title: c.title, avatar_url: c.avatar_url })),
    campaigns: store.campaigns.filter((c) => c.workspace_id === ws && c.name.toLowerCase().includes(t)).slice(0, 6).map((c) => ({ id: c.id, name: c.name, status: c.status })),
    conversations: store.enrollments.filter((e) => e.workspace_id === ws && msgsOf(e.id).length > 0 && (ctById(e.contact_id)?.full_name ?? "").toLowerCase().includes(t)).slice(0, 6).map((e) => ({ enrollment_id: e.id, contact_name: ctById(e.contact_id)?.full_name ?? "", avatar_url: ctById(e.contact_id)?.avatar_url ?? null, state: e.state })),
  };
}

function me() {
  return { user: store.user, organization: store.org, is_org_admin: store.isOrgAdmin, current_workspace_id: null, workspaces: store.workspaces.map((w) => ({ id: w.id, name: w.name, kind: w.kind })) };
}

const SAMPLE_FIRST = ["Quinn", "Avery", "Jordan", "Reese", "Sasha", "Drew", "Blake", "Emery", "Rowan", "Tatum"];
const SAMPLE_LAST = ["Hart", "Vale", "Cruz", "Fenn", "Lowe", "Pike", "Rhodes", "Sance", "Webb", "York"];
function makeSample(ws: string, n: number): DContact[] {
  return Array.from({ length: n }, (_, i) => {
    const name = `${SAMPLE_FIRST[(store.seq + i) % SAMPLE_FIRST.length]} ${SAMPLE_LAST[(store.seq + i * 3) % SAMPLE_LAST.length]}`;
    const email = `${name.toLowerCase().replace(" ", ".")}.${store.seq + i}@example.com`;
    store.seq += 1;
    return { id: nextId("ct"), workspace_id: ws, full_name: name, title: "Software Engineer", company: "Example Co", location: "Remote · EU", email, linkedin_url: null, avatar_url: `https://i.pravatar.cc/240?u=${encodeURIComponent(email)}`, skills: ["python", "react"], source: "sample", notes: null, tags: [], company_size: "51-200", industry: "Software" };
  });
}

// ---- router ----

export interface Ctx {
  wsId: string | null;
  query: URLSearchParams;
  body: any;
}
export interface Result {
  status: number;
  data: unknown;
}

function match(path: string, pattern: string): Record<string, string> | null {
  const ps = path.split("/").filter(Boolean);
  const qs = pattern.split("/").filter(Boolean);
  if (ps.length !== qs.length) return null;
  const params: Record<string, string> = {};
  for (let i = 0; i < qs.length; i++) {
    if (qs[i].startsWith(":")) params[qs[i].slice(1)] = decodeURIComponent(ps[i]);
    else if (qs[i] !== ps[i]) return null;
  }
  return params;
}
const ok = (data: unknown): Result => ({ status: 200, data });
const notFound = (): Result => ({ status: 404, data: { detail: "not found" } });

export function handle(method: string, path: string, ctx: Ctx): Result {
  const ws = ctx.wsId;
  let p: Record<string, string> | null;

  // ---- auth (a real demo session, so sign-out actually signs out) ----
  if (method === "POST" && path === "/auth/dev-login") {
    setAuthed(true);
    return ok({ ok: true });
  }
  if (method === "GET" && path === "/auth/me") {
    return isAuthed() ? ok(me()) : { status: 401, data: { detail: "not authenticated" } };
  }
  if (method === "POST" && path === "/auth/logout") {
    setAuthed(false);
    return ok({ logout_url: "/login" });
  }

  // ---- contacts ----
  if (method === "GET" && path === "/contacts") {
    const q = (ctx.query.get("q") ?? "").toLowerCase();
    let rows = store.contacts.filter((c) => c.workspace_id === ws);
    if (q) rows = rows.filter((c) => [c.full_name, c.company, c.title].some((v) => (v ?? "").toLowerCase().includes(q)));
    return ok(rows.sort((a, b) => a.full_name.localeCompare(b.full_name)).map(dumpContact));
  }
  if (method === "POST" && path === "/contacts/sample") {
    const created = makeSample(ws!, ctx.body?.count ?? 8);
    store.contacts.push(...created);
    return ok({ created: created.length, contacts: created.map(dumpContact) });
  }
  if (method === "POST" && path === "/contacts/import") {
    const created: DContact[] = (ctx.body?.contacts ?? []).map((c: Partial<DContact>) => ({ id: nextId("ct"), workspace_id: ws!, full_name: c.full_name ?? "Unknown", title: c.title ?? null, company: c.company ?? null, location: c.location ?? null, email: c.email ?? null, linkedin_url: c.linkedin_url ?? null, avatar_url: `https://i.pravatar.cc/240?u=${encodeURIComponent(c.email ?? c.full_name ?? "x")}`, skills: c.skills ?? [], source: "import", notes: c.notes ?? null, tags: c.tags ?? [], company_size: c.company_size ?? null, industry: c.industry ?? null }));
    store.contacts.push(...created);
    return ok({ created: created.length, contacts: created.map(dumpContact) });
  }
  if ((p = match(path, "/contacts/:id"))) {
    const c = ctById(p.id);
    if (!c || c.workspace_id !== ws) return notFound();
    if (method === "GET") return ok(contactDetail(c));
    if (method === "PATCH") {
      Object.assign(c, Object.fromEntries(Object.entries(ctx.body ?? {}).filter(([, v]) => v !== undefined)));
      return ok(dumpContact(c));
    }
    if (method === "DELETE") {
      store.contacts = store.contacts.filter((x) => x.id !== c.id);
      return ok({ status: "deleted", id: c.id });
    }
  }

  // ---- campaigns ----
  if (method === "GET" && path === "/campaigns") return ok(store.campaigns.filter((c) => c.workspace_id === ws).map(dumpCampaign));
  if (method === "POST" && path === "/campaigns") {
    const b = ctx.body ?? {};
    const cam = { id: nextId("cmp"), workspace_id: ws!, name: b.name ?? "Untitled", status: "active", autonomy_mode: b.autonomy_mode ?? "approve_each", from_email: b.from_email ?? null, criteria: b.criteria ?? {}, sequence: b.sequence ?? [], created_at: new Date().toISOString() };
    store.campaigns.push(cam);
    return ok(dumpCampaign(cam));
  }
  if ((p = match(path, "/campaigns/:id/enrollments")) && method === "GET") {
    return ok(store.enrollments.filter((e) => e.campaign_id === p!.id).sort((a, b) => b.score - a.score).map(enrollmentRow));
  }
  if ((p = match(path, "/campaigns/:id/estimate")) && method === "GET") {
    const cam = camById(p.id);
    const contacts = store.contacts.filter((c) => c.workspace_id === ws);
    const matches = cam ? contacts.filter((c) => evaluateFit(c, toTargeting(cam.criteria as Partial<Targeting>)).matched).length : 0;
    return ok({ total: contacts.length, matches });
  }
  if ((p = match(path, "/campaigns/:id/rank")) && method === "POST") {
    const cam = camById(p.id);
    if (!cam) return notFound();
    const existing = new Set(store.enrollments.filter((e) => e.campaign_id === cam.id).map((e) => e.contact_id));
    // Score every un-enrolled contact with the shared fit model; propose those above threshold,
    // best first — exactly what the backend Evaluator + rank_campaign produce.
    const candidates = store.contacts
      .filter((c) => c.workspace_id === ws && !existing.has(c.id))
      .map((c) => ({ c, fit: evaluateFit(c, toTargeting(cam.criteria as Partial<Targeting>)) }))
      .filter((x) => x.fit.matched)
      .sort((a, b) => b.fit.score - a.fit.score)
      .slice(0, 12);
    const created = candidates.map(({ c, fit }) => {
      const e: DEnrollment = { id: nextId("enr"), workspace_id: ws!, campaign_id: cam.id, contact_id: c.id, state: "proposed", score: fit.score, score_rationale: fit.reasons.join("; "), current_step: 0, next_run_at: null, outcome: null, reply_pending: false, last_read_at: null, updated_at: new Date().toISOString(), created_at: new Date().toISOString() };
      store.enrollments.push(e);
      return e;
    });
    return ok({ proposed: created.length, enrollments: created.map(dumpEnrollment) });
  }
  if ((p = match(path, "/campaigns/:id/enroll")) && method === "POST") {
    const cam = camById(p.id);
    if (!cam) return notFound();
    const existing = store.enrollments.find((e) => e.campaign_id === cam.id && e.contact_id === ctx.body?.contact_id);
    if (existing) return ok(dumpEnrollment(existing));
    const e: DEnrollment = { id: nextId("enr"), workspace_id: ws!, campaign_id: cam.id, contact_id: ctx.body?.contact_id, state: "proposed", score: 72, score_rationale: "Manually added", current_step: 0, next_run_at: null, outcome: null, reply_pending: false, last_read_at: null, updated_at: new Date().toISOString(), created_at: new Date().toISOString() };
    store.enrollments.push(e);
    return ok(dumpEnrollment(e));
  }
  for (const [verb, status] of [["pause", "paused"], ["resume", "active"], ["archive", "done"]] as const) {
    if ((p = match(path, `/campaigns/:id/${verb}`)) && method === "POST") {
      const cam = camById(p.id);
      if (!cam) return notFound();
      cam.status = status;
      return ok(dumpCampaign(cam));
    }
  }
  if ((p = match(path, "/campaigns/:id/duplicate")) && method === "POST") {
    const src = camById(p.id);
    if (!src) return notFound();
    const copy = { ...src, id: nextId("cmp"), name: `${src.name} (copy)`, status: "draft", created_at: new Date().toISOString() };
    store.campaigns.push(copy);
    return ok(dumpCampaign(copy));
  }
  if ((p = match(path, "/campaigns/:id"))) {
    const cam = camById(p.id);
    if (!cam || cam.workspace_id !== ws) return notFound();
    if (method === "GET") return ok(dumpCampaign(cam));
    if (method === "PATCH") {
      Object.assign(cam, Object.fromEntries(Object.entries(ctx.body ?? {}).filter(([, v]) => v !== undefined)));
      return ok(dumpCampaign(cam));
    }
    if (method === "DELETE") {
      store.campaigns = store.campaigns.filter((x) => x.id !== cam.id);
      store.enrollments = store.enrollments.filter((e) => e.campaign_id !== cam.id);
      return ok({ status: "deleted", id: cam.id });
    }
  }

  // ---- enrollments ----
  if (method === "POST" && path === "/enrollments/bulk-approve") {
    const ids: string[] = ctx.body?.ids ?? [];
    ids.forEach((eid) => { const e = enrById(eid); if (e) { e.state = "awaiting_approval"; e.current_step = 1; } });
    return ok({ approved: ids.length, ids });
  }
  if ((p = match(path, "/enrollments/:id/approve")) && method === "POST") {
    const e = enrById(p.id); if (!e) return notFound();
    e.state = "awaiting_approval"; e.current_step = 1;
    return ok({ id: e.id, state: e.state, outcome: e.outcome, next_run_at: e.next_run_at });
  }
  if ((p = match(path, "/enrollments/:id/handoff")) && method === "POST") {
    const e = enrById(p.id); if (!e) return notFound();
    e.state = "handed_off"; e.outcome = "interested"; e.reply_pending = false; e.updated_at = new Date().toISOString();
    return ok({ id: e.id, state: e.state, outcome: e.outcome, next_run_at: null });
  }
  if ((p = match(path, "/enrollments/:id/opt-out")) && method === "POST") {
    const e = enrById(p.id); if (!e) return notFound();
    e.state = "opted_out"; e.outcome = "opted_out"; e.reply_pending = false; e.updated_at = new Date().toISOString();
    return ok({ id: e.id, state: e.state, outcome: e.outcome, next_run_at: null });
  }
  if ((p = match(path, "/enrollments/:id/messages")) && method === "GET") return ok(msgsOf(p.id).map(dumpMessage));

  // ---- approvals / messages ----
  if (method === "GET" && path === "/approvals") {
    return ok(
      store.messages
        .filter((m) => m.workspace_id === ws && m.status === "draft")
        .map((m) => ({ m, e: enrById(m.enrollment_id)! }))
        .sort((a, b) => b.e.score - a.e.score)
        .map(({ m, e }) => { const c = ctById(e.contact_id); return { ...dumpMessage(m), contact_name: c?.full_name ?? "", contact_title: c?.title ?? null, contact_company: c?.company ?? null, contact_avatar: c?.avatar_url ?? null, score: e.score, step: e.current_step }; }),
    );
  }
  if ((p = match(path, "/messages/:id/approve")) && method === "POST") {
    const m = store.messages.find((x) => x.id === p!.id); if (!m) return notFound();
    m.status = "sent"; m.sent_at = new Date().toISOString();
    const e = enrById(m.enrollment_id); if (e) e.state = "awaiting_reply";
    return ok(dumpMessage(m));
  }
  if ((p = match(path, "/messages/:id")) && method === "PATCH") {
    const m = store.messages.find((x) => x.id === p!.id); if (!m) return notFound();
    if (ctx.body?.subject != null) m.subject = ctx.body.subject;
    if (ctx.body?.body != null) m.body = ctx.body.body;
    return ok(dumpMessage(m));
  }

  // ---- inbox ----
  if (method === "GET" && path === "/inbox") {
    const seen = new Set(store.messages.filter((m) => m.workspace_id === ws).map((m) => m.enrollment_id));
    return ok([...seen].map(inboxItem).sort((a, b) => (b.last_at ?? "").localeCompare(a.last_at ?? "")));
  }
  if ((p = match(path, "/inbox/:id/reply")) && method === "POST") {
    const e = enrById(p.id); if (!e) return notFound();
    const ms = msgsOf(e.id);
    const ch = ms.length ? ms[ms.length - 1].channel : "email";
    const m: DMessage = { id: nextId("msg"), workspace_id: ws!, enrollment_id: e.id, direction: "outbound", channel: ch, status: "sent", subject: null, body: ctx.body?.text ?? "", sent_at: new Date().toISOString(), scheduled_at: null, created_at: new Date().toISOString() };
    store.messages.push(m); e.reply_pending = false;
    return ok(dumpMessage(m));
  }
  if ((p = match(path, "/inbox/:id/draft")) && method === "POST") {
    const e = enrById(p.id); if (!e) return notFound();
    const c = ctById(e.contact_id); const li = lastInbound(msgsOf(e.id));
    const first = c?.full_name.split(" ")[0] ?? "there";
    const text = li && /price|pricing|comp|salary/i.test(li) ? `Happy to share, ${first}! Let's hop on a quick call to walk through specifics — does this week work?` : `Thanks for the note, ${first}! Would you be open to a quick 20-minute call this week?`;
    return ok({ text });
  }
  if ((p = match(path, "/inbox/:id/summary")) && method === "GET") {
    const e = enrById(p.id); if (!e) return notFound();
    const summary = e.state === "handed_off" ? "Interested and a call is scheduled — ready to hand off." : e.state === "opted_out" ? "Politely declined — conversation closed." : e.reply_pending ? "They replied with a question; you owe them a response." : "Outreach in progress.";
    return ok({ summary });
  }
  if ((p = match(path, "/inbox/:id/read")) && method === "POST") {
    const e = enrById(p.id); if (!e) return notFound();
    e.last_read_at = new Date().toISOString();
    return ok({ status: "read", id: e.id });
  }
  if ((p = match(path, "/inbox/:id")) && method === "GET") {
    const e = enrById(p.id); if (!e || e.workspace_id !== ws) return notFound();
    return ok(conversation(e));
  }

  // ---- read surfaces ----
  if (method === "GET" && path === "/dashboard/summary") return ok(dashboard(ws!));
  if (method === "GET" && path === "/analytics") return ok(analytics(ws!));
  if (method === "GET" && path === "/search") return ok(search(ws!, ctx.query.get("q") ?? ""));
  if (method === "GET" && path === "/notifications") return ok(notifications(ws!));
  if (method === "POST" && path === "/notifications/read") { store.notificationsSeenAt = new Date().toISOString(); return ok({ status: "read" }); }
  if (method === "GET" && path === "/audit") return ok([...store.audit].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 50));

  // ---- settings ----
  if (method === "GET" && path === "/settings/members") return ok(store.members);
  if (method === "POST" && path === "/settings/members/invite") {
    const m = { id: nextId("usr"), name: ctx.body?.name ?? "", email: ctx.body?.email ?? "", role: ctx.body?.role ?? "member", scope: "organization" };
    store.members.push(m);
    return ok({ id: m.id, name: m.name, email: m.email, role: m.role });
  }
  if ((p = match(path, "/settings/members/:id")) && method === "PATCH") {
    const m = store.members.find((x) => x.id === p!.id); if (!m) return notFound();
    m.role = ctx.body?.role ?? m.role; return ok({ id: m.id, role: m.role });
  }
  if ((p = match(path, "/settings/members/:id")) && method === "DELETE") {
    store.members = store.members.filter((x) => x.id !== p!.id); return ok({ status: "removed", id: p.id });
  }
  if (method === "GET" && path === "/settings/connections") return ok(store.connections);
  if ((p = match(path, "/settings/connections/:provider/connect")) && method === "POST") {
    let c = store.connections.find((x) => x.provider === p!.provider);
    if (c) c.status = "ok";
    else { c = { id: nextId("con"), provider: p.provider, status: "ok", seat_type: p.provider === "linkedin" ? "recruiter" : "email", user_email: store.user.email, external_id: null }; store.connections.push(c); }
    return ok(c);
  }
  if ((p = match(path, "/settings/connections/:id/disconnect")) && method === "POST") {
    store.connections = store.connections.filter((x) => x.id !== p!.id); return ok({ status: "disconnected", id: p.id });
  }
  if ((p = match(path, "/settings/connections/:id/reauth")) && method === "POST") {
    const c = store.connections.find((x) => x.id === p!.id); if (!c) return notFound();
    c.status = "ok"; return ok(c);
  }
  if (path === "/settings/workspace") {
    const w = store.workspaces.find((x) => x.id === ws); if (!w) return notFound();
    if (method === "PATCH") {
      if (ctx.body?.name != null) w.name = ctx.body.name;
      if (ctx.body?.brand_voice != null) w.brand_voice = ctx.body.brand_voice;
      if (ctx.body?.settings) w.settings = { ...w.settings, ...ctx.body.settings };
    }
    return ok({ id: w.id, name: w.name, brand_voice: w.brand_voice, settings: { ...WORKSPACE_DEFAULTS, ...w.settings } });
  }

  // ---- people discovery (Rail B) ----
  if (method === "GET" && path === "/people/providers") return ok([{ key: "demo", name: "Demo data", search: true, enrich: true, verify_email: true }]);
  if (method === "POST" && path === "/people/search") {
    const q = ctx.body ?? {};
    const titles: string[] = q.titles?.length ? q.titles : ["VP of Sales", "Senior Backend Engineer"];
    const skills: string[] = q.skills?.length ? q.skills : ["Python"];
    const locs: string[] = q.locations?.length ? q.locations : ["Remote · EU"];
    const first = ["Aisha", "Marcus", "Sofia", "Diego", "Lena", "Raj", "Mia", "Theo"];
    const last = ["Berg", "Lee", "Wong", "Santos", "Park", "Kumar", "Becker", "Ruiz"];
    const comps = ["Northwind", "Globex", "Initech", "Lumen"];
    const n = Math.min(q.limit ?? 10, 25);
    const results = Array.from({ length: n }, (_, i) => {
      const fn = `${first[i % first.length]} ${last[(i * 3) % last.length]}`;
      const company = comps[i % comps.length];
      const email = `${fn.toLowerCase().replace(/ /g, ".")}@${company.toLowerCase()}.com`;
      return { provider: "demo", external_id: `demo-${i}`, full_name: fn, title: titles[i % titles.length], company, location: locs[i % locs.length], email, email_status: "unverified", linkedin_url: `https://linkedin.com/in/${fn.toLowerCase().replace(/ /g, "")}`, avatar_url: null, skills, company_size: "201-500", industry: "B2B SaaS", phone: null, confidence: 80, score: Math.max(20, 100 - i * 4), rationale: "matches criteria" };
    });
    return ok({ results, providers: ["demo"] });
  }
  if (method === "POST" && path === "/people/import") {
    const hits = ctx.body?.hits ?? [];
    const created: string[] = [];
    for (const h of hits) {
      if (h.email && store.contacts.some((c) => c.email === h.email)) continue;
      const c: DContact = { id: nextId("ct"), workspace_id: ws!, full_name: h.full_name ?? "Unknown", title: h.title ?? null, company: h.company ?? null, location: h.location ?? null, email: h.email ?? null, linkedin_url: h.linkedin_url ?? null, avatar_url: h.avatar_url ?? null, skills: h.skills ?? [], source: h.provider ?? "demo", notes: null, tags: [], company_size: h.company_size ?? null, industry: h.industry ?? null };
      store.contacts.push(c);
      created.push(c.id);
    }
    return ok({ imported: created.length, contact_ids: created });
  }
  if (method === "POST" && path === "/people/enrich") return ok({ hit: null });
  if (method === "GET" && path === "/people/usage") return ok([]);

  // ---- data-provider credentials (BYO) ----
  if (method === "GET" && path === "/settings/data-providers") return ok(DATA_PROVIDER_CATALOG.map(dumpDemoProvider));
  if ((p = match(path, "/settings/data-providers/:provider/verify")) && method === "POST") {
    const cred = demoProviderKeys.get(p.provider);
    const s = DATA_PROVIDER_CATALOG.find((x) => x.key === p!.provider);
    if (!cred || !s) return notFound();
    cred.status = "ok";
    return ok(dumpDemoProvider(s));
  }
  if ((p = match(path, "/settings/data-providers/:provider"))) {
    const s = DATA_PROVIDER_CATALOG.find((x) => x.key === p!.provider);
    if (!s) return notFound();
    if (method === "PUT") {
      const key = (ctx.body?.api_key ?? "").trim();
      demoProviderKeys.set(s.key, { last4: key.slice(-4), enabled: ctx.body?.enabled ?? true, status: "unverified" });
      return ok(dumpDemoProvider(s));
    }
    if (method === "DELETE") {
      demoProviderKeys.delete(s.key);
      return ok({ status: "removed", id: s.key });
    }
  }

  // ---- suppression ----
  if (method === "GET" && path === "/suppressions") return ok([...demoSuppressions].reverse());
  if (method === "POST" && path === "/suppressions") {
    const email = (ctx.body?.email ?? "").trim().toLowerCase();
    if (!email) return { status: 400, data: { detail: "email required" } };
    const row = { id: nextId("sup"), email, reason: ctx.body?.reason ?? "manual", note: ctx.body?.note ?? null, created_at: new Date().toISOString() };
    demoSuppressions.push(row);
    return ok(row);
  }
  if ((p = match(path, "/suppressions/:email")) && method === "DELETE") {
    const email = decodeURIComponent(p.email);
    demoSuppressions = demoSuppressions.filter((s) => s.email !== email);
    return ok({ status: "removed", email });
  }

  // ---- contact GDPR erasure ----
  if ((p = match(path, "/contacts/:id/forget")) && method === "POST") {
    const c = ctById(p.id);
    if (!c || c.workspace_id !== ws) return notFound();
    if (c.email) demoSuppressions.push({ id: nextId("sup"), email: c.email.toLowerCase(), reason: "manual", note: "erased (GDPR)", created_at: new Date().toISOString() });
    store.contacts = store.contacts.filter((x) => x.id !== c.id);
    return ok({ status: "forgotten", id: c.id });
  }

  // ---- org export ----
  if (method === "GET" && path === "/settings/export")
    return ok({ exported_at: new Date().toISOString(), organization: { id: "org_demo", name: "Acme Talent", slug: "acme-talent", data_region: "us" }, workspaces: store.workspaces.map((w) => ({ id: w.id, name: w.name, kind: w.kind })), contacts: store.contacts.map((c) => ({ id: c.id, full_name: c.full_name, title: c.title, company: c.company, email: c.email })), campaigns: store.campaigns.map((c) => ({ id: c.id, name: c.name, status: c.status, criteria: c.criteria })), enrollments: [], messages: [] });

  // ---- admin ----
  if (method === "POST" && path === "/admin/run-due") return ok({ processed: 0 });

  return notFound();
}

export { resetDemo };
