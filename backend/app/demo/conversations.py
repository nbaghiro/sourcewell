# ruff: noqa: E501, RUF001  (hand-authored prose: long lines + em dashes / bullets are intentional)
"""Hand-authored, realistic recruiter ↔ candidate conversations for the demo inbox.

These mirror how an agency recruiter actually works a candidate over email + LinkedIn: a targeted
cold intro, the candidate's real questions (comp, remote, team, tech, timing), the recruiter's
answers, scheduling, hand-off — plus opt-outs, referrals, and "ping me later". Channels are mixed
(some LinkedIn DMs, some email, one that starts on LinkedIn and moves to email).

`who`: recruiter | candidate | suggested  (suggested = an agent-drafted reply not yet sent)
`hours_ago`: when the message was sent, relative to the seed time (older = larger).
"""

from typing import TypedDict


class ConversationContact(TypedDict):
    name: str
    title: str
    company: str
    location: str
    email: str
    linkedin: str
    skills: list[str]
    score: int


class ConversationTurn(TypedDict, total=False):
    who: str
    channel: str
    subject: str
    body: str
    hours_ago: int


class Conversation(TypedDict, total=False):
    contact: ConversationContact
    state: str
    turns: list[ConversationTurn]
    suggested: str


CONVERSATIONS: list[Conversation] = [
    {
        "contact": {
            "name": "Ingrid Rossi",
            "title": "Senior Backend Engineer",
            "company": "Cyberdyne",
            "location": "Berlin, DE",
            "email": "ingrid.rossi@cyberdyne.com",
            "linkedin": "in/ingrid-rossi",
            "skills": ["go", "postgres", "event sourcing", "payments"],
            "score": 96,
        },
        "state": "handed_off",
        "turns": [
            {
                "who": "recruiter",
                "channel": "email",
                "subject": "Senior Backend Engineer @ Acme",
                "body": "Hi Ingrid,\n\nI came across your work on Cyberdyne's payments platform — the move to event-sourced ledgers caught my eye. I'm partnering with Acme (Series C fintech) to hire a Senior Backend Engineer for exactly that kind of problem: Go + Postgres, high-throughput ledgers.\n\nWould you be open to a quick 20-min chat this week to compare notes? No pressure either way.\n\nBest,\nAvery",
                "hours_ago": 96,
            },
            {
                "who": "candidate",
                "channel": "email",
                "body": "Hi Avery — nicely targeted :)\n\nI'm reasonably happy where I am, but always open to an interesting problem. Two quick questions: what's the comp range, and is it remote-friendly?",
                "hours_ago": 88,
            },
            {
                "who": "recruiter",
                "channel": "email",
                "subject": "re: Senior Backend Engineer @ Acme",
                "body": "Great questions:\n\n• Comp: €120–150k base + meaningful equity, depending on level\n• Remote: hybrid (2 days/wk in Berlin) or fully remote within the EU\n• Team: 8 engineers under Dana (ex-Stripe), shipping a new ledger core this half\n\nWould Thursday 3pm CET work for a 30-min intro with me, then a follow-up with Dana if it feels right?",
                "hours_ago": 80,
            },
            {
                "who": "candidate",
                "channel": "email",
                "body": "Thursday 3pm works. Could you send the JD beforehand so I come prepared?",
                "hours_ago": 72,
            },
            {
                "who": "recruiter",
                "channel": "email",
                "subject": "Invite + JD — Thu 3pm CET",
                "body": "Perfect — calendar invite + JD on the way. Looking forward to it, Ingrid!",
                "hours_ago": 70,
            },
        ],
    },
    {
        "contact": {
            "name": "Marcus Lee",
            "title": "Staff Software Engineer",
            "company": "Globex",
            "location": "Amsterdam, NL",
            "email": "marcus.lee@globex.com",
            "linkedin": "in/marcuslee",
            "skills": ["distributed systems", "go", "kafka"],
            "score": 91,
        },
        "state": "handed_off",
        "turns": [
            {
                "who": "recruiter",
                "channel": "linkedin",
                "body": "Hi Marcus — your KubeCon talk on idempotent event pipelines was excellent. I'm helping Acme build out their ledger team and your background is a near-perfect fit. Open to a quick chat?",
                "hours_ago": 60,
            },
            {
                "who": "candidate",
                "channel": "linkedin",
                "body": "Hey Avery — appreciate that! What's the team size and who would I report to?",
                "hours_ago": 52,
            },
            {
                "who": "recruiter",
                "channel": "linkedin",
                "body": "8 engineers under Dana (ex-Stripe). You'd own the streaming layer. Happy to send full details by email — what's the best address?",
                "hours_ago": 49,
            },
            {
                "who": "candidate",
                "channel": "email",
                "body": "marcus.lee@globex.com works. Mornings CET are best for a call.",
                "hours_ago": 40,
            },
            {
                "who": "recruiter",
                "channel": "email",
                "subject": "Acme · Staff Engineer — details + a few times",
                "body": "Sent over the JD and three morning slots this week. Grab whichever works and I'll confirm with Dana.",
                "hours_ago": 38,
            },
        ],
    },
    {
        "contact": {
            "name": "Sofia Wong",
            "title": "Platform Engineer",
            "company": "Initech",
            "location": "Remote (EU)",
            "email": "sofia.wong@initech.com",
            "linkedin": "in/sofiawong",
            "skills": ["kubernetes", "python", "aws"],
            "score": 84,
        },
        "state": "awaiting_reply",
        "turns": [
            {
                "who": "recruiter",
                "channel": "email",
                "subject": "Platform role at Acme",
                "body": "Hi Sofia,\n\nSaw your work at Initech on multi-region K8s — we're hiring a platform engineer at Acme to harden exactly that as they scale. Would a short chat be worth your time this week?",
                "hours_ago": 30,
            },
            {
                "who": "candidate",
                "channel": "email",
                "body": "Hi Avery, could be — what does the tech stack look like, and is the team shipping or still in build-out mode?",
                "hours_ago": 22,
            },
            {
                "who": "recruiter",
                "channel": "email",
                "subject": "re: Platform role at Acme",
                "body": "Stack is GKE + Terraform + a homegrown internal platform (Backstage-ish). Shipping daily — they're past 0→1 and into reliability/scale. Want me to set up 30 mins with the platform lead?",
                "hours_ago": 18,
            },
        ],
        "suggested": "Hi Sofia — just following up in case this slipped through. Would Tuesday or Wednesday afternoon CET work for a quick intro? Happy to keep it low-key.",
    },
    {
        "contact": {
            "name": "Aisha Berg",
            "title": "Backend Engineer",
            "company": "Hooli",
            "location": "London, UK",
            "email": "aisha.berg@hooli.com",
            "linkedin": "in/aishaberg",
            "skills": ["node", "postgres", "aws"],
            "score": 79,
        },
        "state": "awaiting_reply",
        "turns": [
            {
                "who": "recruiter",
                "channel": "email",
                "subject": "Backend role — Acme (fintech)",
                "body": "Hi Aisha,\n\nWe're hiring backend engineers at Acme and your fintech background stood out. Open to learning more?",
                "hours_ago": 26,
            },
            {
                "who": "candidate",
                "channel": "email",
                "body": "What does the compensation range look like? I'm not actively looking, so it would need to be a clear step up.",
                "hours_ago": 14,
            },
            {
                "who": "recruiter",
                "channel": "email",
                "subject": "re: Backend role — Acme (fintech)",
                "body": "Totally fair. Range is £95–120k + equity, and there's a clear senior track. If the numbers work, would you be open to a 20-min intro — no commitment?",
                "hours_ago": 10,
            },
        ],
        "suggested": "Hi Aisha — wanted to make sure the comp details landed. If a step up is there, I'd love to set up a quick call. Either way, thanks for being upfront!",
    },
    {
        "contact": {
            "name": "Priya Nair",
            "title": "Senior Platform Engineer",
            "company": "Umbrella",
            "location": "Berlin, DE",
            "email": "priya.nair@umbrella.io",
            "linkedin": "in/priyanair",
            "skills": ["kubernetes", "go", "observability"],
            "score": 88,
        },
        "state": "awaiting_reply",
        "turns": [
            {
                "who": "recruiter",
                "channel": "linkedin",
                "body": "Hi Priya — ex-bank platform experience + Berlin-based is exactly what Acme's looking for on their reliability team. Worth a quick chat?",
                "hours_ago": 44,
            },
            {
                "who": "candidate",
                "channel": "linkedin",
                "body": "Possibly! LinkedIn isn't great for me though — can you email me the specifics? priya.nair@umbrella.io",
                "hours_ago": 36,
            },
            {
                "who": "recruiter",
                "channel": "email",
                "subject": "Acme · Senior Platform Engineer (as requested)",
                "body": "Moved us to email as requested 🙂 Quick summary: senior platform role, GKE + Terraform, €130–160k + equity, Berlin hybrid. Full JD attached. Would a call next week suit?",
                "hours_ago": 30,
            },
        ],
    },
    {
        "contact": {
            "name": "Sam Chen",
            "title": "Staff Engineer",
            "company": "Vandelay",
            "location": "Dublin, IE",
            "email": "sam.chen@vandelay.com",
            "linkedin": "in/samchen",
            "skills": ["java", "spring", "kafka"],
            "score": 71,
        },
        "state": "opted_out",
        "turns": [
            {
                "who": "recruiter",
                "channel": "email",
                "subject": "Senior backend role — Acme",
                "body": "Hi Sam, we're hiring senior backend engineers at Acme — would you be open to a chat?",
                "hours_ago": 52,
            },
            {
                "who": "candidate",
                "channel": "email",
                "body": "Thanks, but I just started a new role and I'm not looking. Please remove me from your list.",
                "hours_ago": 48,
            },
            {
                "who": "recruiter",
                "channel": "email",
                "subject": "re: Senior backend role — Acme",
                "body": "Completely understand, Sam — congrats on the new role. You're removed, and I won't reach out again. All the best!",
                "hours_ago": 47,
            },
        ],
    },
    {
        "contact": {
            "name": "Diego Santos",
            "title": "Senior Backend Engineer",
            "company": "Soylent",
            "location": "Lisbon, PT",
            "email": "diego.santos@soylent.com",
            "linkedin": "in/diegosantos",
            "skills": ["go", "grpc", "postgres"],
            "score": 82,
        },
        "state": "awaiting_reply",
        "turns": [
            {
                "who": "recruiter",
                "channel": "email",
                "subject": "Acme — Senior Backend (ledgers)",
                "body": "Hi Diego, your gRPC + Go background is a strong match for a senior backend role at Acme. Open to a chat?",
                "hours_ago": 34,
            },
            {
                "who": "candidate",
                "channel": "email",
                "body": "Appreciate it — I'm actually mid-process with two other companies. What would make Acme different?",
                "hours_ago": 20,
            },
            {
                "who": "recruiter",
                "channel": "email",
                "subject": "re: Acme — Senior Backend (ledgers)",
                "body": "Fair to ask. Two things candidates tell us stand out: (1) you'd own the ledger core end-to-end, not a slice, and (2) a fast, transparent process — we can run start-to-offer in ~10 days to fit your timeline. Want me to set up a 20-min intro so you can compare directly?",
                "hours_ago": 12,
            },
        ],
    },
    {
        "contact": {
            "name": "Lena Park",
            "title": "Data Engineer",
            "company": "Aperture",
            "location": "Remote (EU)",
            "email": "lena.park@aperture.com",
            "linkedin": "in/lenapark",
            "skills": ["spark", "python", "airflow"],
            "score": 76,
        },
        "state": "awaiting_reply",
        "turns": [
            {
                "who": "recruiter",
                "channel": "linkedin",
                "body": "Hi Lena — Acme's building out a data platform team and your Spark/Airflow work is a great fit. Worth a quick chat?",
                "hours_ago": 58,
            },
            {
                "who": "candidate",
                "channel": "linkedin",
                "body": "Hi! Flattered, but I'm heads-down on a launch this quarter. Could you ping me again in Q3? Genuinely interested, just bad timing.",
                "hours_ago": 50,
            },
            {
                "who": "recruiter",
                "channel": "linkedin",
                "body": "Absolutely, Lena — I'll set a reminder for early Q3 and keep it warm. Good luck with the launch!",
                "hours_ago": 49,
            },
        ],
    },
]
