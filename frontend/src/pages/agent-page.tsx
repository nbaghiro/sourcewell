import {
    Ban,
    Bot,
    CheckCircle2,
    ChevronRight,
    Clock,
    Flag,
    Loader2,
    MailOpen,
    PenLine,
    Reply,
    Send,
    Sparkles,
    Target,
    UserCheck,
} from "lucide-react";
import * as React from "react";
import { Link } from "react-router-dom";

import { PageHeader } from "@/components/page-header";
import { PageLayout } from "@/components/page-layout";
import { StateBadge } from "@/components/state-badge";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Segmented } from "@/components/ui/segmented";
import { Skeleton } from "@/components/ui/skeleton";
import { useAgentActivity, useAgentChat, useAgentState } from "@/lib/api/queries";
import { initials, longAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

// ---- runtime shapes (the read-model is loosely typed in the schema; these are authoritative) ----

interface Ref {
    id: string;
    name: string;
    sub?: string | null;
    avatar?: string | null;
}

interface ActivityEvent {
    id: string;
    ts: string;
    kind: string;
    title: string;
    detail?: string | null;
    rationale?: string | null;
    contact?: Ref | null;
    campaign?: { id: string; name: string } | null;
}

interface Channel {
    cap: number;
    sent: number;
    blocked: boolean;
}

interface AgentState {
    status: "active" | "idle" | string;
    counts: Record<string, number>;
    today: { sent: number; replies: number; handed_off: number };
    needs_you: { approvals: number; hot_replies: number };
    governor: { email: Channel; linkedin: Channel };
    campaigns: { id: string; name: string; status: string; active: number }[];
}

type Variant = "feed" | "mission" | "briefing" | "chat";

const VARIANTS = [
    { value: "feed", label: "Activity feed" },
    { value: "mission", label: "Mission control" },
    { value: "briefing", label: "Daily briefing" },
    { value: "chat", label: "Copilot chat" },
];

export function AgentPage() {
    const [variant, setVariant] = React.useState<Variant>("feed");

    return (
        <PageLayout width="wide">
            <PageHeader
                eyebrow="Your agent"
                title="Wren"
                description="Your sourcing agent — drafting, sending, and watching for replies within your guardrails."
            />

            {/* TEMPORARY: lets us compare the four experiences side by side before keeping one. */}
            <div className="flex items-center justify-end gap-3">
                <span className="font-mono text-[0.65rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                    Compare experience
                </span>
                <Segmented
                    value={variant}
                    onChange={(v) => setVariant(v as Variant)}
                    options={VARIANTS}
                />
            </div>

            {variant === "feed" && <ActivityFeed />}
            {variant === "mission" && <MissionControl />}
            {variant === "briefing" && <DailyBriefing />}
            {variant === "chat" && <CopilotChat />}
        </PageLayout>
    );
}

// ---------------------------------------------------------------------------
// kind → icon + color
// ---------------------------------------------------------------------------

const KIND_META: Record<
    string,
    { Icon: typeof Target; tone: "emerald" | "blue" | "muted" | "amber"; highlight?: boolean }
> = {
    sourced: { Icon: Target, tone: "emerald" },
    drafted: { Icon: PenLine, tone: "blue" },
    scheduled: { Icon: Clock, tone: "amber" },
    sent: { Icon: Send, tone: "blue" },
    reply: { Icon: MailOpen, tone: "emerald", highlight: true },
    handed_off: { Icon: UserCheck, tone: "emerald" },
    opted_out: { Icon: Ban, tone: "muted" },
    completed: { Icon: Flag, tone: "muted" },
};

const TONE_CLASSES: Record<string, string> = {
    emerald: "bg-accent text-[var(--accent-foreground)]",
    blue: "bg-secondary text-secondary-foreground",
    amber: "bg-[color-mix(in_srgb,var(--warning)_16%,white)] text-[var(--warning)]",
    muted: "bg-muted text-muted-foreground",
};

function kindMeta(kind: string) {
    return KIND_META[kind] ?? { Icon: Sparkles, tone: "muted" as const };
}

// ---------------------------------------------------------------------------
// 1 · Activity Feed
// ---------------------------------------------------------------------------

function ActivityFeed() {
    const { data, isLoading } = useAgentActivity();
    const events = (data as ActivityEvent[] | undefined) ?? [];

    return (
        <Card>
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Bot className="size-4 text-primary" />
                    Agent activity
                </CardTitle>
                <span className="flex items-center gap-1.5 font-mono text-[0.65rem] font-semibold uppercase tracking-wide text-success">
                    <span className="size-1.5 animate-pulse rounded-full bg-success" aria-hidden />
                    live
                </span>
            </CardHeader>
            <CardContent>
                {isLoading ? (
                    <div className="space-y-5">
                        {[0, 1, 2, 3].map((i) => (
                            <div key={i} className="flex gap-3">
                                <Skeleton className="size-8 shrink-0 rounded-full" />
                                <div className="flex-1 space-y-2">
                                    <Skeleton className="h-4 w-1/2" />
                                    <Skeleton className="h-3 w-3/4" />
                                </div>
                            </div>
                        ))}
                    </div>
                ) : events.length === 0 ? (
                    <div className="py-12 text-center">
                        <Bot className="mx-auto size-7 text-muted-foreground" />
                        <p className="mt-2 text-sm text-muted-foreground">
                            Nothing yet — Wren will start posting here as it sources and reaches out.
                        </p>
                    </div>
                ) : (
                    <ol className="relative ml-4 space-y-6 border-l border-border pl-6">
                        {events.map((e) => (
                            <FeedRow key={e.id} event={e} />
                        ))}
                    </ol>
                )}
            </CardContent>
        </Card>
    );
}

function FeedRow({ event }: { event: ActivityEvent }) {
    const { Icon, tone, highlight } = kindMeta(event.kind);
    return (
        <li className="relative">
            {/* node on the timeline */}
            <span
                className={cn(
                    "absolute -left-[2.35rem] grid size-7 place-items-center rounded-full ring-4 ring-card",
                    TONE_CLASSES[tone],
                )}
            >
                <Icon className="size-3.5" />
            </span>
            <div
                className={cn(
                    "flex items-start justify-between gap-3 rounded-lg",
                    highlight &&
                        "-mx-2 -my-1 bg-accent/50 px-2 py-1 ring-1 ring-[var(--accent)]",
                )}
            >
                <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                        <span className="font-semibold text-foreground">{event.title}</span>
                        {event.campaign && (
                            <Badge variant="outline" className="font-normal">
                                {event.campaign.name}
                            </Badge>
                        )}
                    </div>
                    {event.detail && (
                        <p className="mt-0.5 truncate text-sm text-muted-foreground">
                            {event.detail}
                        </p>
                    )}
                    {event.kind === "sourced" && event.rationale && (
                        <p className="mt-0.5 text-xs text-muted-foreground">
                            <span className="font-medium">why:</span> {event.rationale}
                        </p>
                    )}
                    {event.contact && (
                        <div className="mt-2 flex items-center gap-2">
                            <Avatar className="size-5 rounded-full">
                                {event.contact.avatar && (
                                    <AvatarImage
                                        src={event.contact.avatar}
                                        alt={event.contact.name}
                                    />
                                )}
                                <AvatarFallback className="text-[0.6rem]">
                                    {initials(event.contact.name)}
                                </AvatarFallback>
                            </Avatar>
                            <span className="text-xs text-muted-foreground">
                                {event.contact.name}
                            </span>
                        </div>
                    )}
                </div>
                <time className="shrink-0 whitespace-nowrap font-mono text-xs text-muted-foreground">
                    {longAgo(event.ts)}
                </time>
            </div>
        </li>
    );
}

// ---------------------------------------------------------------------------
// 2 · Mission Control
// ---------------------------------------------------------------------------

function MissionControl() {
    const { data, isLoading } = useAgentState();
    const state = data as AgentState | undefined;

    if (isLoading || !state) {
        return (
            <div className="space-y-4">
                <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                    {[0, 1, 2, 3].map((i) => (
                        <Skeleton key={i} className="h-24" />
                    ))}
                </div>
                <Skeleton className="h-16" />
                <Skeleton className="h-28" />
            </div>
        );
    }

    const pipeline = [
        { label: "Sourcing", value: state.counts.proposed ?? 0 },
        { label: "Drafting", value: state.counts.awaiting_approval ?? 0 },
        { label: "Scheduled", value: state.counts.scheduled ?? 0 },
        { label: "Awaiting reply", value: state.counts.awaiting_reply ?? 0 },
    ];

    return (
        <div className="space-y-4">
            <div className="flex items-center justify-between">
                <span className="flex items-center gap-2 font-mono text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                    Agent
                    <StateBadge state={state.status} />
                </span>
                <span className="font-mono text-[0.65rem] uppercase tracking-wide text-muted-foreground">
                    autonomy per campaign
                </span>
            </div>

            {/* pipeline stat cards */}
            <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
                {pipeline.map((p) => (
                    <Card key={p.label} className="p-4">
                        <p className="font-mono text-[0.65rem] uppercase tracking-wide text-muted-foreground">
                            {p.label}
                        </p>
                        <p className="mt-1 font-display text-3xl font-bold tabular-nums tracking-tight text-foreground">
                            {p.value}
                        </p>
                    </Card>
                ))}
            </div>

            {/* today strip */}
            <Card className="p-4">
                <div className="flex flex-wrap items-center gap-x-6 gap-y-2">
                    <span className="font-mono text-[0.65rem] uppercase tracking-wide text-muted-foreground">
                        today
                    </span>
                    <Stat n={state.today.sent} label="sent" />
                    <span className="text-muted-foreground">·</span>
                    <Stat n={state.today.replies} label="replies" />
                    <span className="text-muted-foreground">·</span>
                    <Stat n={state.today.handed_off} label="handed off" />
                </div>
            </Card>

            {/* governor headroom */}
            <Card>
                <CardHeader>
                    <CardTitle>Governor headroom</CardTitle>
                </CardHeader>
                <CardContent className="space-y-4">
                    <GovernorBar label="Email" channel={state.governor.email} />
                    <GovernorBar label="LinkedIn" channel={state.governor.linkedin} />
                </CardContent>
            </Card>

            {/* needs you callout */}
            <NeedsYouCallout needs={state.needs_you} />

            {/* campaigns mini-list */}
            <Card>
                <CardHeader>
                    <CardTitle>Campaigns</CardTitle>
                    <span className="text-xs text-muted-foreground">{state.campaigns.length}</span>
                </CardHeader>
                <CardContent className="space-y-1">
                    {state.campaigns.length === 0 ? (
                        <p className="text-sm text-muted-foreground">No active campaigns.</p>
                    ) : (
                        state.campaigns.map((c) => (
                            <Link
                                key={c.id}
                                to={`/campaigns/${c.id}`}
                                className="flex items-center justify-between gap-3 rounded-lg px-2 py-2 transition-colors hover:bg-secondary/40"
                            >
                                <span className="truncate text-sm font-medium text-foreground">
                                    {c.name}
                                </span>
                                <span className="flex shrink-0 items-center gap-3">
                                    <StateBadge state={c.status} />
                                    <span className="font-mono text-xs tabular-nums text-muted-foreground">
                                        {c.active} in sequence
                                    </span>
                                </span>
                            </Link>
                        ))
                    )}
                </CardContent>
            </Card>
        </div>
    );
}

function Stat({ n, label }: { n: number; label: string }) {
    return (
        <span className="text-sm">
            <span className="font-mono text-base font-bold tabular-nums text-foreground">{n}</span>{" "}
            <span className="text-muted-foreground">{label}</span>
        </span>
    );
}

function GovernorBar({ label, channel }: { label: string; channel: Channel }) {
    const pct = channel.cap > 0 ? Math.min(100, (channel.sent / channel.cap) * 100) : 0;
    return (
        <div>
            <div className="mb-1.5 flex items-center justify-between text-sm">
                <span className="flex items-center gap-1.5 font-medium text-foreground">
                    {channel.blocked && <span className="text-destructive">⚠</span>}
                    {label}
                </span>
                <span
                    className={cn(
                        "font-mono text-xs tabular-nums",
                        channel.blocked ? "text-destructive" : "text-muted-foreground",
                    )}
                >
                    {channel.sent}/{channel.cap}
                </span>
            </div>
            <div
                className={cn(
                    "h-2 w-full overflow-hidden rounded-full",
                    channel.blocked
                        ? "bg-[color-mix(in_srgb,var(--destructive)_18%,white)]"
                        : "bg-secondary",
                )}
            >
                <div
                    className={cn(
                        "h-full rounded-full transition-all",
                        channel.blocked ? "bg-destructive" : "bg-primary",
                    )}
                    style={{ width: `${pct}%` }}
                />
            </div>
        </div>
    );
}

function NeedsYouCallout({ needs }: { needs: AgentState["needs_you"] }) {
    const total = needs.approvals + needs.hot_replies;
    if (total === 0) {
        return (
            <Card className="flex items-center gap-3 border-success/40 bg-[color-mix(in_srgb,var(--success)_8%,white)] p-4">
                <CheckCircle2 className="size-5 text-success" />
                <span className="text-sm font-medium text-foreground">
                    Nothing needs you right now — Wren has it handled.
                </span>
            </Card>
        );
    }
    return (
        <Card className="flex flex-wrap items-center gap-x-4 gap-y-2 border-[var(--warning)]/40 bg-[color-mix(in_srgb,var(--warning)_8%,white)] p-4">
            <span className="flex items-center gap-1.5 font-semibold text-[var(--warning)]">
                ⚠ needs you
            </span>
            <Link
                to="/approvals"
                className="text-sm font-medium text-foreground underline-offset-2 hover:underline"
            >
                {needs.approvals} approvals
            </Link>
            <span className="text-muted-foreground">·</span>
            <Link
                to="/inbox"
                className="text-sm font-medium text-foreground underline-offset-2 hover:underline"
            >
                {needs.hot_replies} replies
            </Link>
        </Card>
    );
}

// ---------------------------------------------------------------------------
// 3 · Daily Briefing
// ---------------------------------------------------------------------------

function DailyBriefing() {
    const { data, isLoading } = useAgentState();
    const state = data as AgentState | undefined;

    if (isLoading || !state) {
        return (
            <div className="mx-auto max-w-2xl space-y-4 py-4">
                <Skeleton className="h-40" />
                <Skeleton className="h-32" />
            </div>
        );
    }

    const { approvals, hot_replies } = state.needs_you;
    const allCaughtUp = approvals + hot_replies === 0;
    const reviewHref = approvals > 0 ? "/approvals" : "/inbox";

    return (
        <div className="mx-auto max-w-2xl space-y-6 py-4">
            {/* hero */}
            <Card className="bg-gradient-to-br from-score-from to-score-to p-8 text-center text-primary-foreground">
                <div className="mx-auto mb-3 grid size-12 place-items-center rounded-xl bg-white/15">
                    <Bot className="size-6" />
                </div>
                <h2 className="font-display text-2xl font-bold tracking-tight">
                    Here's today with Wren
                </h2>
                <p className="mx-auto mt-2 max-w-md text-sm text-primary-foreground/85">
                    It ran {state.today.sent} sends and sorted {state.today.replies} replies on its
                    own.
                </p>
            </Card>

            {/* needs you */}
            <div>
                <p className="mb-3 font-mono text-[0.65rem] font-semibold uppercase tracking-[0.12em] text-muted-foreground">
                    Needs you
                </p>
                {allCaughtUp ? (
                    <Card className="flex items-center justify-center gap-2 p-8 text-center">
                        <CheckCircle2 className="size-5 text-success" />
                        <span className="font-medium text-foreground">All caught up ✓</span>
                    </Card>
                ) : (
                    <div className="space-y-2.5">
                        {approvals > 0 && (
                            <BriefingRow
                                to="/approvals"
                                Icon={PenLine}
                                count={approvals}
                                label={`Approve ${approvals} ${approvals === 1 ? "draft" : "drafts"}`}
                            />
                        )}
                        {hot_replies > 0 && (
                            <BriefingRow
                                to="/inbox"
                                Icon={Reply}
                                count={hot_replies}
                                label={`${hot_replies} ${hot_replies === 1 ? "reply" : "replies"} waiting`}
                            />
                        )}
                    </div>
                )}
            </div>

            {/* primary CTA */}
            {!allCaughtUp && (
                <div className="flex justify-center">
                    <Button size="lg" asChild>
                        <Link to={reviewHref}>Start review →</Link>
                    </Button>
                </div>
            )}
        </div>
    );
}

function BriefingRow({
    to,
    Icon,
    count,
    label,
}: {
    to: string;
    Icon: typeof PenLine;
    count: number;
    label: string;
}) {
    return (
        <Link
            to={to}
            className="flex items-center gap-4 rounded-xl border border-border bg-card p-4 shadow-sm transition-colors hover:border-primary/40 hover:bg-secondary/30"
        >
            <span className="grid size-9 shrink-0 place-items-center rounded-full bg-accent text-[var(--accent-foreground)]">
                <Icon className="size-4" />
            </span>
            <span className="flex-1 font-medium text-foreground">{label}</span>
            <span className="font-mono text-lg font-bold tabular-nums text-foreground">{count}</span>
            <ChevronRight className="size-5 text-muted-foreground" />
        </Link>
    );
}

// ---------------------------------------------------------------------------
// 4 · Copilot Chat
// ---------------------------------------------------------------------------

interface ChatMessage {
    role: "user" | "agent";
    text: string;
}

const GREETING =
    "Hi — I'm Wren. Ask me what needs you, why I skipped someone, or to find people.";

const SUGGESTIONS = [
    "What needs me today?",
    "Why did you skip Aisha Patel?",
    "Find VPs of Sales in EU fintech",
];

function CopilotChat() {
    const [messages, setMessages] = React.useState<ChatMessage[]>([
        { role: "agent", text: GREETING },
    ]);
    const [input, setInput] = React.useState("");
    const chat = useAgentChat();
    const scrollRef = React.useRef<HTMLDivElement>(null);

    React.useEffect(() => {
        scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
    }, [messages, chat.isPending]);

    async function send(text: string) {
        const trimmed = text.trim();
        if (!trimmed || chat.isPending) return;
        setInput("");
        setMessages((m) => [...m, { role: "user", text: trimmed }]);
        try {
            const res = await chat.mutateAsync(trimmed);
            setMessages((m) => [...m, { role: "agent", text: res.reply }]);
        } catch {
            setMessages((m) => [
                ...m,
                { role: "agent", text: "Sorry — I couldn't reach the agent just now. Try again?" },
            ]);
        }
    }

    return (
        <Card className="flex h-[32rem] flex-col">
            <CardHeader>
                <CardTitle className="flex items-center gap-2">
                    <Bot className="size-4 text-primary" />
                    Copilot
                </CardTitle>
            </CardHeader>

            <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto px-5">
                {messages.map((m, i) =>
                    m.role === "user" ? (
                        <div key={i} className="flex justify-end">
                            <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-primary px-3.5 py-2 text-sm text-primary-foreground">
                                {m.text}
                            </div>
                        </div>
                    ) : (
                        <div key={i} className="flex items-end gap-2">
                            <Avatar className="size-7 shrink-0 rounded-full">
                                <AvatarFallback className="bg-accent text-[var(--accent-foreground)]">
                                    <Bot className="size-3.5" />
                                </AvatarFallback>
                            </Avatar>
                            <div className="max-w-[80%] rounded-2xl rounded-bl-sm border border-border bg-card px-3.5 py-2 text-sm text-foreground shadow-sm">
                                {m.text}
                            </div>
                        </div>
                    ),
                )}
                {chat.isPending && (
                    <div className="flex items-end gap-2">
                        <Avatar className="size-7 shrink-0 rounded-full">
                            <AvatarFallback className="bg-accent text-[var(--accent-foreground)]">
                                <Bot className="size-3.5" />
                            </AvatarFallback>
                        </Avatar>
                        <div className="rounded-2xl rounded-bl-sm border border-border bg-card px-3.5 py-2.5 shadow-sm">
                            <Loader2 className="size-4 animate-spin text-muted-foreground" />
                        </div>
                    </div>
                )}
            </div>

            <CardContent className="space-y-3 pt-4">
                {/* suggestion chips */}
                <div className="flex flex-wrap gap-2">
                    {SUGGESTIONS.map((s) => (
                        <button
                            key={s}
                            type="button"
                            disabled={chat.isPending}
                            onClick={() => void send(s)}
                            className="rounded-full border border-border bg-secondary/40 px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground disabled:opacity-50"
                        >
                            {s}
                        </button>
                    ))}
                </div>

                {/* input row */}
                <form
                    className="flex items-center gap-2"
                    onSubmit={(e) => {
                        e.preventDefault();
                        void send(input);
                    }}
                >
                    <Input
                        value={input}
                        onChange={(e) => setInput(e.target.value)}
                        placeholder="Ask Wren…"
                        disabled={chat.isPending}
                    />
                    <Button type="submit" size="icon" disabled={chat.isPending || !input.trim()}>
                        {chat.isPending ? (
                            <Loader2 className="animate-spin" />
                        ) : (
                            <Send />
                        )}
                    </Button>
                </form>
            </CardContent>
        </Card>
    );
}
