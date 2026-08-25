import { cn } from "@/lib/utils";

/** A thin progress track with a colored fill. `pct` is 0–100; `tone` is any CSS color.
 *  Height + track color override via `className` (e.g. "h-1.5 bg-black/25"). */
export function Meter({ pct, tone, className }: { pct: number; tone: string; className?: string }) {
  return (
    <div className={cn("h-2 w-full overflow-hidden rounded-full bg-secondary", className)}>
      <div
        className="h-full rounded-full transition-all"
        style={{ width: `${Math.min(100, Math.max(0, pct))}%`, background: tone }}
      />
    </div>
  );
}

/** Over → near (≥80%) → ok color for a usage meter. Shared so the sidebar + settings agree. */
export function usageTone(pct: number, over: boolean): string {
  if (over) return "var(--destructive)";
  if (pct >= 80) return "var(--warning)";
  return "var(--score-to)";
}
