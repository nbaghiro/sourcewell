import { CheckCircle2 } from "lucide-react";
import { Link } from "react-router-dom";

import { PageHeader } from "@/components/page-header";
import { PageLayout } from "@/components/page-layout";
import { StateBadge } from "@/components/state-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAgentState } from "@/lib/api/queries";
import { cn } from "@/lib/utils";

// The read-model is loosely typed in the schema; these are the authoritative shapes.
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

/** The agent's command center — live status, queue, throughput, governor headroom, what needs you.
 *  (The conversational surface is the always-on chat widget, not a tab here.) */
export function AgentPage() {
    return (
        <PageLayout width="wide">
            <PageHeader
                eyebrow="Your agent"
                title="Wren"
                description="Your sourcing agent — drafting, sending, and watching for replies within your guardrails."
            />
            <MissionControl />
        </PageLayout>
    );
}

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
