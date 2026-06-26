import {
  Archive,
  ArrowLeft,
  Copy,
  MoreHorizontal,
  Pause,
  Play,
  Radar,
  Sparkles,
  Trash2,
} from "lucide-react";
import * as React from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";

import { CampaignActivity } from "@/components/campaign-activity";
import { CampaignComposer, type Step } from "@/components/campaign-composer";
import { PageHeader } from "@/components/page-header";
import { PageLayout } from "@/components/page-layout";
import { longAgo } from "@/lib/format";
import { toTargeting, type Targeting } from "@/lib/targeting";
import { PersonCell } from "@/components/person-cell";
import { ScoreBar } from "@/components/score-bar";
import { Segmented } from "@/components/ui/segmented";
import { StateBadge } from "@/components/state-badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Checkbox } from "@/components/ui/checkbox";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  useBulkApprove,
  useCampaign,
  useCampaignEnrollments,
  useCampaignLifecycle,
  useCampaignRuns,
  useContacts,
  useDeleteCampaign,
  useDuplicateCampaign,
  useRankCampaign,
  useSourceNow,
  useUpdateCampaign,
} from "@/lib/api/queries";

interface Campaign {
  id: string;
  name: string;
  status: string;
  autonomy_mode: string;
  autonomy_level: string;
  criteria: Partial<Targeting>;
  sequence: { channel: "email" | "linkedin"; delay_days: number; subject: string | null; body: string }[];
  next_source_at: string | null;
}

interface AgentRun {
  role: string;
  summary: string;
  created_at: string;
}

const IN_SEQUENCE = ["active", "awaiting_approval", "scheduled", "awaiting_reply"];
const TABS: { value: string; label: string; match: (s: string) => boolean }[] = [
  { value: "proposed", label: "Proposed", match: (s) => s === "proposed" },
  { value: "sequence", label: "In sequence", match: (s) => IN_SEQUENCE.includes(s) },
  { value: "handed_off", label: "Handed off", match: (s) => s === "handed_off" },
  { value: "opted_out", label: "Opted out", match: (s) => s === "opted_out" },
];

function CampaignActions({ id, status }: { id: string; status: string }) {
  const navigate = useNavigate();
  const lifecycle = useCampaignLifecycle();
  const duplicate = useDuplicateCampaign();
  const del = useDeleteCampaign();
  const active = status === "active";
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" size="sm" className="px-2">
          <MoreHorizontal />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end">
        {active ? (
          <DropdownMenuItem onClick={() => lifecycle.mutate({ id, action: "pause" }, { onSuccess: () => toast.success("Campaign paused") })}>
            <Pause /> Pause
          </DropdownMenuItem>
        ) : (
          <DropdownMenuItem onClick={() => lifecycle.mutate({ id, action: "resume" }, { onSuccess: () => toast.success("Campaign resumed") })}>
            <Play /> Resume
          </DropdownMenuItem>
        )}
        <DropdownMenuItem onClick={() => duplicate.mutate(id, { onSuccess: (c) => { toast.success("Duplicated"); navigate(`/campaigns/${c.id}`); } })}>
          <Copy /> Duplicate
        </DropdownMenuItem>
        <DropdownMenuItem onClick={() => lifecycle.mutate({ id, action: "archive" }, { onSuccess: () => toast.success("Campaign archived") })}>
          <Archive /> Archive
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem
          className="text-destructive focus:text-destructive"
          onClick={() => del.mutate(id, { onSuccess: () => { toast.success("Campaign deleted"); navigate("/campaigns"); } })}
        >
          <Trash2 /> Delete
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}

