/**
 * In-memory demo dataset. Entities mirror the backend DB tables 1:1; ./handlers.ts derives the exact
 * API response shapes from them. Three fully-populated verticals (recruiting, enterprise sales,
 * partnerships) with varied content, multi-week timelines, deep multi-channel threads, CRM
 * enrichment, campaign lifecycle (active/paused/draft/archived) and a generated audit trail.
 */

export interface DWorkspace {
  id: string;
  name: string;
  kind: string;
  brand_voice: string | null;
  settings: Record<string, unknown>;
}
export interface DContact {
  id: string;
  workspace_id: string;
  full_name: string;
  title: string | null;
  company: string | null;
  location: string | null;
  email: string | null;
  linkedin_url: string | null;
  avatar_url: string | null;
  skills: string[];
  source: string;
  notes: string | null;
  tags: string[];
  company_size: string | null;
  industry: string | null;
}
export interface DCampaign {
  id: string;
  workspace_id: string;
  name: string;
  status: string;
  autonomy_mode: string;
  from_email: string | null;
  criteria: Record<string, unknown>;
  sequence: Record<string, unknown>[];
  created_at: string;
}
export interface DEnrollment {
  id: string;
  workspace_id: string;
  campaign_id: string;
  contact_id: string;
  state: string;
  score: number;
  score_rationale: string | null;
  current_step: number;
  next_run_at: string | null;
  outcome: string | null;
  reply_pending: boolean;
  last_read_at: string | null;
  updated_at: string;
  created_at: string;
}
export interface DMessage {
  id: string;
  workspace_id: string;
  enrollment_id: string;
  direction: "inbound" | "outbound";
  channel: "email" | "linkedin";
  status: "draft" | "sent";
  subject: string | null;
  body: string;
  sent_at: string | null;
  scheduled_at: string | null;
  created_at: string;
}
export interface DConnection {
  id: string;
  provider: string;
  status: string;
  seat_type: string;
  user_email: string;
  external_id: string | null;
}
export interface DMember {
  id: string;
  name: string;
  email: string;
  role: string;
  scope: string;
}
export interface DAudit {
  id: string;
  action: string;
  summary: string;
  target_type: string | null;
  target_id: string | null;
  actor_name: string | null;
  workspace_id: string | null;
  created_at: string;
}

export interface DemoStore {
  org: { id: string; name: string };
  user: { id: string; email: string; name: string };
  isOrgAdmin: boolean;
  workspaces: DWorkspace[];
  contacts: DContact[];
  campaigns: DCampaign[];
  enrollments: DEnrollment[];
  messages: DMessage[];
  connections: DConnection[];
  members: DMember[];
  audit: DAudit[];
  notificationsSeenAt: string | null;
  seq: number;
}

