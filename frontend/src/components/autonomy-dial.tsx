import { Check } from "lucide-react";

import { Segmented } from "@/components/ui/segmented";
import type { components } from "@/lib/api/schema";
import { cn } from "@/lib/utils";

export type AutonomyStop = "manual" | "assisted" | "autopilot";
export type AutonomyLevel = components["schemas"]["AutonomyLevel"];

/** One dial over the backend's single autonomy knob (autonomy_level). */
export const AUTONOMY: Record<AutonomyStop, { label: string; blurb: string; level: AutonomyLevel }> =
  {
    manual: {
      label: "Manual",
      blurb: "The agent only suggests — you source, enroll, and send everything yourself.",
      level: "manual",
    },
    assisted: {
      label: "Assisted",
      blurb: "The agent sources and drafts continuously; every send waits for your approval.",
      level: "assisted",
    },
    autopilot: {
      label: "Autopilot",
      blurb: "The agent sources, drafts, and sends within your caps. You only step in on replies.",
      level: "full",
    },
  };

const STOPS: AutonomyStop[] = ["manual", "assisted", "autopilot"];

export function stopFrom(level: string): AutonomyStop {
  if (level === "full") return "autopilot";
  if (level === "manual") return "manual";
  return "assisted";
}

export function AutonomyDial({
  level,
  onChange,
  variant = "compact",
}: {
  level: string;
  onChange: (patch: { autonomy_level: AutonomyLevel }) => void;
  variant?: "compact" | "cards";
}) {
  const current = stopFrom(level);
  const set = (s: AutonomyStop) => onChange({ autonomy_level: AUTONOMY[s].level });

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
