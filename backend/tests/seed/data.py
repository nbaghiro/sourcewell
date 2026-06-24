"""Demo data generation — three richly-populated verticals (recruiting, sales, partnerships).

Mirrors the client-side demo (frontend/src/lib/api/demo/data.ts) so the backend-seeded demo user
renders identically to the in-memory demo. `builder.py` turns these specs into ORM rows.
"""

# ruff: noqa: E501, RUF001

import random
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import TypedDict

from app.core.types import JsonList

LOCATIONS = [
    "Berlin, DE",
    "London, UK",
    "Amsterdam, NL",
    "Lisbon, PT",
    "Dublin, IE",
    "Remote · EU",
    "Munich, DE",
    "Paris, FR",
]


# ---- structured shapes for the demo specs (one definition per shape) ----


class RoleSpec(TypedDict):
    title: str
    skills: list[str]


class CompanySpec(TypedDict):
    name: str
    size: str
    industry: str


class Template(TypedDict, total=False):
    """A message template; openers/drafts carry a subject, followups omit it."""

    subject: str
    body: str


class ContentPools(TypedDict):
    openers: list[Template]
    followups: list[Template]
    drafts: list[Template]
    interested: list[str]
    question: list[str]
    answer: list[str]
    schedule: list[str]
    decline: list[str]
    notes: list[str]
    tags: list[str]
    sources: list[str]


class Vertical(TypedDict):
    first: list[str]
    last: list[str]
    roles: list[RoleSpec]
    companies: list[CompanySpec]
    content: ContentPools


class ContactDict(TypedDict):
    workspace_id: str
    full_name: str
    title: str
    company: str
    location: str
    email: str
    linkedin_url: str
    avatar_url: str
    skills: list[str]
    source: str
    company_size: str
    industry: str
    tags: list[str]
    notes: str | None


class SequenceStep(TypedDict, total=False):
    channel: str
    delay_days: int
    subject: str
    body: str


class MessageSpec(TypedDict):
    direction: str
    channel: str
    status: str
    subject: str | None
    body: str
    sent_at: datetime | None
    scheduled_at: datetime | None
    created_at: datetime


def _slug(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum())


def avatar_for(seed: str) -> str:
    """A deterministic stock headshot URL for demo data (real backend returns the real photo)."""
    return f"https://i.pravatar.cc/240?u={seed}"


def _stable_hash(s: str) -> int:
    h = 7
    for c in s:
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    return h


def _pick[T](arr: list[T], seed: int) -> T:
    return arr[seed % len(arr)]


def fill(t: str, contact: Mapping[str, object]) -> str:
    """Replace {first}/{company}/{title}/{skill} tokens against a contact dict."""
    raw_name = contact.get("full_name", "")
    name = raw_name if isinstance(raw_name, str) else ""
    first = name.split(" ")[0] if name else "there"
    company_v = contact.get("company")
    company = company_v if isinstance(company_v, str) and company_v else "your company"
    title_v = contact.get("title")
    title = title_v if isinstance(title_v, str) and title_v else "your role"
    skills_v = contact.get("skills")
    skills = skills_v if isinstance(skills_v, list) else []
    skill = str(skills[0]) if skills else "your work"
    return (
        (t or "")
        .replace("{first_name}", first)
        .replace("{first}", first)
        .replace("{company}", company)
        .replace("{title}", title)
        .replace("{skill}", skill)
    )


# ---- vertical configuration (personas, firmographics, content pools) ----