// ---- helpers ----
function mulberry32(seed: number) {
  let a = seed;
  return () => {
    a |= 0;
    a = (a + 0x6d2b79f5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
const NOW = Date.now();
const D = 86_400_000;
const at = (ms: number) => new Date(ms).toISOString();
const daysAgo = (d: number) => NOW - d * D;
const avatar = (seed: string) => `https://i.pravatar.cc/240?u=${encodeURIComponent(seed)}`;
const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "");
const hash = (s: string) => [...s].reduce((a, c) => (a * 31 + c.charCodeAt(0)) >>> 0, 7);
const pickBy = <T>(arr: T[], seed: number) => arr[seed % arr.length];
function fill(t: string, c: DContact): string {
  const first = c.full_name.split(" ")[0];
  return (t || "")
    .replace(/\{first_name\}|\{first\}/g, first)
    .replace(/\{company\}/g, c.company ?? "your company")
    .replace(/\{title\}/g, c.title ?? "your role")
    .replace(/\{skill\}/g, c.skills[0] ?? "your work");
}

let _id = 0;
const id = (p: string) => `${p}_${(++_id).toString(36).padStart(6, "0")}`;

// ---- vertical configuration ----
type Kind = "eng" | "sales" | "partner";
interface Firm {
  name: string;
  size: string;
  industry: string;
}
interface Content {
  openers: { subject: string; body: string }[];
  followups: { body: string }[];
  drafts: { subject: string; body: string }[];
  interested: string[];
  question: string[];
  answer: string[];
  schedule: string[];
  decline: string[];
  notes: string[];
  tags: string[];
  sources: string[];
}
interface Vertical {
  first: string[];
  last: string[];
  roles: { title: string; skills: string[] }[];
  companies: Firm[];
  content: Content;
}

const LOCATIONS = ["Berlin, DE", "London, UK", "Amsterdam, NL", "Lisbon, PT", "Dublin, IE", "Remote · EU", "Munich, DE", "Paris, FR"];

const VERTICALS: Record<Kind, Vertical> = {
  eng: {
    first: ["Aisha", "Marcus", "Sofia", "Diego", "Lena", "Raj", "Mia", "Theo", "Hana", "Omar", "Clara", "Ravi", "Nadia", "Hugo", "Wei", "Bruno", "Elif", "Tomas"],
    last: ["Berg", "Lee", "Wong", "Santos", "Park", "Kumar", "Becker", "Ruiz", "Patel", "Novak", "Haddad", "Costa", "Mensah", "Ito", "Walsh", "Okafor", "Bauer", "Reyes"],
    roles: [
      { title: "Senior Backend Engineer", skills: ["Python", "Go", "Postgres"] },
      { title: "Staff Engineer", skills: ["Distributed Systems", "Kafka", "AWS"] },
      { title: "Data Platform Engineer", skills: ["Spark", "Airflow", "dbt"] },
      { title: "ML Engineer", skills: ["PyTorch", "LLMs", "Python"] },
      { title: "Site Reliability Engineer", skills: ["Kubernetes", "Terraform", "Go"] },
    ],
    companies: [
      { name: "Hooli", size: "1,000-5,000", industry: "Cloud Infrastructure" },
      { name: "Globex", size: "501-1,000", industry: "Fintech" },
      { name: "Initech", size: "201-500", industry: "Enterprise Software" },
      { name: "Umbra", size: "51-200", industry: "Cybersecurity" },
      { name: "Vandelay", size: "501-1,000", industry: "Logistics" },
      { name: "Stark Labs", size: "201-500", industry: "Robotics" },
    ],
    content: {
      openers: [
        { subject: "Your work on {skill}, {first}", body: "Hi {first} — came across your background in {skill} at {company}. We're building out a {title} team and your profile stood out. Open to a quick chat?" },
        { subject: "{company} → us?", body: "Hey {first}, I know {company} does serious engineering. We've got a {title} role solving exactly the kind of {skill} problems you've worked on. Worth 15 minutes?" },
        { subject: "Quick question, {first}", body: "Hi {first}, your {skill} experience caught my eye. We're hiring a {title} — fully remote-friendly, strong team. Would you be open to hearing more?" },
      ],
      followups: [
        { body: "Following up here, {first} — happy to share the JD and comp range up front so you can decide if it's worth a conversation." },
        { body: "Bumping this, {first}. No pressure at all — even a 'not now' helps me know whether to keep you in mind for later." },
      ],
      drafts: [
        { subject: "The {title} role, {first}", body: "Hi {first}, wanted to share a bit more: it's a {title} role owning {skill} systems end-to-end. Comp is competitive + equity. Open to a call this week?" },
        { subject: "Re: {company}", body: "Hi {first} — circling back. The team's small and senior, and you'd have real ownership over the {skill} stack. Worth a quick intro?" },
      ],
      interested: [
        "Thanks for reaching out! I'm not actively looking but I'm always open to a strong team. What's the comp range?",
        "Appreciate the note — the {skill} angle is interesting. Tell me a bit more about the team and how senior the role is.",
        "Hi — happy to chat. I've been at {company} a while so timing could be right. Is it remote?",
      ],
      question: [
        "Interesting — is this fully remote, and what's the team size?",
        "What does the comp range look like, and is there meaningful equity?",
        "Curious what the {skill} stack looks like day to day before I commit to a call.",
      ],
      answer: [
        "Great questions! It's fully remote within the EU, ~8 engineers, and comp is €120–150k + equity depending on level. Want to grab 20 minutes this week?",
        "Happy to share — strong equity, small senior team, and you'd own the {skill} roadmap. Does Thursday at 3pm work for a quick intro?",
      ],
      schedule: [
        "Perfect — does Thursday at 3pm CET work for a 20-minute intro with the hiring manager?",
        "Great — I'll set up a quick call. Would Wednesday morning or Friday afternoon suit you better?",
      ],
      decline: [
        "Appreciate it, but I just signed for another year at {company} — not looking right now.",
        "Thanks for thinking of me! Not the right time, but feel free to reach back out in 6 months.",
      ],
      notes: [
        "Passive candidate — strong on {skill}. Referred via a current teammate.",
        "Opened the last two emails multiple times; warm. Mentioned timing might be right.",
        "Top of the shortlist for the {title} req. Senior, low-flight-risk profile.",
        "Replied quickly and engaged — moving toward an intro call.",
      ],
      tags: ["passive", "strong match", "responsive", "senior", "warm intro", "shortlist"],
      sources: ["linkedin", "github", "referral", "inbound", "ats"],
    },
  },
  sales: {
    first: ["Priya", "Daniel", "Grace", "Mateo", "Yuki", "Noah", "Elena", "Kofi", "Maya", "Pavel", "Ingrid", "Tom", "Sara", "Luca"],
    last: ["Raman", "Foster", "Bauer", "Silva", "Tanaka", "Moreau", "Holt", "Ferraro", "Adeyemi", "Khan", "Doe", "Ruiz", "Klein", "Marsh"],
    roles: [
      { title: "VP of Sales", skills: ["Salesforce", "Outbound", "Enterprise"] },
      { title: "Head of RevOps", skills: ["HubSpot", "Forecasting", "Analytics"] },
      { title: "Director of Demand Gen", skills: ["ABM", "Marketo", "Paid"] },
      { title: "Chief Revenue Officer", skills: ["Pipeline", "Enterprise", "GTM"] },
      { title: "Sales Operations Lead", skills: ["Salesforce", "Enablement", "Territory"] },
    ],
    companies: [
      { name: "Northwind", size: "501-1,000", industry: "B2B SaaS" },
      { name: "Contoso", size: "1,000-5,000", industry: "MarTech" },
      { name: "Brightwave", size: "201-500", industry: "FinTech" },
      { name: "Acme Cloud", size: "5,000+", industry: "Cloud Infrastructure" },
      { name: "Lumen", size: "51-200", industry: "DevTools" },
      { name: "Cobalt", size: "201-500", industry: "Security" },
    ],
    content: {
      openers: [
        { subject: "Cutting {company}'s SDR ramp time", body: "Hi {first}, saw {company} is scaling outbound. We help RevOps teams automate the top of funnel without adding headcount. Worth a quick look?" },
        { subject: "{company} + 3x more replies", body: "Hey {first} — teams like yours are seeing 3x reply rates by letting AI agents run multichannel outbound with a human approving each send. Open to a 20-min walkthrough?" },
        { subject: "A question for the {title}", body: "Hi {first}, quick one — how is {company} currently handling outbound at scale? We've helped similar {industry} teams book 40% more meetings." },
      ],
      followups: [
        { body: "Following up, {first} — happy to send a 2-minute Loom showing how it'd work for a team your size before we ever talk." },
        { body: "Circling back. If outbound isn't a priority this quarter no worries — just let me know and I'll close the loop." },
      ],
      drafts: [
        { subject: "ROI for {company}", body: "Hi {first}, put together a quick ROI estimate for a team your size — happy to walk through it. Does this week work?" },
        { subject: "Re: {company} outbound", body: "Hi {first} — circling back with a quick breakdown of how we'd cut your SDR ramp. Worth 20 minutes?" },
      ],
      interested: [
        "We are indeed scaling the team. How does this compare to Outreach?",
        "Timely — we're re-evaluating our outbound stack this quarter. What does pricing look like?",
        "Interesting. We've struggled with reply rates. Can you show me real numbers from a {industry} team?",
      ],
      question: [
        "Curious about pricing and whether it integrates with Salesforce?",
        "Does this replace our SDRs or augment them? And how's deliverability handled?",
        "What's the ramp time to see results, and do you support multichannel (email + LinkedIn)?",
      ],
      answer: [
        "Great questions — it augments your SDRs (they approve every send), integrates natively with Salesforce, and most teams see lift in ~2 weeks. Want to see it on your data Wednesday?",
        "It's multichannel out of the box (email + LinkedIn), Salesforce-native, and priced per seat. Happy to run a quick walkthrough — does Thursday work?",
      ],
      schedule: [
        "Perfect — would Wednesday at 11am work for a 20-minute walkthrough with your RevOps lead?",
        "Great — I'll send an invite. Does Thursday afternoon or Friday morning suit you better?",
      ],
      decline: [
        "Not a priority this quarter — we just renewed our current tool. Worth a look in Q4.",
        "Appreciate it, but budget's frozen until next fiscal. Reach back out in January?",
      ],
      notes: [
        "Champion — owns the outbound number at {company}. Re-evaluating their stack now.",
        "Inbound from our webinar; high intent. Asked about Salesforce integration.",
        "Decision-maker for a {company} expansion deal. Budget holder confirmed.",
        "Engaged on LinkedIn before replying — warm. Mentioned a Q3 initiative.",
      ],
      tags: ["champion", "decision-maker", "evaluating", "budget holder", "inbound", "expansion"],
      sources: ["apollo", "zoominfo", "salesforce", "linkedin", "inbound", "referral"],
    },
  },
  partner: {
    first: ["Anika", "Sven", "Rosa", "Idris", "Mei", "Felix", "Zara", "Jonas", "Amara", "Petra", "Caleb", "Nina"],
    last: ["Voss", "Lindqvist", "Mwangi", "Hassan", "Chen", "Albrecht", "Okonkwo", "Brandt", "Diallo", "Novik", "Fischer", "Sato"],
    roles: [
      { title: "Head of Partnerships", skills: ["Channel", "Alliances", "GTM"] },
      { title: "Founder & CEO", skills: ["Strategy", "Fundraising", "Product"] },
      { title: "Director of Business Development", skills: ["BD", "Integrations", "Ecosystem"] },
      { title: "VP Strategic Alliances", skills: ["Co-sell", "Enterprise", "Channel"] },
      { title: "Ecosystem Lead", skills: ["Marketplace", "Integrations", "Developer Rel"] },
    ],
    companies: [
      { name: "Foundry Labs", size: "11-50", industry: "Dev Platform" },
      { name: "Meridian", size: "51-200", industry: "Systems Integrator" },
      { name: "Halcyon", size: "201-500", industry: "Cloud Consulting" },
      { name: "Polaris Digital", size: "11-50", industry: "Agency" },
      { name: "Keystone", size: "501-1,000", industry: "Platform" },
      { name: "Arcadia", size: "51-200", industry: "Data Services" },
    ],
    content: {
      openers: [
        { subject: "{company} × us — partnership?", body: "Hi {first}, I lead partnerships and think {company} could be a great fit for our program — your {skill} focus overlaps nicely with our customers. Worth exploring?" },
        { subject: "An integration idea for {company}", body: "Hey {first} — our mutual customers keep asking for a {company} integration. Would love to scope a co-sell or marketplace listing. Open to a chat?" },
        { subject: "Co-marketing with {company}?", body: "Hi {first}, we're expanding our partner ecosystem and {company} is top of my list given your {industry} reach. Could we find 20 minutes?" },
      ],
      followups: [
        { body: "Following up, {first} — I can send our partner one-pager and a couple of example co-sell wins if that's useful." },
        { body: "Circling back. Even a quick intro to the right person on your side would be a great start." },
      ],
      drafts: [
        { subject: "Partner program — {company}", body: "Hi {first}, sharing our partner tiers and the co-marketing support we offer. Worth a quick scoping call?" },
        { subject: "Re: {company} integration", body: "Hi {first} — following up on the integration idea. Happy to loop in our solutions team. Does this week work?" },
      ],
      interested: [
        "We've been looking to expand our integrations — this could be timely. What does the program look like?",
        "Interesting. Our customers do ask about this. How does co-sell revenue share work?",
        "Open to it. We're picky about partners though — what kind of joint GTM support do you offer?",
      ],
      question: [
        "What's the revenue share / referral model, and is there a marketplace listing?",
        "Do you offer co-marketing budget, and what's expected of us technically?",
        "How many joint customers do you have today, and who'd own the relationship?",
      ],
      answer: [
        "Great — we do 20% referral + co-marketing budget, a marketplace listing, and a dedicated partner manager. Want to scope it Wednesday?",
        "Co-sell with revenue share, joint webinars, and lightweight tech lift on your side. Happy to walk through tiers — does Thursday work?",
      ],
      schedule: [
        "Perfect — would Wednesday at 2pm work to scope the partnership with our alliances lead?",
        "Great — I'll set up a partner scoping call. Does early next week suit you?",
      ],
      decline: [
        "Appreciate it — we're heads-down on product this quarter and pausing new partnerships.",
        "Thanks, but our partner roadmap is full for now. Let's reconnect next quarter.",
      ],
      notes: [
        "Warm — mutual customers requesting the integration. Strong ecosystem fit.",
        "Founder-led; fast mover. Interested in co-marketing for {industry}.",
        "Strategic alliance potential. Owns the partner roadmap at {company}.",
        "Engaged after the follow-up; wants to scope co-sell.",
      ],
      tags: ["strategic", "co-sell", "integration", "warm", "founder", "ecosystem fit"],
      sources: ["referral", "inbound", "linkedin", "event", "marketplace"],
    },
  },
};

// ---- contacts ----
function makeContacts(ws: string, kind: Kind, n: number, rng: () => number): DContact[] {
  const v = VERTICALS[kind];
  const combos: [string, string][] = [];
  for (const f of v.first) for (const l of v.last) combos.push([f, l]);
  for (let i = combos.length - 1; i > 0; i--) {
    const j = Math.floor(rng() * (i + 1));
    [combos[i], combos[j]] = [combos[j], combos[i]];
  }
  return combos.slice(0, n).map(([first, last]) => {
    const role = v.roles[Math.floor(rng() * v.roles.length)];
    const firm = v.companies[Math.floor(rng() * v.companies.length)];
    const email = `${first.toLowerCase()}.${last.toLowerCase()}@${slug(firm.name)}.com`;
    const h = hash(first + last);
    const tagN = 1 + (h % 3);
    const tags = [...v.content.tags].sort(() => (h % 7) - 3).slice(0, tagN);
    const c: DContact = {
      id: id("ct"),
      workspace_id: ws,
      full_name: `${first} ${last}`,
      title: role.title,
      company: firm.name,
      location: LOCATIONS[h % LOCATIONS.length],
      email,
      linkedin_url: `https://linkedin.com/in/${first.toLowerCase()}${last.toLowerCase()}`,
      avatar_url: avatar(email),
      skills: [...role.skills],
      source: pickBy(v.content.sources, h),
      notes: null,
      tags,
      company_size: firm.size,
      industry: firm.industry,
    };
    c.notes = fill(pickBy(v.content.notes, h), c);
    return c;
  });
}

// ---- threads (deep, multi-channel, realistic timeline) ----
function buildThread(store: DemoStore, enr: DEnrollment, cam: DCampaign, ct: DContact, kind: Kind, startDay: number) {
  const v = VERTICALS[kind].content;
  const h = hash(ct.id);
  const ch1 = (cam.sequence[0]?.channel as "email" | "linkedin") ?? "email";
  const ch2 = (cam.sequence[1]?.channel as "email" | "linkedin") ?? "linkedin";
  let cursor = startDay;
  const add = (m: {
    direction: "inbound" | "outbound";
    channel: "email" | "linkedin";
    status: "draft" | "sent";
    subject: string | null;
    body: string;
    day: number;
    scheduled?: number;
  }) => {
    const ms = at(daysAgo(m.day));
    store.messages.push({
      id: id("msg"),
      workspace_id: enr.workspace_id,
      enrollment_id: enr.id,
      direction: m.direction,
      channel: m.channel,
      status: m.status,
      subject: m.subject,
      body: fill(m.body, ct),
      sent_at: m.status === "sent" ? ms : null,
      scheduled_at: m.scheduled != null ? at(NOW + m.scheduled * D) : null,
      created_at: ms,
    });
  };
  const opener = pickBy(v.openers, h);

  if (enr.state === "proposed") return;
  if (enr.state === "scheduled") {
    // queued first touch — not sent yet
    add({ direction: "outbound", channel: ch1, status: "draft", subject: ch1 === "email" ? fill(opener.subject, ct) : null, body: opener.body, day: 0, scheduled: 1 + (h % 3) });
    return;
  }
  if (enr.state === "awaiting_approval") {
    const d = pickBy(v.drafts, h);
    add({ direction: "outbound", channel: ch1, status: "draft", subject: ch1 === "email" ? fill(d.subject, ct) : null, body: d.body, day: cursor });
    return;
  }

  // touch 1
  add({ direction: "outbound", channel: ch1, status: "sent", subject: ch1 === "email" ? fill(opener.subject, ct) : null, body: opener.body, day: cursor });
  cursor -= 3 + (h % 3);
  // touch 2 — channel switch (the multi-channel story)
  add({ direction: "outbound", channel: ch2, status: "sent", subject: null, body: pickBy(v.followups, h).body, day: cursor });

  if (enr.state === "awaiting_reply" && !enr.reply_pending) return; // still waiting on them

  cursor -= 1 + (h % 2);
  if (enr.state === "awaiting_reply" && enr.reply_pending) {
    add({ direction: "inbound", channel: ch2, status: "sent", subject: null, body: pickBy(v.question, h), day: cursor });
    return;
  }
  if (enr.state === "opted_out") {
    add({ direction: "inbound", channel: ch2, status: "sent", subject: null, body: pickBy(v.decline, h), day: cursor });
    return;
  }
  if (enr.state === "handed_off") {
    add({ direction: "inbound", channel: ch2, status: "sent", subject: null, body: pickBy(v.interested, h), day: cursor });
    cursor -= 0.4;
    add({ direction: "outbound", channel: ch2, status: "sent", subject: null, body: pickBy(v.answer, h), day: cursor });
    cursor -= 0.6;
    add({ direction: "inbound", channel: ch2, status: "sent", subject: null, body: pickBy(v.question, h), day: cursor });
    cursor -= 0.3;
    add({ direction: "outbound", channel: ch2, status: "sent", subject: null, body: pickBy(v.schedule, h), day: cursor });
  }
}

interface Planned {
  state: string;
  replyPending: boolean;
}
function statesFor(k: number, mode: string): Planned[] {
  const out: Planned[] = [];
  const push = (state: string, frac: number, rp = false) => {
    for (let i = 0; i < Math.max(1, Math.round(k * frac)); i++) out.push({ state, replyPending: rp });
  };
  if (mode === "draft") return [];
  if (mode === "done") {
    push("handed_off", 0.4);
    push("opted_out", 0.32);
    push("awaiting_reply", 0.28);
  } else if (mode === "paused") {
    push("handed_off", 0.08);
    push("awaiting_reply", 0.12, true);
    push("opted_out", 0.08);
  } else {
    push("handed_off", 0.14);
    push("awaiting_reply", 0.14, true);
    push("awaiting_reply", 0.1);
    push("awaiting_approval", 0.16);
    push("scheduled", 0.08);
    push("opted_out", 0.08);
  }
  out.length = Math.min(out.length, k);
  while (out.length < k) out.push({ state: "proposed", replyPending: false });
  return out;
}

function assign(store: DemoStore, ws: DWorkspace, cam: DCampaign, contacts: DContact[], kind: Kind, rng: () => number) {
  const plan = statesFor(contacts.length, cam.status);
  if (plan.length === 0) return;
  const campaignAge = cam.status === "done" ? 70 : 18;
  contacts
    .map((ct, i) => ({ ct, score: Math.max(20, Math.min(99, 96 - i * 3 - Math.floor(rng() * 6))) }))
    .sort((a, b) => b.score - a.score)
    .forEach(({ ct, score }, idx) => {
      const { state, replyPending } = plan[idx];
      const startDay = (cam.status === "done" ? 60 : 14) - (idx % 10) * 0.8;
      const enr: DEnrollment = {
        id: id("enr"),
        workspace_id: ws.id,
        campaign_id: cam.id,
        contact_id: ct.id,
        state,
        score,
        score_rationale: `${score >= 80 ? "Strong" : score >= 60 ? "Solid" : "Partial"} match on ${ct.skills.slice(0, 2).join(" + ")}`,
        current_step: state === "proposed" ? 0 : state === "scheduled" ? 1 : 2,
        next_run_at: state === "scheduled" ? at(NOW + (1 + (hash(ct.id) % 3)) * D) : null,
        outcome: state === "handed_off" ? "interested" : state === "opted_out" ? "opted_out" : null,
        reply_pending: replyPending,
        last_read_at: null,
        updated_at: at(daysAgo(Math.max(0.2, startDay - 5))),
        created_at: at(daysAgo(campaignAge - idx * 0.3)),
      };
      store.enrollments.push(enr);
      buildThread(store, enr, cam, ct, kind, startDay);
    });
}

// ---- audit (generated from real activity) ----
const ACTORS = ["Avery Brooks", "Dana Okafor", "Riley Walsh"];
function generateAudit(store: DemoStore) {
  const events: DAudit[] = [];
  const actor = (seed: number) => ACTORS[seed % ACTORS.length];
  for (const m of store.messages) {
    if (m.status === "sent" && m.direction === "outbound") events.push({ id: id("aud"), action: "message.approved", summary: "Approved a drafted message", target_type: "message", target_id: m.id, actor_name: actor(hash(m.id)), workspace_id: m.workspace_id, created_at: m.sent_at ?? m.created_at });
    if (m.direction === "inbound") events.push({ id: id("aud"), action: "reply.received", summary: "Inbound reply received", target_type: "enrollment", target_id: m.enrollment_id, actor_name: null, workspace_id: m.workspace_id, created_at: m.created_at });
  }
  for (const e of store.enrollments) {
    if (e.state === "handed_off") events.push({ id: id("aud"), action: "enrollment.handed_off", summary: "Handed off a candidate", target_type: "enrollment", target_id: e.id, actor_name: actor(hash(e.id)), workspace_id: e.workspace_id, created_at: e.updated_at });
    if (e.state === "opted_out") events.push({ id: id("aud"), action: "enrollment.opted_out", summary: "Marked not interested", target_type: "enrollment", target_id: e.id, actor_name: actor(hash(e.id)), workspace_id: e.workspace_id, created_at: e.updated_at });
  }
  // a few operational events
  events.push(
    { id: id("aud"), action: "auth.login", summary: "Signed in", target_type: null, target_id: null, actor_name: "Avery Brooks", workspace_id: null, created_at: at(daysAgo(0.1)) },
    { id: id("aud"), action: "connection.connected", summary: "Connected Gmail seat", target_type: "connection", target_id: null, actor_name: "Avery Brooks", workspace_id: null, created_at: at(daysAgo(9)) },
    { id: id("aud"), action: "member.invited", summary: "Invited Riley Walsh", target_type: "user", target_id: null, actor_name: "Avery Brooks", workspace_id: null, created_at: at(daysAgo(20)) },
  );
  // keep the most recent ~80 to keep the feed snappy
  store.audit = events.sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, 80);
}

// ---- workspace assembly ----
interface CampSpec {
  name: string;
  status: string;
  autonomy_mode: string;
  criteria: Record<string, unknown>;
  steps: { channel: string; delay_days: number; subject?: string; body?: string }[];
}
function seedWorkspace(store: DemoStore, ws: DWorkspace, kind: Kind, specs: CampSpec[], contactCount: number, rng: () => number) {
  const contacts = makeContacts(ws.id, kind, contactCount, rng);
  store.contacts.push(...contacts);
  const fromEmail = kind === "eng" ? "recruiter@acme.com" : "gtm@acme.com";
  const cams: DCampaign[] = specs.map((s, i) => ({
    id: id("cmp"),
    workspace_id: ws.id,
    name: s.name,
    status: s.status,
    autonomy_mode: s.autonomy_mode,
    from_email: fromEmail,
    criteria: s.criteria,
    sequence: s.steps.map((st) => ({ channel: st.channel, delay_days: st.delay_days, subject: st.subject ?? "", body: st.body ?? "" })),
    created_at: at(daysAgo(s.status === "done" ? 90 : 21 - i * 2)),
  }));
  store.campaigns.push(...cams);

  // Primary active campaign covers the full pipeline over all contacts; other campaigns get slices.
  const primary = cams.find((c) => c.status === "active") ?? cams[0];
  assign(store, ws, primary, contacts, kind, rng);
  cams.forEach((c, i) => {
    if (c === primary) return;
    if (c.status === "draft") return; // drafts have no enrollments yet
    const slice = contacts.slice((i * 5) % contacts.length, ((i * 5) % contacts.length) + 7);
    assign(store, ws, c, slice, kind, rng);
  });
}

function build(): DemoStore {
  _id = 0;
  const rng = mulberry32(20260621);
  const org = { id: "org_demo", name: "Acme Talent" };
  const user = { id: "usr_demo", email: "demo@sourcewell.ai", name: "Avery Brooks" };

  const wsRecruit: DWorkspace = { id: "ws_recruit", name: "Recruiting", kind: "team", brand_voice: "Warm, specific, candidate-first.", settings: {} };
  const wsSales: DWorkspace = { id: "ws_sales", name: "Enterprise Sales", kind: "team", brand_voice: "Direct, value-first, no fluff.", settings: { autonomy_default: "auto" } };
  const wsPartner: DWorkspace = { id: "ws_partner", name: "Partnerships", kind: "team", brand_voice: "Collaborative and concrete.", settings: {} };

  const store: DemoStore = {
    org,
    user,
    isOrgAdmin: true,
    workspaces: [wsRecruit, wsSales, wsPartner],
    contacts: [],
    campaigns: [],
    enrollments: [],
    messages: [],
    connections: [
      { id: id("con"), provider: "gmail", status: "ok", seat_type: "email", user_email: "recruiter@acme.com", external_id: "recruiter@acme.com" },
      { id: id("con"), provider: "linkedin", status: "needs_reauth", seat_type: "recruiter", user_email: "demo@sourcewell.ai", external_id: null },
      { id: id("con"), provider: "linkedin", status: "ok", seat_type: "sales_nav", user_email: "riley@acme.demo", external_id: "riley-li" },
      { id: id("con"), provider: "graph", status: "ok", seat_type: "email", user_email: "dana@acme.demo", external_id: "dana@acme.demo" },
    ],
    members: [
      { id: user.id, name: "Avery Brooks", email: user.email, role: "org_admin", scope: "organization" },
      { id: id("usr"), name: "Dana Okafor", email: "dana@acme.demo", role: "member", scope: "organization" },
      { id: id("usr"), name: "Riley Walsh", email: "riley@acme.demo", role: "workspace_admin", scope: "workspace" },
      { id: id("usr"), name: "Sam Patel", email: "compliance@acme.demo", role: "compliance", scope: "organization" },
    ],
    audit: [],
    notificationsSeenAt: null,
    seq: 0,
  };

  seedWorkspace(store, wsRecruit, "eng", [
    { name: "Senior Backend Engineer", status: "active", autonomy_mode: "approve_each", criteria: { titles: ["Senior Backend Engineer", "Staff Engineer"], skills: ["Python", "Go"], locations: ["EU"] }, steps: [{ channel: "email", delay_days: 0, subject: "Quick question, {first}", body: "Saw your work at {company}." }, { channel: "linkedin", delay_days: 3, body: "Following up, {first}." }, { channel: "email", delay_days: 5, body: "Last nudge — happy to share the JD." }] },
    { name: "Data Platform Lead", status: "active", autonomy_mode: "approve_each", criteria: { titles: ["Data Platform Engineer"], skills: ["Spark", "dbt"] }, steps: [{ channel: "email", delay_days: 0, subject: "Data platform role" }, { channel: "linkedin", delay_days: 3 }] },
    { name: "Frontend Engineer — H1", status: "done", autonomy_mode: "approve_each", criteria: { titles: ["Frontend Engineer"], skills: ["React"] }, steps: [{ channel: "email", delay_days: 0 }] },
    { name: "ML Research Scientist", status: "draft", autonomy_mode: "approve_each", criteria: { titles: ["ML Engineer"], skills: ["PyTorch", "LLMs"] }, steps: [{ channel: "email", delay_days: 0 }] },
  ], 18, rng);

  seedWorkspace(store, wsSales, "sales", [
    { name: "Enterprise Outbound — Q3", status: "active", autonomy_mode: "approve_each", criteria: { titles: ["VP of Sales", "Chief Revenue Officer"], skills: ["Salesforce", "Enterprise"] }, steps: [{ channel: "email", delay_days: 0, subject: "Cutting {company}'s SDR ramp" }, { channel: "linkedin", delay_days: 2 }, { channel: "email", delay_days: 4 }] },
    { name: "RevOps Expansion", status: "active", autonomy_mode: "auto", criteria: { titles: ["Head of RevOps"], skills: ["HubSpot"] }, steps: [{ channel: "email", delay_days: 0 }, { channel: "linkedin", delay_days: 3 }] },
    { name: "Mid-Market Pilot", status: "done", autonomy_mode: "approve_each", criteria: { titles: ["Director of Demand Gen"] }, steps: [{ channel: "email", delay_days: 0 }] },
  ], 16, rng);

  seedWorkspace(store, wsPartner, "partner", [
    { name: "Agency Partner Program", status: "active", autonomy_mode: "approve_each", criteria: { titles: ["Head of Partnerships", "Director of Business Development"], skills: ["Channel"] }, steps: [{ channel: "email", delay_days: 0, subject: "{company} × us — partnership?" }, { channel: "linkedin", delay_days: 3 }, { channel: "email", delay_days: 6 }] },
    { name: "Integration Partners", status: "active", autonomy_mode: "approve_each", criteria: { titles: ["Ecosystem Lead", "VP Strategic Alliances"] }, steps: [{ channel: "email", delay_days: 0 }, { channel: "linkedin", delay_days: 4 }] },
    { name: "Reseller Outreach", status: "draft", autonomy_mode: "approve_each", criteria: { titles: ["Founder & CEO"] }, steps: [{ channel: "email", delay_days: 0 }] },
  ], 14, rng);

  generateAudit(store);
  return store;
}

export let store: DemoStore = build();
export function resetDemo() {
  store = build();
}
export const nextId = id;
export const isoAgo = (mins: number) => at(NOW - mins * 60_000);
