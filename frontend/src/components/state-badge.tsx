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
  handed_off: { label: "Handed off", variant: "success" },
  opted_out: { label: "Opted out", variant: "destructive" },
  completed: { label: "Completed", variant: "outline" },
  // reply intent
  interested: { label: "Interested", variant: "success" },
  neutral: { label: "Replied", variant: "secondary" },
};

interface StateBadgeProps {
  state: string;
  className?: string;
}

function StateBadge({ state, className }: StateBadgeProps) {
  const cfg = STATE_MAP[state] ?? { label: state, variant: "outline" as Variant };
  return (
    <Badge variant={cfg.variant} className={cn(className)}>
      {cfg.dot && (
        <span className="size-1.5 rounded-full" style={{ backgroundColor: cfg.dot }} aria-hidden />
      )}
      {cfg.label}
    </Badge>
  );
}

export { StateBadge, STATE_MAP };
export type { StateBadgeProps };