VERTICALS: dict[str, Vertical] = {
    "eng": {
        "first": [
            "Aisha",
            "Marcus",
            "Sofia",
            "Diego",
            "Lena",
            "Raj",
            "Mia",
            "Theo",
            "Hana",
            "Omar",
            "Clara",
            "Ravi",
            "Nadia",
            "Hugo",
            "Wei",
            "Bruno",
            "Elif",
            "Tomas",
        ],
        "last": [
            "Berg",
            "Lee",
            "Wong",
            "Santos",
            "Park",
            "Kumar",
            "Becker",
            "Ruiz",
            "Patel",
            "Novak",
            "Haddad",
            "Costa",
            "Mensah",
            "Ito",
            "Walsh",
            "Okafor",
            "Bauer",
            "Reyes",
        ],
        "roles": [
            {"title": "Senior Backend Engineer", "skills": ["Python", "Go", "Postgres"]},
            {"title": "Staff Engineer", "skills": ["Distributed Systems", "Kafka", "AWS"]},
            {"title": "Data Platform Engineer", "skills": ["Spark", "Airflow", "dbt"]},
            {"title": "ML Engineer", "skills": ["PyTorch", "LLMs", "Python"]},
            {"title": "Site Reliability Engineer", "skills": ["Kubernetes", "Terraform", "Go"]},
        ],
        "companies": [
            {"name": "Hooli", "size": "1,000-5,000", "industry": "Cloud Infrastructure"},
            {"name": "Globex", "size": "501-1,000", "industry": "Fintech"},
            {"name": "Initech", "size": "201-500", "industry": "Enterprise Software"},
            {"name": "Umbra", "size": "51-200", "industry": "Cybersecurity"},
            {"name": "Vandelay", "size": "501-1,000", "industry": "Logistics"},
            {"name": "Stark Labs", "size": "201-500", "industry": "Robotics"},
        ],
        "content": {
            "openers": [
                {
                    "subject": "Your work on {skill}, {first}",
                    "body": "Hi {first} — came across your background in {skill} at {company}. We're building out a {title} team and your profile stood out. Open to a quick chat?",
                },
                {
                    "subject": "{company} → us?",
                    "body": "Hey {first}, I know {company} does serious engineering. We've got a {title} role solving exactly the kind of {skill} problems you've worked on. Worth 15 minutes?",
                },
                {
                    "subject": "Quick question, {first}",
                    "body": "Hi {first}, your {skill} experience caught my eye. We're hiring a {title} — fully remote-friendly, strong team. Would you be open to hearing more?",
                },
            ],
            "followups": [
                {
                    "body": "Following up here, {first} — happy to share the JD and comp range up front so you can decide if it's worth a conversation."
                },
                {
                    "body": "Bumping this, {first}. No pressure at all — even a 'not now' helps me know whether to keep you in mind for later."
                },
            ],
            "drafts": [
                {
                    "subject": "The {title} role, {first}",
                    "body": "Hi {first}, wanted to share a bit more: it's a {title} role owning {skill} systems end-to-end. Comp is competitive + equity. Open to a call this week?",
                },
                {
                    "subject": "Re: {company}",
                    "body": "Hi {first} — circling back. The team's small and senior, and you'd have real ownership over the {skill} stack. Worth a quick intro?",
                },
            ],
            "interested": [
                "Thanks for reaching out! I'm not actively looking but I'm always open to a strong team. What's the comp range?",
                "Appreciate the note — the {skill} angle is interesting. Tell me a bit more about the team and how senior the role is.",
                "Hi — happy to chat. I've been at {company} a while so timing could be right. Is it remote?",
            ],
            "question": [
                "Interesting — is this fully remote, and what's the team size?",
                "What does the comp range look like, and is there meaningful equity?",
                "Curious what the {skill} stack looks like day to day before I commit to a call.",
            ],
            "answer": [
                "Great questions! It's fully remote within the EU, ~8 engineers, and comp is €120-150k + equity depending on level. Want to grab 20 minutes this week?",
                "Happy to share — strong equity, small senior team, and you'd own the {skill} roadmap. Does Thursday at 3pm work for a quick intro?",
            ],
            "schedule": [
                "Perfect — does Thursday at 3pm CET work for a 20-minute intro with the hiring manager?",
                "Great — I'll set up a quick call. Would Wednesday morning or Friday afternoon suit you better?",
            ],
            "decline": [
                "Appreciate it, but I just signed for another year at {company} — not looking right now.",
                "Thanks for thinking of me! Not the right time, but feel free to reach back out in 6 months.",
            ],
            "notes": [
                "Passive candidate — strong on {skill}. Referred via a current teammate.",
                "Opened the last two emails multiple times; warm. Mentioned timing might be right.",
                "Top of the shortlist for the {title} req. Senior, low-flight-risk profile.",
                "Replied quickly and engaged — moving toward an intro call.",
            ],
            "tags": ["passive", "strong match", "responsive", "senior", "warm intro", "shortlist"],
            "sources": ["linkedin", "github", "referral", "inbound", "ats"],
        },
    },
    "sales": {
        "first": [
            "Priya",
            "Daniel",
            "Grace",
            "Mateo",
            "Yuki",
            "Noah",
            "Elena",
            "Kofi",
            "Maya",
            "Pavel",
            "Ingrid",
            "Tom",
            "Sara",
            "Luca",
        ],
        "last": [
            "Raman",
            "Foster",
            "Bauer",
            "Silva",
            "Tanaka",
            "Moreau",
            "Holt",
            "Ferraro",
            "Adeyemi",
            "Khan",
            "Doe",
            "Ruiz",
            "Klein",
            "Marsh",
        ],
        "roles": [
            {"title": "VP of Sales", "skills": ["Salesforce", "Outbound", "Enterprise"]},
            {"title": "Head of RevOps", "skills": ["HubSpot", "Forecasting", "Analytics"]},
            {"title": "Director of Demand Gen", "skills": ["ABM", "Marketo", "Paid"]},
            {"title": "Chief Revenue Officer", "skills": ["Pipeline", "Enterprise", "GTM"]},
            {"title": "Sales Operations Lead", "skills": ["Salesforce", "Enablement", "Territory"]},
        ],
        "companies": [
            {"name": "Northwind", "size": "501-1,000", "industry": "B2B SaaS"},
            {"name": "Contoso", "size": "1,000-5,000", "industry": "MarTech"},
            {"name": "Brightwave", "size": "201-500", "industry": "FinTech"},
            {"name": "Acme Cloud", "size": "5,000+", "industry": "Cloud Infrastructure"},
            {"name": "Lumen", "size": "51-200", "industry": "DevTools"},
            {"name": "Cobalt", "size": "201-500", "industry": "Security"},
        ],
        "content": {
            "openers": [
                {
                    "subject": "Cutting {company}'s SDR ramp time",
                    "body": "Hi {first}, saw {company} is scaling outbound. We help RevOps teams automate the top of funnel without adding headcount. Worth a quick look?",
                },
                {
                    "subject": "{company} + 3x more replies",
                    "body": "Hey {first} — teams like yours are seeing 3x reply rates by letting AI agents run multichannel outbound with a human approving each send. Open to a 20-min walkthrough?",
                },
                {
                    "subject": "A question for the {title}",
                    "body": "Hi {first}, quick one — how is {company} currently handling outbound at scale? We've helped similar teams book 40% more meetings.",
                },
            ],
            "followups": [
                {
                    "body": "Following up, {first} — happy to send a 2-minute Loom showing how it'd work for a team your size before we ever talk."
                },
                {
                    "body": "Circling back. If outbound isn't a priority this quarter no worries — just let me know and I'll close the loop."
                },
            ],
            "drafts": [
                {
                    "subject": "ROI for {company}",
                    "body": "Hi {first}, put together a quick ROI estimate for a team your size — happy to walk through it. Does this week work?",
                },
                {
                    "subject": "Re: {company} outbound",
                    "body": "Hi {first} — circling back with a quick breakdown of how we'd cut your SDR ramp. Worth 20 minutes?",
                },
            ],
            "interested": [
                "We are indeed scaling the team. How does this compare to Outreach?",
                "Timely — we're re-evaluating our outbound stack this quarter. What does pricing look like?",
                "Interesting. We've struggled with reply rates. Can you show me real numbers from a comparable team?",
            ],
            "question": [
                "Curious about pricing and whether it integrates with Salesforce?",
                "Does this replace our SDRs or augment them? And how's deliverability handled?",
                "What's the ramp time to see results, and do you support multichannel (email + LinkedIn)?",
            ],
            "answer": [
                "Great questions — it augments your SDRs (they approve every send), integrates natively with Salesforce, and most teams see lift in ~2 weeks. Want to see it on your data Wednesday?",
                "It's multichannel out of the box (email + LinkedIn), Salesforce-native, and priced per seat. Happy to run a quick walkthrough — does Thursday work?",
            ],
            "schedule": [
                "Perfect — would Wednesday at 11am work for a 20-minute walkthrough with your RevOps lead?",
                "Great — I'll send an invite. Does Thursday afternoon or Friday morning suit you better?",
            ],
            "decline": [
                "Not a priority this quarter — we just renewed our current tool. Worth a look in Q4.",
                "Appreciate it, but budget's frozen until next fiscal. Reach back out in January?",
            ],
            "notes": [
                "Champion — owns the outbound number at {company}. Re-evaluating their stack now.",
                "Inbound from our webinar; high intent. Asked about Salesforce integration.",
                "Decision-maker for a {company} expansion deal. Budget holder confirmed.",
                "Engaged on LinkedIn before replying — warm. Mentioned a Q3 initiative.",
            ],
            "tags": [
                "champion",
                "decision-maker",
                "evaluating",
                "budget holder",
                "inbound",
                "expansion",
            ],
            "sources": ["apollo", "zoominfo", "salesforce", "linkedin", "inbound", "referral"],
        },
    },
    "partner": {
        "first": [
            "Anika",
            "Sven",
            "Rosa",
            "Idris",
            "Mei",
            "Felix",
            "Zara",
            "Jonas",
            "Amara",
            "Petra",
            "Caleb",
            "Nina",
        ],
        "last": [
            "Voss",
            "Lindqvist",
            "Mwangi",
            "Hassan",
            "Chen",
            "Albrecht",
            "Okonkwo",
            "Brandt",
            "Diallo",
            "Novik",
            "Fischer",
            "Sato",
        ],
        "roles": [
            {"title": "Head of Partnerships", "skills": ["Channel", "Alliances", "GTM"]},
            {"title": "Founder & CEO", "skills": ["Strategy", "Fundraising", "Product"]},
            {
                "title": "Director of Business Development",
                "skills": ["BD", "Integrations", "Ecosystem"],
            },
            {"title": "VP Strategic Alliances", "skills": ["Co-sell", "Enterprise", "Channel"]},
            {"title": "Ecosystem Lead", "skills": ["Marketplace", "Integrations", "Developer Rel"]},
        ],
        "companies": [
            {"name": "Foundry Labs", "size": "11-50", "industry": "Dev Platform"},
            {"name": "Meridian", "size": "51-200", "industry": "Systems Integrator"},
            {"name": "Halcyon", "size": "201-500", "industry": "Cloud Consulting"},
            {"name": "Polaris Digital", "size": "11-50", "industry": "Agency"},
            {"name": "Keystone", "size": "501-1,000", "industry": "Platform"},
            {"name": "Arcadia", "size": "51-200", "industry": "Data Services"},
        ],
        "content": {
            "openers": [
                {
                    "subject": "{company} × us — partnership?",
                    "body": "Hi {first}, I lead partnerships and think {company} could be a great fit for our program — your {skill} focus overlaps nicely with our customers. Worth exploring?",
                },
                {
                    "subject": "An integration idea for {company}",
                    "body": "Hey {first} — our mutual customers keep asking for a {company} integration. Would love to scope a co-sell or marketplace listing. Open to a chat?",
                },
                {
                    "subject": "Co-marketing with {company}?",
                    "body": "Hi {first}, we're expanding our partner ecosystem and {company} is top of my list. Could we find 20 minutes?",
                },
            ],
            "followups": [
                {
                    "body": "Following up, {first} — I can send our partner one-pager and a couple of example co-sell wins if that's useful."
                },
                {
                    "body": "Circling back. Even a quick intro to the right person on your side would be a great start."
                },
            ],
            "drafts": [
                {
                    "subject": "Partner program — {company}",
                    "body": "Hi {first}, sharing our partner tiers and the co-marketing support we offer. Worth a quick scoping call?",
                },
                {
                    "subject": "Re: {company} integration",
                    "body": "Hi {first} — following up on the integration idea. Happy to loop in our solutions team. Does this week work?",
                },
            ],
            "interested": [
                "We've been looking to expand our integrations — this could be timely. What does the program look like?",
                "Interesting. Our customers do ask about this. How does co-sell revenue share work?",
                "Open to it. We're picky about partners though — what kind of joint GTM support do you offer?",
            ],
            "question": [
                "What's the revenue share / referral model, and is there a marketplace listing?",
                "Do you offer co-marketing budget, and what's expected of us technically?",
                "How many joint customers do you have today, and who'd own the relationship?",
            ],
            "answer": [
                "Great — we do 20% referral + co-marketing budget, a marketplace listing, and a dedicated partner manager. Want to scope it Wednesday?",
                "Co-sell with revenue share, joint webinars, and lightweight tech lift on your side. Happy to walk through tiers — does Thursday work?",
            ],
            "schedule": [
                "Perfect — would Wednesday at 2pm work to scope the partnership with our alliances lead?",
                "Great — I'll set up a partner scoping call. Does early next week suit you?",
            ],
            "decline": [
                "Appreciate it — we're heads-down on product this quarter and pausing new partnerships.",
                "Thanks, but our partner roadmap is full for now. Let's reconnect next quarter.",
            ],
            "notes": [
                "Warm — mutual customers requesting the integration. Strong ecosystem fit.",
                "Founder-led; fast mover. Interested in co-marketing.",
                "Strategic alliance potential. Owns the partner roadmap at {company}.",
                "Engaged after the follow-up; wants to scope co-sell.",
            ],
            "tags": ["strategic", "co-sell", "integration", "warm", "founder", "ecosystem fit"],
            "sources": ["referral", "inbound", "linkedin", "event", "marketplace"],
        },
    },
}


