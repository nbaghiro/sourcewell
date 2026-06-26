import { StateBadge } from "@/components/state-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useAgentState } from "@/lib/api/queries";
import { cn } from "@/lib/utils";

interface Channel {
  cap: number;
  sent: number;
  blocked: boolean;
}

interface AgentState {
  status: string;
  governor: { email: Channel; linkedin: Channel };
}

/** Compact agent status + per-channel send-cap headroom — a card on the dashboard. */
export function AgentStatus() {
  const { data } = useAgentState();
  const state = data as AgentState | undefined;
  if (!state) return null;

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          Agent <StateBadge state={state.status} />
        </CardTitle>
        <span className="text-xs text-muted-foreground">send headroom today</span>
      </CardHeader>
      <CardContent className="space-y-4">
        <GovernorBar label="Email" channel={state.governor.email} />
        <GovernorBar label="LinkedIn" channel={state.governor.linkedin} />
      </CardContent>
    </Card>
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
          channel.blocked ? "bg-[color-mix(in_srgb,var(--destructive)_18%,white)]" : "bg-secondary",
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
