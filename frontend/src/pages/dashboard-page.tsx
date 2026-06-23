import { Plus, Send, Upload } from "lucide-react";
import { Link, useNavigate } from "react-router-dom";

import { DataError } from "@/components/data-error";
import { PageHeader } from "@/components/page-header";
import { PageLayout } from "@/components/page-layout";
import { PersonCell } from "@/components/person-cell";
import { StatCard } from "@/components/stat-card";
import { StateBadge } from "@/components/state-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useDashboard } from "@/lib/api/queries";
import { useAuth } from "@/lib/auth";
import { useWorkspace } from "@/lib/workspace";

export function DashboardPage() {
  const { me } = useAuth();
  const { workspaces, workspaceId } = useWorkspace();
  const { data: summary, isError, refetch } = useDashboard();
  const first = me?.user?.name?.split(" ")[0] ?? "there";
  const wsName = workspaces.find((w) => w.id === workspaceId)?.name ?? "this workspace";

  const empty = summary && summary.stats.contacts === 0 && summary.campaigns.length === 0;

  if (isError) {
    return (
      <PageLayout>
        <DataError onRetry={() => void refetch()} />
      </PageLayout>
    );
  }

  if (empty) {
    return <Onboarding workspace={wsName} onChanged={() => void refetch()} />;
  }

  return (
    <PageLayout>
      <PageHeader
        eyebrow="Workspace overview"
        title={`Good morning, ${first}`}
        description="Sourcing activity across your campaigns. Approvals and replies need you; the rest runs on its own."
      />

      {!summary ? (
        <Skeletons />
      ) : (
        <>
          <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
            <StatCard label="Active campaigns" value={summary.stats.active_campaigns} />
            <StatCard label="Contacts sourced" value={summary.stats.contacts.toLocaleString()} />
            <StatCard label="Awaiting approval" value={summary.stats.awaiting_approval} trend="needs review" trendDirection="flat" />
            <StatCard label="Replies · 7d" value={summary.stats.replies_7d} trend="this week" />
          </div>

          <div className="grid gap-4 lg:grid-cols-[1.5fr_1fr]">
            <Card>
              <CardHeader>
                <CardTitle>Campaigns</CardTitle>
                <span className="text-xs text-muted-foreground">{summary.campaigns.length}</span>
              </CardHeader>
              <CardContent className="space-y-2">
                {summary.campaigns.map((c) => (
                  <Link
                    key={c.id}
                    to={`/campaigns/${c.id}`}
                    className="block rounded-lg border border-border p-3 transition-colors hover:border-primary/40 hover:bg-secondary/30"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate font-semibold text-foreground">{c.name}</span>
                      <StateBadge state={c.status} />
                    </div>
                    <div className="mt-2 flex items-center gap-4 text-xs text-muted-foreground">
                      <Metric value={c.sourced} label="sourced" />
                      <Metric value={c.awaiting} label="awaiting" />
                      <Metric value={c.replies} label="replies" />
                    </div>
                  </Link>
                ))}
              </CardContent>
            </Card>

            <div className="flex flex-col gap-4">
              <Card>
                <CardHeader>
                  <CardTitle>Approvals</CardTitle>
                  <span className="text-xs text-muted-foreground">{summary.stats.awaiting_approval} waiting</span>
                </CardHeader>
                <CardContent className="space-y-3.5">
                  {summary.approvals.length === 0 && (
                    <p className="text-sm text-muted-foreground">Nothing to approve right now.</p>
                  )}
                  {summary.approvals.slice(0, 3).map((a) => (
                    <div key={a.enrollment_id} className="space-y-1.5">
                      <div className="flex items-center justify-between gap-2">
                        <PersonCell name={a.contact_name} subtitle={a.title ?? undefined} imageSrc={a.contact_avatar ?? undefined} />
                        <span className="font-mono text-sm font-semibold text-primary">{a.score}</span>
                      </div>
                      <p className="truncate text-xs text-muted-foreground">{a.subject}</p>
                    </div>
                  ))}
                </CardContent>
              </Card>

              <Card>
                <CardHeader>
                  <CardTitle>Recent replies</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3">
                  {summary.recent_replies.slice(0, 4).map((r, i) => (
                    <div key={i} className="flex items-center justify-between gap-2">
                      <div className="min-w-0">
                        <div className="text-sm font-semibold text-foreground">{r.contact_name}</div>
                        <div className="truncate text-xs text-muted-foreground">{r.snippet}</div>
                      </div>
                      <StateBadge state={r.state} />
                    </div>
                  ))}
                </CardContent>
              </Card>
            </div>
          </div>
        </>
      )}
    </PageLayout>
  );
}

function Onboarding({ workspace }: { workspace: string; onChanged: () => void }) {
  const navigate = useNavigate();

  const steps = [
    {
      n: 1,
      title: "Add candidates",
      body: "Import your list of people to start sourcing.",
      actions: (
        <Button size="sm" onClick={() => navigate("/contacts")}>
          <Upload /> Import contacts
        </Button>
      ),
    },
    {
      n: 2,
      title: "Create a campaign",
      body: "Define who to reach and a multi-channel touch sequence.",
      actions: (
        <Button variant="outline" size="sm" onClick={() => navigate("/campaigns/new")}>
          <Plus /> New campaign
        </Button>
      ),
    },
    {
      n: 3,
      title: "Review & send",
      body: "Rank candidates, then approve each message before it goes out.",
      actions: <span className="text-xs text-muted-foreground">Replies land in your Inbox.</span>,
    },
  ];

  return (
    <div className="mx-auto max-w-2xl py-6">
      <div className="mb-6 text-center">
        <div className="mx-auto mb-3 grid size-12 place-items-center rounded-xl bg-gradient-to-br from-score-from to-score-to text-primary-foreground">
          <Send className="size-5" />
        </div>
        <h1 className="font-display text-2xl font-bold tracking-tight">Set up {workspace}</h1>
        <p className="mt-1.5 text-sm text-muted-foreground">Three steps to your first outreach.</p>
      </div>
      <div className="space-y-3">
        {steps.map((s) => (
          <Card key={s.n} className="flex items-center gap-4 p-5">
            <div className="grid size-9 shrink-0 place-items-center rounded-full bg-accent font-display font-bold text-[var(--accent-strong)]">
              {s.n}
            </div>
            <div className="min-w-0 flex-1">
              <div className="font-display font-semibold">{s.title}</div>
              <div className="text-sm text-muted-foreground">{s.body}</div>
            </div>
            <div className="flex shrink-0 items-center gap-2">{s.actions}</div>
          </Card>
        ))}
      </div>
    </div>
  );
}

function Metric({ value, label }: { value: number; label: string }) {
  return (
    <span>
      <span className="font-mono font-semibold tabular-nums text-foreground">{value}</span> {label}
    </span>
  );
}

function Skeletons() {
  return (
    <div className="space-y-6">
      <div className="grid grid-cols-2 gap-4 md:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-[1.5fr_1fr]">
        <Skeleton className="h-72" />
        <Skeleton className="h-72" />
      </div>
    </div>
  );
}