def make_contacts(workspace_id: str, kind: str, n: int, *, rng: random.Random) -> list[ContactDict]:
    """Generate `n` enriched contact kwargs for a vertical (unique names)."""
    v = VERTICALS[kind]
    combos = [(f, last) for f in v["first"] for last in v["last"]]
    rng.shuffle(combos)
    out: list[ContactDict] = []
    for first, last in combos[:n]:
        role = rng.choice(v["roles"])
        firm = rng.choice(v["companies"])
        email = f"{first.lower()}.{last.lower()}@{_slug(firm['name'])}.com"
        h = _stable_hash(first + last)
        pool = v["content"]["tags"]
        tag_n = 1 + (h % 3)
        tags = list(dict.fromkeys(pool[(h + i * 2) % len(pool)] for i in range(tag_n)))
        contact: ContactDict = {
            "workspace_id": workspace_id,
            "full_name": f"{first} {last}",
            "title": role["title"],
            "company": firm["name"],
            "location": LOCATIONS[h % len(LOCATIONS)],
            "email": email,
            "linkedin_url": f"https://linkedin.com/in/{first.lower()}{last.lower()}",
            "avatar_url": avatar_for(email),
            "skills": list(role["skills"]),
            "source": _pick(v["content"]["sources"], h),
            "company_size": firm["size"],
            "industry": firm["industry"],
            "tags": tags,
            "notes": None,
        }
        contact["notes"] = fill(_pick(v["content"]["notes"], h), contact)
        out.append(contact)
    return out


