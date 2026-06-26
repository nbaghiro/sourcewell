import { ArrowRight, Check, FileText, Loader2, SlidersHorizontal, Sparkles, Users } from "lucide-react";
import * as React from "react";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { type Contact, useIntake } from "@/lib/api/queries";
import { initials } from "@/lib/format";
import { emptyTargeting, type Targeting } from "@/lib/targeting";
import { cn } from "@/lib/utils";

export interface IntakeResult {
  name: string;
  objective: string;
  criteria: Targeting;
  seedContactIds: string[];
  authoredBy: "agent" | "human"; // JD / examples → the agent designed it; manual → you did
}

type Mode = "jd" | "examples" | "manual";

const MODES: { value: Mode; label: string; hint: string; Icon: typeof FileText }[] = [
  { value: "jd", label: "Paste a job description", hint: "The agent reads it into criteria", Icon: FileText },
  { value: "examples", label: "From example people", hint: "Find more like these", Icon: Users },
  { value: "manual", label: "Enter criteria", hint: "Set the audience yourself", Icon: SlidersHorizontal },
];

// --- derive starting criteria from a handful of example contacts (look-alike, no embeddings) ----

function mostCommon(values: string[], n: number): string[] {
  const counts = new Map<string, number>();
  for (const v of values) {
    const t = v.trim();
    if (t) counts.set(t, (counts.get(t) ?? 0) + 1);
  }
  return [...counts.entries()].sort((a, b) => b[1] - a[1]).slice(0, n).map(([k]) => k);
}

function criteriaFromSeeds(seeds: Contact[]): Targeting {
  return {
    ...emptyTargeting(),
    titles: mostCommon(seeds.map((c) => c.title ?? "").filter(Boolean), 3),
    skills: mostCommon(seeds.flatMap((c) => c.skills ?? []), 5),
    locations: mostCommon(seeds.map((c) => c.location ?? "").filter(Boolean), 3),
    industries: mostCommon(seeds.map((c) => c.industry ?? "").filter(Boolean), 2),
  };
}

// A short campaign name from the parsed criteria (the role title), not the full objective sentence.
function campaignName(criteria: Targeting, objective: string): string {
  const title = criteria.titles[0]?.trim();
  if (title) return title.length > 40 ? title.slice(0, 40).trim() : title;
  const clause = objective.split(/[,.;:—–-]/)[0].trim();
  return clause ? clause.slice(0, 36).trim() : "New campaign";
}

