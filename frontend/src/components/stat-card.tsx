import { ArrowUp } from "lucide-react";

import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface StatCardProps {
  label: string;
  value: string | number;
  trend?: string;
  trendDirection?: "up" | "flat";
  className?: string;
}

/** A single overview metric: label, big display number, optional trend line. */
function StatCard({ label, value, trend, trendDirection = "up", className }: StatCardProps) {
  return (
    <Card className={cn("p-5", className)}>
      <p className="text-sm font-medium text-muted-foreground">{label}</p>
      <p className="mt-1.5 font-display text-3xl font-bold tabular-nums tracking-tight text-foreground">
        {value}
      </p>
      {trend && (
        <p
          className={cn(
            "mt-1 flex items-center gap-1 font-mono text-xs font-semibold",
            trendDirection === "flat" ? "text-muted-foreground" : "text-success",
          )}
        >
          {trendDirection === "up" && <ArrowUp className="size-3" strokeWidth={2.5} />}
          {trend}
        </p>
      )}
    </Card>
  );
}

export { StatCard };
export type { StatCardProps };
