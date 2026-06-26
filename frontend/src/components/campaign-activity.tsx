import { Bot, ChevronDown, ChevronRight } from "lucide-react";
import * as React from "react";

import { StateBadge } from "@/components/state-badge";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useCampaignRuns } from "@/lib/api/queries";
import { longAgo } from "@/lib/format";

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

/** Per-campaign agent run feed — the sourcing/main/outreach agent's episodes as a timeline. */
export function CampaignActivity({ campaignId }: { campaignId: string }) {
  const { data, isLoading } = useCampaignRuns(campaignId);
  const runs = (data as Run[] | undefined) ?? [];

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Bot className="size-4 text-primary" />
          Agent activity
        </CardTitle>
        <span className="flex items-center gap-1.5 font-mono text-[0.65rem] font-semibold uppercase tracking-wide text-success">
          <span className="size-1.5 animate-pulse rounded-full bg-success" aria-hidden />
          live
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
  const [open, setOpen] = React.useState(false);
  const hasSteps = run.steps.length > 0;

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
          <p className="mt-0.5 text-sm text-muted-foreground">{run.summary}</p>
          {hasSteps && (
            <button
              type="button"
              onClick={() => setOpen((o) => !o)}
              className="mt-1.5 inline-flex items-center gap-1 text-xs font-medium text-muted-foreground transition-colors hover:text-foreground"
            >
              {open ? <ChevronDown className="size-3.5" /> : <ChevronRight className="size-3.5" />}
              {run.steps.length} step{run.steps.length === 1 ? "" : "s"}
            </button>
          )}
          {open && (
            <ol className="mt-2 space-y-1 border-l border-border pl-3">
              {run.steps.map((s) => (
                <li key={s.seq} className="flex items-center gap-2 text-xs">
                  <span className="font-mono tabular-nums text-muted-foreground">{s.seq}</span>
                  <span className="font-medium text-foreground">{s.kind}</span>
                  {s.tool_name && (
                    <span className="font-mono text-muted-foreground">{s.tool_name}</span>
                  )}
                </li>
              ))}
            </ol>
          )}
        </div>
        <time className="shrink-0 whitespace-nowrap font-mono text-xs text-muted-foreground">
          {longAgo(run.created_at)}
        </time>
      </div>
    </li>
  );
}