export function CampaignDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: campaignData } = useCampaign(id ?? "");
  const campaign = campaignData as Campaign | undefined;
  const { data: enrollments } = useCampaignEnrollments(id ?? "");
  const { data: pool } = useContacts();
  const rankCampaign = useRankCampaign(id ?? "");
  const bulkApprove = useBulkApprove();
  const updateCampaign = useUpdateCampaign();
  const sourceNow = useSourceNow();
  const { data: runs } = useCampaignRuns(id ?? "");

  const [view, setView] = React.useState<"sequence" | "activity" | "candidates">("sequence");
  const [tab, setTab] = React.useState("proposed");
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const busy = rankCampaign.isPending || bulkApprove.isPending;

  // Editable copies of the campaign's audience + sequence (autosaved via PATCH).
  const [criteria, setCriteria] = React.useState<Targeting | null>(null);
  const [steps, setSteps] = React.useState<Step[] | null>(null);
  const dirty = React.useRef(false);

  React.useEffect(() => {
    if (!campaign || criteria !== null) return;
    setCriteria(toTargeting(campaign.criteria));
    setSteps(campaign.sequence.map((s) => ({ channel: s.channel, delay_days: s.delay_days, subject: s.subject ?? "", body: s.body })));
  }, [campaign, criteria]);

  React.useEffect(() => {
    if (!dirty.current || !criteria || !steps || !id) return;
    const t = setTimeout(() => {
      updateCampaign.mutate({ id, patch: { criteria, sequence: steps } });
    }, 800);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [criteria, steps, id]);

  const all = enrollments ?? [];
  const counts = (m: (s: string) => boolean) => all.filter((e) => m(e.state)).length;
  const activeTab = TABS.find((t) => t.value === tab)!;
  const rows = all.filter((e) => activeTab.match(e.state));

  function rank() {
    rankCampaign.mutate(undefined, {
      onSuccess: (r) => toast.success(`Ranked ${r.proposed} new contact${r.proposed === 1 ? "" : "s"}`),
    });
  }
  function approve(ids: string[]) {
    if (ids.length === 0) return;
    bulkApprove.mutate(ids, {
      onSuccess: (r) => {
        toast.success(`Approved ${r.approved} lead${r.approved === 1 ? "" : "s"}`);
        setSelected(new Set());
      },
    });
  }
  function toggle(eid: string) {
    setSelected((s) => {
      const n = new Set(s);
      n.has(eid) ? n.delete(eid) : n.add(eid);
      return n;
    });
  }

  return (
    <PageLayout>
      <Link to="/campaigns" className="inline-flex items-center gap-1.5 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground">
        <ArrowLeft className="size-4" /> Campaigns
      </Link>

      {!campaign ? (
        <Skeleton className="h-40" />
      ) : (
        <>
          <PageHeader eyebrow="Campaign" title={campaign.name}>
            <Button
              variant="outline"
              size="sm"
              disabled={sourceNow.isPending}
              onClick={() =>
                sourceNow.mutate(campaign.id, {
                  onSuccess: () => toast.success("Sourcing queued — the agent runs shortly"),
                })
              }
            >
              <Radar /> Source now
            </Button>
            <Button variant="outline" size="sm" disabled={busy} onClick={() => void rank()}>
              <Sparkles /> Rank contacts
            </Button>
            <CampaignActions id={campaign.id} status={campaign.status} />
          </PageHeader>

          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <StateBadge state={campaign.status} />
              <span className="flex items-center gap-1.5" title="How much the agent sources and enrolls on its own">
                <span className="text-xs text-muted-foreground">Autonomy</span>
                <Segmented
                  value={campaign.autonomy_level}
                  onChange={(v) =>
                    updateCampaign.mutate({
                      id: campaign.id,
                      patch: { autonomy_level: v as "manual" | "assisted" | "full" },
                    })
                  }
                  options={[
                    { value: "manual", label: "Manual" },
                    { value: "assisted", label: "Assisted" },
                    { value: "full", label: "Full" },
                  ]}
                />
              </span>
              <span className="flex items-center gap-1.5" title="Whether outbound messages need your approval">
                <span className="text-xs text-muted-foreground">Sends</span>
                <Segmented
                  value={campaign.autonomy_mode}
                  onChange={(v) =>
                    updateCampaign.mutate({
                      id: campaign.id,
                      patch: { autonomy_mode: v as "approve_each" | "auto" },
                    })
                  }
                  options={[
                    { value: "approve_each", label: "Approve each" },
                    { value: "auto", label: "Auto-send" },
                  ]}
                />
              </span>
              <span className="text-muted-foreground">·</span>
              <span className="text-muted-foreground">{all.length} candidates</span>
              {(() => {
                const sourced = ((runs ?? []) as AgentRun[]).find((r) => r.role === "sourcing");
                if (sourced)
                  return (
                    <>
                      <span className="text-muted-foreground">·</span>
                      <span className="text-muted-foreground">
                        sourced {longAgo(sourced.created_at)}
                      </span>
                    </>
                  );
                if (campaign.next_source_at)
                  return (
                    <>
                      <span className="text-muted-foreground">·</span>
                      <span className="font-medium text-primary">sourcing queued</span>
                    </>
                  );
                return null;
              })()}
            </div>
            <Segmented
              value={view}
              onChange={(v) => setView(v as "sequence" | "activity" | "candidates")}
              options={[
                { value: "sequence", label: "Sequence" },
                { value: "activity", label: "Activity" },
                { value: "candidates", label: `Candidates · ${all.length}` },
              ]}
            />
          </div>

          {view === "sequence" ? (
            <div className="space-y-2">
              <p className="text-right text-xs text-muted-foreground">
                {updateCampaign.isPending ? "Saving…" : dirty.current ? "Changes saved" : "Changes save automatically"}
              </p>
              {criteria && steps ? (
                <CampaignComposer
                  criteria={criteria}
                  steps={steps}
                  pool={pool}
                  onCriteriaChange={(c) => {
                    dirty.current = true;
                    setCriteria(c);
                  }}
                  onStepsChange={(s) => {
                    dirty.current = true;
                    setSteps(s);
                  }}
                />
              ) : (
                <Skeleton className="h-96" />
              )}
            </div>
          ) : view === "activity" ? (
            <CampaignActivity campaignId={campaign.id} />
          ) : (
            <>
              <div className="flex items-center justify-between gap-3">
                <Segmented
                  value={tab}
                  onChange={(v) => {
                    setTab(v);
                    setSelected(new Set());
                  }}
                  options={TABS.map((t) => ({ value: t.value, label: `${t.label} · ${counts(t.match)}` }))}
                />
                {tab === "proposed" && selected.size > 0 && (
                  <Button size="sm" disabled={busy} onClick={() => void approve([...selected])}>
                    Approve selected · {selected.size}
                  </Button>
                )}
              </div>

              <Card>
                {!enrollments ? (
                  <div className="space-y-3 p-5">
                    <Skeleton className="h-10" />
                    <Skeleton className="h-10" />
                  </div>
                ) : rows.length === 0 ? (
                  <div className="p-10 text-center text-sm text-muted-foreground">
                    {tab === "proposed" ? "No proposed candidates. Click “Rank contacts” to score the workspace." : "Nothing here yet."}
                  </div>
                ) : (
                  <Table>
                    <TableHeader>
                      <TableRow>
                        {tab === "proposed" && <TableHead className="w-8" />}
                        <TableHead>Candidate</TableHead>
                        <TableHead>Fit</TableHead>
                        {tab === "proposed" ? <TableHead>Why</TableHead> : <TableHead>State</TableHead>}
                        <TableHead />
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {rows.map((e) => (
                        <TableRow key={e.id}>
                          {tab === "proposed" && (
                            <TableCell>
                              <Checkbox checked={selected.has(e.id)} onCheckedChange={() => toggle(e.id)} />
                            </TableCell>
                          )}
                          <TableCell>
                            <Link to={`/people/${e.contact_id}`} className="inline-block rounded-md transition-opacity hover:opacity-80">
                              <PersonCell name={e.contact_name} subtitle={e.contact_title ?? undefined} imageSrc={e.contact_avatar ?? undefined} />
                            </Link>
                          </TableCell>
                          <TableCell>
                            <ScoreBar value={e.score} />
                          </TableCell>
                          {tab === "proposed" ? (
                            <TableCell className="max-w-xs truncate text-xs text-muted-foreground">{e.score_rationale}</TableCell>
                          ) : (
                            <TableCell>
                              <StateBadge state={e.state} />
                            </TableCell>
                          )}
                          <TableCell className="text-right">
                            {tab === "proposed" && (
                              <Button variant="outline" size="sm" disabled={busy} onClick={() => void approve([e.id])}>
                                Approve
                              </Button>
                            )}
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                )}
              </Card>
            </>
          )}
        </>
      )}
    </PageLayout>
  );
}
