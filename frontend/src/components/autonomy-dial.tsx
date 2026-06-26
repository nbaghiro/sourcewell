import { Check } from "lucide-react";

import { Segmented } from "@/components/ui/segmented";
import { cn } from "@/lib/utils";

export type AutonomyStop = "manual" | "assisted" | "autopilot";
export type AutonomyLevel = "manual" | "assisted" | "full";
export type AutonomyMode = "approve_each" | "auto";

/** One dial that bundles the agent's two backend knobs (autonomy_level + autonomy_mode). */
export const AUTONOMY: Record<
  AutonomyStop,
  { label: string; blurb: string; level: AutonomyLevel; mode: AutonomyMode }
> = {
  manual: {
    label: "Manual",
    blurb: "The agent only suggests — you source, enroll, and send everything yourself.",
    level: "manual",
    mode: "approve_each",
  },
  assisted: {
    label: "Assisted",
    blurb: "The agent sources and drafts continuously; every send waits for your approval.",
    level: "assisted",
    mode: "approve_each",
  },
  autopilot: {
    label: "Autopilot",
    blurb: "The agent sources, drafts, and sends within your caps. You only step in on replies.",
    level: "full",
    mode: "auto",
  },
};

const STOPS: AutonomyStop[] = ["manual", "assisted", "autopilot"];

export function stopFrom(level: string, mode: string): AutonomyStop {
  if (level === "full" && mode === "auto") return "autopilot";
  if (level === "manual") return "manual";
  return "assisted";
}

export function AutonomyDial({
  level,
  mode,
  onChange,
  variant = "compact",
}: {
  level: string;
  mode: string;
  onChange: (patch: { autonomy_level: AutonomyLevel; autonomy_mode: AutonomyMode }) => void;
  variant?: "compact" | "cards";
}) {
  const current = stopFrom(level, mode);
  const set = (s: AutonomyStop) =>
    onChange({ autonomy_level: AUTONOMY[s].level, autonomy_mode: AUTONOMY[s].mode });

  if (variant === "cards") {
    return (
      <div className="space-y-2">
        {STOPS.map((s) => {
          const active = current === s;
          return (
            <button
              key={s}
              type="button"
              onClick={() => set(s)}
              className={cn(
                "flex w-full flex-col gap-0.5 rounded-lg border p-3 text-left transition-colors",
                active
                  ? "border-primary bg-accent/30 ring-1 ring-primary/30"
                  : "border-border hover:border-primary/40 hover:bg-secondary/30",
              )}
            >
              <span className="flex items-center justify-between font-semibold">
                {AUTONOMY[s].label}
                {active && <Check className="size-4 text-primary" />}
              </span>
              <span className="text-xs text-muted-foreground">{AUTONOMY[s].blurb}</span>
            </button>
          );
        })}
      </div>
    );
  }

  return (
    <Segmented
      value={current}
      onChange={(v) => set(v as AutonomyStop)}
      options={STOPS.map((s) => ({ value: s, label: AUTONOMY[s].label }))}
    />
  );
}