def states_for(k: int, mode: str) -> list[tuple[str, bool]]:
    """Proportional state spread (top scorers get the most-advanced states)."""
    out: list[tuple[str, bool]] = []

    def push(state: str, frac: float, reply_pending: bool = False) -> None:
        for _ in range(max(1, round(k * frac))):
            out.append((state, reply_pending))

    if mode == "draft":
        return []
    if mode == "done":
        push("handed_off", 0.4)
        push("opted_out", 0.32)
        push("awaiting_reply", 0.28)
    elif mode == "paused":
        push("handed_off", 0.08)
        push("awaiting_reply", 0.12, True)
        push("opted_out", 0.08)
    else:
        push("handed_off", 0.14)
        push("awaiting_reply", 0.14, True)
        push("awaiting_reply", 0.1)
        push("awaiting_approval", 0.16)
        push("scheduled", 0.08)
        push("opted_out", 0.08)
    out = out[:k]
    while len(out) < k:
        out.append(("proposed", False))
    return out


def build_messages(
    *,
    state: str,
    reply_pending: bool,
    sequence: JsonList,
    contact: Mapping[str, object],
    kind: str,
    start_day: float,
    now: datetime,
) -> list[MessageSpec]:
    """Deep, multi-channel, realistically-timed message specs for one enrollment."""
    v = VERTICALS[kind]["content"]
    full_name = contact["full_name"]
    h = _stable_hash(full_name if isinstance(full_name, str) else "")
    ch1_v = sequence[0]["channel"] if sequence else "email"
    ch1 = ch1_v if isinstance(ch1_v, str) else "email"
    ch2_v = sequence[1]["channel"] if len(sequence) > 1 else "linkedin"
    ch2 = ch2_v if isinstance(ch2_v, str) else "linkedin"
    msgs: list[MessageSpec] = []

    def add(
        direction: str,
        channel: str,
        status: str,
        subject: str | None,
        body: str,
        day: float,
        scheduled: float | None = None,
    ) -> None:
        ts = now - timedelta(days=day)
        msgs.append(
            {
                "direction": direction,
                "channel": channel,
                "status": status,
                "subject": subject,
                "body": fill(body, contact),
                "sent_at": ts if status == "sent" else None,
                "scheduled_at": (now + timedelta(days=scheduled))
                if scheduled is not None
                else None,
                "created_at": ts,
            }
        )

    opener = _pick(v["openers"], h)
    if state == "proposed":
        return []
    if state == "scheduled":
        add(
            "outbound",
            ch1,
            "draft",
            fill(opener["subject"], contact) if ch1 == "email" else None,
            opener["body"],
            0,
            scheduled=1 + (h % 3),
        )
        return msgs
    if state == "awaiting_approval":
        d = _pick(v["drafts"], h)
        add(
            "outbound",
            ch1,
            "draft",
            fill(d["subject"], contact) if ch1 == "email" else None,
            d["body"],
            start_day,
        )
        return msgs

    cursor = start_day
    add(
        "outbound",
        ch1,
        "sent",
        fill(opener["subject"], contact) if ch1 == "email" else None,
        opener["body"],
        cursor,
    )
    cursor -= 3 + (h % 3)
    add("outbound", ch2, "sent", None, _pick(v["followups"], h)["body"], cursor)

    if state == "awaiting_reply" and not reply_pending:
        return msgs

    cursor -= 1 + (h % 2)
    if state == "awaiting_reply" and reply_pending:
        add("inbound", ch2, "received", None, _pick(v["question"], h), cursor)
        return msgs
    if state == "opted_out":
        add("inbound", ch2, "received", None, _pick(v["decline"], h), cursor)
        return msgs
    if state == "handed_off":
        add("inbound", ch2, "received", None, _pick(v["interested"], h), cursor)
        cursor -= 0.4
        add("outbound", ch2, "sent", None, _pick(v["answer"], h), cursor)
        cursor -= 0.6
        add("inbound", ch2, "received", None, _pick(v["question"], h), cursor)
        cursor -= 0.3
        add("outbound", ch2, "sent", None, _pick(v["schedule"], h), cursor)
    return msgs
