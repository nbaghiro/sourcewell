import { cn } from "@/lib/utils";

interface ScoreBarProps {
  /** Fit score 0–100. */
  value: number;
  showValue?: boolean;
  className?: string;
  /**
   * No score exists for this thread — say so rather than drawing a zero.
   *
   * Fit is measured against a *campaign's* criteria, so a direct conversation has nothing to
   * score against and `Enrollment.score` keeps its column default of 0. Rendering that as an
   * empty bar reads as "we scored them and they're a 0", which is the opposite of the truth for
   * someone you picked out of search and chose to message.
   */
  unscored?: boolean;
}

/** The candidate fit-score bar: an emerald gradient track + mono numeral. */
function ScoreBar({ value, showValue = true, className, unscored = false }: ScoreBarProps) {
  const v = Math.max(0, Math.min(100, Math.round(value)));
  if (unscored) {
    return (
      <span className={cn("text-xs text-muted-foreground", className)}>
        Not scored
      </span>
    );
  }
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