export function CampaignIntake({
  pool,
  onComplete,
  onCancel,
}: {
  pool: Contact[] | undefined;
  onComplete: (result: IntakeResult) => void;
  onCancel: () => void;
}) {
  const [mode, setMode] = React.useState<Mode>("jd");
  const [jd, setJd] = React.useState("");
  const [seeds, setSeeds] = React.useState<Set<string>>(new Set());
  const [query, setQuery] = React.useState("");
  const intake = useIntake();

  async function analyzeJd() {
    const text = jd.trim();
    if (!text || intake.isPending) return;
    const res = await intake.mutateAsync(text);
    const criteria = { ...emptyTargeting(), ...(res.criteria as Partial<Targeting>) };
    const name = campaignName(criteria, res.objective ?? "");
    onComplete({ name, objective: res.objective ?? "", criteria, seedContactIds: [], authoredBy: "agent" });
  }

  function continueWithSeeds() {
    const chosen = (pool ?? []).filter((c) => seeds.has(c.id));
    if (chosen.length === 0) return;
    const titles = chosen[0].title ? `like ${chosen[0].title}` : "look-alike";
    onComplete({
      name: `Sourcing ${titles}`,
      objective: `Find people resembling ${chosen.map((c) => c.full_name).join(", ")}`,
      criteria: criteriaFromSeeds(chosen),
      seedContactIds: chosen.map((c) => c.id),
      authoredBy: "agent",
    });
  }

  function continueManual() {
    onComplete({
      name: "New campaign",
      objective: "",
      criteria: emptyTargeting(),
      seedContactIds: [],
      authoredBy: "human",
    });
  }

  const filtered = (pool ?? []).filter((c) =>
    [c.full_name, c.title, c.company].filter(Boolean).join(" ").toLowerCase().includes(query.toLowerCase()),
  );

  return (
    <div className="mx-auto max-w-2xl space-y-6 py-4">
      <div>
        <p className="font-mono text-[0.6rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
          New campaign
        </p>
        <h1 className="mt-1 font-display text-2xl font-bold tracking-tight text-foreground">
          How do you want to start?
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          The agent turns this into an audience, sources matching people, and drafts the outreach.
        </p>
      </div>

      {/* mode chooser */}
      <div className="grid gap-2.5 sm:grid-cols-3">
        {MODES.map((m) => (
          <button
            key={m.value}
            type="button"
            onClick={() => setMode(m.value)}
            className={cn(
              "flex flex-col items-start gap-1.5 rounded-xl border p-3.5 text-left transition-colors",
              mode === m.value
                ? "border-primary bg-accent/40 ring-1 ring-primary/30"
                : "border-border hover:border-primary/40 hover:bg-secondary/30",
            )}
          >
            <m.Icon className="size-4 text-primary" />
            <span className="text-sm font-semibold text-foreground">{m.label}</span>
            <span className="text-xs text-muted-foreground">{m.hint}</span>
          </button>
        ))}
      </div>

      {/* active panel */}
      {mode === "jd" && (
        <div className="space-y-3">
          <textarea
            value={jd}
            onChange={(e) => setJd(e.target.value)}
            placeholder="Paste the job description or a short brief — e.g. “Senior backend engineer, Go/Python, EU, fintech, 5+ years”…"
            className="min-h-[12rem] w-full resize-y rounded-xl border border-border bg-card px-4 py-3 text-sm text-foreground outline-none placeholder:text-muted-foreground/60 focus-visible:ring-2 focus-visible:ring-ring"
          />
          <div className="flex justify-end">
            <Button onClick={() => void analyzeJd()} disabled={!jd.trim() || intake.isPending}>
              {intake.isPending ? (
                <>
                  <Loader2 className="animate-spin" /> Reading…
                </>
              ) : (
                <>
                  <Sparkles /> Analyze &amp; continue
                </>
              )}
            </Button>
          </div>
        </div>
      )}

      {mode === "examples" && (
        <div className="space-y-3">
          <Input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search your contacts to use as examples…"
          />
          <div className="max-h-[18rem] divide-y divide-border overflow-y-auto rounded-xl border border-border">
            {filtered.length === 0 ? (
              <p className="px-4 py-6 text-center text-sm text-muted-foreground">
                {pool?.length ? "No contacts match." : "No contacts yet — add some first, or start from a JD."}
              </p>
            ) : (
              filtered.map((c) => {
                const on = seeds.has(c.id);
                return (
                  <button
                    key={c.id}
                    type="button"
                    onClick={() =>
                      setSeeds((s) => {
                        const next = new Set(s);
                        if (next.has(c.id)) next.delete(c.id);
                        else next.add(c.id);
                        return next;
                      })
                    }
                    className="flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors hover:bg-secondary/40"
                  >
                    <Avatar className="size-8 rounded-full">
                      {c.avatar_url && <AvatarImage src={c.avatar_url} />}
                      <AvatarFallback className="bg-secondary text-xs">
                        {initials(c.full_name)}
                      </AvatarFallback>
                    </Avatar>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-foreground">{c.full_name}</span>
                      <span className="block truncate text-xs text-muted-foreground">
                        {[c.title, c.company].filter(Boolean).join(" · ")}
                      </span>
                    </span>
                    <span
                      className={cn(
                        "grid size-5 shrink-0 place-items-center rounded-full border",
                        on ? "border-primary bg-primary text-primary-foreground" : "border-border",
                      )}
                    >
                      {on && <Check className="size-3" />}
                    </span>
                  </button>
                );
              })
            )}
          </div>
          <div className="flex items-center justify-between">
            <span className="text-xs text-muted-foreground">
              {seeds.size} selected · the agent sources people resembling them
            </span>
            <Button onClick={continueWithSeeds} disabled={seeds.size === 0}>
              Continue <ArrowRight />
            </Button>
          </div>
        </div>
      )}

      {mode === "manual" && (
        <div className="flex items-center justify-between rounded-xl border border-dashed border-border p-4">
          <span className="text-sm text-muted-foreground">
            Skip the agent intake and set the audience yourself in the builder.
          </span>
          <Button variant="outline" onClick={continueManual}>
            Continue <ArrowRight />
          </Button>
        </div>
      )}

      <div className="border-t border-border pt-3">
        <Button variant="ghost" size="sm" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}
