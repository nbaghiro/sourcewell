import { cn } from "@/lib/utils";

interface ScoreBarProps {
  /** Fit score 0–100. */
  value: number;
  showValue?: boolean;
  className?: string;
}

/** The candidate fit-score bar: an emerald gradient track + mono numeral. */
function ScoreBar({ value, showValue = true, className }: ScoreBarProps) {
  const v = Math.max(0, Math.min(100, Math.round(value)));
  return (
    <div className={cn("flex items-center gap-2.5", className)}>
      <div className="h-1.5 w-24 overflow-hidden rounded-full bg-muted">
        <div
          className="h-full rounded-full bg-gradient-to-r from-score-from to-score-to"
          style={{ width: `${v}%` }}
        />
      </div>
      {showValue && (
        <span className="font-mono text-sm font-semibold tabular-nums text-foreground">{v}</span>
      )}
    </div>
  );
}

export { ScoreBar };
export type { ScoreBarProps };
