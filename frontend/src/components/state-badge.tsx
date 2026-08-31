import { Badge, type BadgeProps } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type Variant = NonNullable<BadgeProps["variant"]>;

/** Maps a campaign / enrollment / reply state to a label, badge variant and dot color. */
const STATE_MAP: Record<string, { label: string; variant: Variant; dot?: string }> = {
  // campaign
  active: { label: "Active", variant: "accent", dot: "var(--success)" },
  paused: { label: "Paused", variant: "warning", dot: "var(--warning)" },
  draft: { label: "Draft", variant: "outline" },
  done: { label: "Done", variant: "outline" },
  // enrollment
  proposed: { label: "Proposed", variant: "secondary" },
  awaiting_approval: { label: "Awaiting approval", variant: "warning", dot: "var(--warning)" },
  scheduled: { label: "Scheduled", variant: "accent", dot: "var(--success)" },
  awaiting_reply: { label: "Awaiting reply", variant: "secondary" },
  // They wrote back and it wasn't a clear yes or no, so the ball is with the recruiter. The
  // enrollment is still `awaiting_reply` underneath — that's what gates the next touchpoint —
  // which on its own read as "waiting on them" long after they'd answered.
  needs_reply: { label: "Needs your reply", variant: "warning", dot: "var(--warning)" },
  handed_off: { label: "Handed off", variant: "success" },
  opted_out: { label: "Opted out", variant: "destructive" },
  completed: { label: "Completed", variant: "outline" },
  // reply intent
  interested: { label: "Interested", variant: "success" },
  neutral: { label: "Replied", variant: "secondary" },
};

/** Settled outcomes outrank "you owe them a reply" — that result is the more useful label. */
const SETTLED = new Set(["handed_off", "opted_out", "completed", "awaiting_approval"]);

/**
 * What a state should *read* as, given whether a reply is outstanding.
 *
 * `reply_pending` means they answered and it wasn't a clear yes or no. The enrollment stays
 * `awaiting_reply` underneath, because that is what gates the next touchpoint — so rendering the
 * raw state claimed we were still waiting on someone who had already written back. One rule,
 * shared by the badge and by the inbox's filter chips, so the two can't disagree.
 */
function displayState(state: string, replyPending?: boolean): string {
  if (!replyPending || SETTLED.has(state)) return state;
  return "needs_reply";
}

interface StateBadgeProps {
  state: string;
  /** From the enrollment — see `displayState`. */
  replyPending?: boolean;
  className?: string;
}

function StateBadge({ state, replyPending, className }: StateBadgeProps) {
  const shown = displayState(state, replyPending);
  const cfg = STATE_MAP[shown] ?? { label: shown, variant: "outline" as Variant };
  return (
    <Badge variant={cfg.variant} className={cn(className)}>
      {cfg.dot && (
        <span className="size-1.5 rounded-full" style={{ backgroundColor: cfg.dot }} aria-hidden />
      )}
      {cfg.label}
    </Badge>
  );
}

export { StateBadge, STATE_MAP, displayState };
export type { StateBadgeProps };
