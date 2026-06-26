import {
  Bot,
  ChevronDown,
  ChevronRight,
  Gauge,
  Mail,
  Search,
  ShieldCheck,
  Sparkles,
  UserPlus,
  Users,
  Wrench,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import * as React from "react";

import { Markdown } from "@/components/markdown";
import { StateBadge } from "@/components/state-badge";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useCampaignRuns } from "@/lib/api/queries";
import { longAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

interface RunStep {
  seq: number;
  kind: string;
  tool_name: string | null;
}

interface Run {
  id: string;
  role: string;
  trigger: string;
  status: string;
  summary: string;
  created_at: string;
  steps: RunStep[];
}

// Friendly label + icon for each sourcing-agent tool.
const TOOL: Record<string, { label: string; Icon: LucideIcon }> = {
  search: { label: "Searched candidate sources", Icon: Search },
  list_existing: { label: "Checked your workspace", Icon: Users },
  score: { label: "Scored & ranked candidates", Icon: Gauge },
  enrich: { label: "Enriched contact details", Icon: Mail },
  check_suppressed: { label: "Ran suppression checks", Icon: ShieldCheck },
  import: { label: "Imported & enrolled", Icon: UserPlus },
};
const toolMeta = (tool: string) =>
  TOOL[tool] ?? { label: tool.replace(/_/g, " "), Icon: Wrench };

interface Action {
  type: "tool" | "thought";
  tool: string;
  count: number;
}

// Collapse the raw step stream into grouped actions: consecutive calls to the same
// tool become one row with a count, and reasoning turns fold into a "thought" row.
function groupSteps(steps: RunStep[]): Action[] {
  const out: Action[] = [];
  for (const s of steps) {
    if (s.kind === "thought") {
      const last = out[out.length - 1];
      if (last && last.type === "thought") last.count++;
      else out.push({ type: "thought", tool: "", count: 1 });
      continue;
    }
    if (s.kind !== "tool_call") continue; // results are implied by their call
    const tool = s.tool_name ?? "tool";
    const last = out[out.length - 1];
    if (last && last.type === "tool" && last.tool === tool) last.count++;
    else out.push({ type: "tool", tool, count: 1 });
  }
  return out;
}

/** Per-campaign agent run feed — the sourcing/main/outreach agent's episodes as a timeline. */
export function CampaignActivity({ campaignId, live = false }: { campaignId: string; live?: boolean }) {
  const { data, isLoading } = useCampaignRuns(campaignId, live);
  const runs = (data as Run[] | undefined) ?? [];
  const running = live || runs.some((r) => r.status === "running");

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Bot className="size-4 text-primary" />
          Agent activity
        </CardTitle>
        <span
          className={cn(
            "flex items-center gap-1.5 font-mono text-[0.65rem] font-semibold uppercase tracking-wide",
            running ? "text-primary" : "text-success",
          )}
        >
          <span
            className={cn(
              "size-1.5 rounded-full",
              running ? "animate-pulse bg-primary" : "bg-success",
            )}
            aria-hidden
          />
          {running ? "running" : "live"}
        </span>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="space-y-5">
            {[0, 1, 2].map((i) => (
              <div key={i} className="flex gap-3">
                <Skeleton className="size-8 shrink-0 rounded-full" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-4 w-1/2" />
                  <Skeleton className="h-3 w-3/4" />
                </div>
              </div>
            ))}
          </div>
        ) : runs.length === 0 ? (
          <div className="py-12 text-center">
            <Bot className="mx-auto size-7 text-muted-foreground" />
            <p className="mt-2 text-sm text-muted-foreground">No agent runs yet for this campaign.</p>
          </div>
        ) : (
          <ol className="relative ml-4 space-y-6 border-l border-border pl-6">
            {runs.map((run) => (
              <RunRow key={run.id} run={run} />
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}

function RunRow({ run }: { run: Run }) {
  // Auto-expand a run that's still in flight so its steps stream in live.
  const [open, setOpen] = React.useState(run.status === "running");
  const actions = React.useMemo(() => groupSteps(run.steps), [run.steps]);
  const summary = run.summary?.trim();

  return (
    <li className="relative">
      <span className="absolute -left-[2.35rem] grid size-7 place-items-center rounded-full bg-accent text-accent-foreground ring-4 ring-card">
        <Bot className="size-3.5" />
      </span>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold capitalize text-foreground">{run.role}</span>
            <Badge variant="outline" className="font-normal">
              {run.trigger}
            </Badge>
            <StateBadge state={run.status} />
          </div>

          {summary && (
            <div className="mt-2 rounded-lg border border-border bg-secondary/30 px-3.5 py-2.5 text-sm leading-relaxed">
              <Markdown>{summary}</Markdown>
            </div>
          )}

          {actions.length > 0 && (
            <>
              <button
                type="button"
                onClick={() => setOpen((o) => !o)}
                className="mt-2 inline-flex items-center gap-1 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
              >
                {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
                {open ? "Hide" : "Show"} what the agent did
                <span className="text-muted-foreground/60">· {run.steps.length} steps</span>
              </button>
              {open && (
                <ol className="mt-2.5 space-y-2">
                  {actions.map((a, i) =>
                    a.type === "thought" ? (
                      <li key={i} className="flex items-center gap-2 text-xs text-muted-foreground">
                        <span className="grid size-6 shrink-0 place-items-center rounded-md bg-secondary text-muted-foreground">
                          <Sparkles className="size-3.5" />
                        </span>
                        <span className="italic">
                          Reasoned about next steps
                          {a.count > 1 && <span className="not-italic"> · {a.count}×</span>}
                        </span>
                      </li>
                    ) : (
                      <ActionRow key={i} tool={a.tool} count={a.count} />
                    ),
                  )}
                </ol>
              )}
            </>
          )}
        </div>
        <time className="shrink-0 whitespace-nowrap font-mono text-xs text-muted-foreground">
          {longAgo(run.created_at)}
        </time>
      </div>
    </li>
  );
}

function ActionRow({ tool, count }: { tool: string; count: number }) {
  const { label, Icon } = toolMeta(tool);
  return (
    <li className="flex items-center gap-2 text-xs">
      <span className="grid size-6 shrink-0 place-items-center rounded-md bg-accent text-accent-foreground">
        <Icon className="size-3.5" />
      </span>
      <span className="font-medium text-foreground">{label}</span>
      {count > 1 && (
        <Badge variant="secondary" className="px-1.5 font-mono text-[0.6rem] tabular-nums">
          ×{count}
        </Badge>
      )}
    </li>
  );
}
