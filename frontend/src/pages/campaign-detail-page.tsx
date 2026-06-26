import {
  Archive,
  ArrowLeft,
  ArrowRight,
  Bot,
  Copy,
  MoreHorizontal,
  Pause,
  Play,
  Radar,
  ScrollText,
  Settings2,
  Sparkles,
  Trash2,
} from "lucide-react";
import * as React from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";

import { AUTONOMY, AutonomyDial, stopFrom } from "@/components/autonomy-dial";
import { CampaignActivity } from "@/components/campaign-activity";
import { CampaignComposer, type Step } from "@/components/campaign-composer";
import { PageHeader } from "@/components/page-header";
import { PageLayout } from "@/components/page-layout";
import { PersonCell } from "@/components/person-cell";
import { ScoreBar } from "@/components/score-bar";
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
import { Segmented } from "@/components/ui/segmented";
import { Sheet } from "@/components/ui/sheet";
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
import { longAgo } from "@/lib/format";
import { toTargeting, type Targeting } from "@/lib/targeting";
import { cn } from "@/lib/utils";

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

interface Enrollment {
  id: string;
  contact_id: string;
  contact_name: string;
  contact_title: string | null;
  contact_avatar: string | null;
  score: number;
  score_rationale: string | null;
  state: string;
  current_step: number;
}

interface AgentRun {
  role: string;
  summary: string;
  created_at: string;
}

const IN_SEQUENCE = ["active", "awaiting_approval", "scheduled", "awaiting_reply"];
const STAGES: { value: string; label: string; match: (s: string) => boolean }[] = [
  { value: "all", label: "All", match: () => true },
  { value: "proposed", label: "Proposed", match: (s) => s === "proposed" },
  { value: "in_sequence", label: "In sequence", match: (s) => IN_SEQUENCE.includes(s) },
  { value: "handed_off", label: "Handed off", match: (s) => s === "handed_off" },
  { value: "opted_out", label: "Opted out", match: (s) => s === "opted_out" },
];

export function CampaignDetailPage() {
  const { id } = useParams<{ id: string }>();
  const { data: campaignData } = useCampaign(id ?? "");
  const campaign = campaignData as Campaign | undefined;
  const { data: enrollments } = useCampaignEnrollments(id ?? "");
  const { data: pool } = useContacts();
  const { data: runs } = useCampaignRuns(id ?? "");
  const rankCampaign = useRankCampaign(id ?? "");
  const bulkApprove = useBulkApprove();
  const updateCampaign = useUpdateCampaign();
  const sourceNow = useSourceNow();

  const [stage, setStage] = React.useState("all");
  const [selected, setSelected] = React.useState<Set<string>>(new Set());
  const [setupOpen, setSetupOpen] = React.useState(false);
  const [setupTab, setSetupTab] = React.useState("design");
  const [activityOpen, setActivityOpen] = React.useState(false);
  const busy = rankCampaign.isPending || bulkApprove.isPending;

  // Editable copies of the audience + sequence, autosaved via PATCH (edited in the Setup drawer).
  const [criteria, setCriteria] = React.useState<Targeting | null>(null);
  const [steps, setSteps] = React.useState<Step[] | null>(null);
  const dirty = React.useRef(false);

  React.useEffect(() => {
    if (!campaign || criteria !== null) return;
    setCriteria(toTargeting(campaign.criteria));
    setSteps(
      campaign.sequence.map((s) => ({
        channel: s.channel,
        delay_days: s.delay_days,
        subject: s.subject ?? "",
        body: s.body,
      })),
    );
  }, [campaign, criteria]);

  React.useEffect(() => {
    if (!dirty.current || !criteria || !steps || !id) return;
    const t = setTimeout(() => {
      updateCampaign.mutate({ id, patch: { criteria, sequence: steps } });
    }, 800);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [criteria, steps, id]);

  const all = (enrollments ?? []) as Enrollment[];
  const count = (m: (s: string) => boolean) => all.filter((e) => m(e.state)).length;
  const activeStage = STAGES.find((s) => s.value === stage)!;
  const rows = all.filter((e) => activeStage.match(e.state));
  const seqLen = campaign?.sequence.length ?? 0;

  const inSeq = count((s) => IN_SEQUENCE.includes(s));
  const handed = count((s) => s === "handed_off");
  const opted = count((s) => s === "opted_out");
  const needsYou = count((s) => s === "awaiting_approval");
  const sourcedRun = ((runs ?? []) as AgentRun[]).find((r) => r.role === "sourcing");
  const sourcingStatus = sourcedRun
    ? `sourced ${longAgo(sourcedRun.created_at)}`
    : campaign?.next_source_at
      ? "sourcing queued"
      : "idle";

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
            <StateBadge state={campaign.status} />
            <AutonomyDial
              level={campaign.autonomy_level}
              mode={campaign.autonomy_mode}
              onChange={(patch) => updateCampaign.mutate({ id: campaign.id, patch })}
            />
            <Button variant="outline" size="sm" onClick={() => setSetupOpen(true)}>
              <Settings2 /> Setup
            </Button>
            <CampaignActions id={campaign.id} status={campaign.status} />
          </PageHeader>

          {/* agent strip — what the agent's doing + the funnel + your queue */}
          <Card className="flex flex-wrap items-center justify-between gap-4 p-4">
            <div className="flex items-center gap-3">
              <span className="grid size-9 shrink-0 place-items-center rounded-lg bg-accent text-accent-foreground">
                <Bot className="size-5" />
              </span>
              <div>
                <div className="text-sm font-semibold">
                  {AUTONOMY[stopFrom(campaign.autonomy_level, campaign.autonomy_mode)].label}
                  <span className="font-normal text-muted-foreground"> · {sourcingStatus}</span>
                </div>
                <div className="text-xs text-muted-foreground">
                  <span className="font-mono font-semibold text-foreground">{all.length}</span> sourced
                  <span className="mx-1.5">→</span>
                  <span className="font-mono font-semibold text-foreground">{inSeq}</span> in sequence
                  <span className="mx-1.5">→</span>
                  <span className="font-mono font-semibold text-foreground">{handed}</span> handed off
                  {opted > 0 && (
                    <span className="text-muted-foreground/70"> · {opted} opted out</span>
                  )}
                </div>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {needsYou > 0 && (
                <Button variant="ghost" size="sm" asChild className="text-primary">
                  <Link to="/inbox">
                    {needsYou} need you <ArrowRight />
                  </Link>
                </Button>
              )}
              <Button variant="outline" size="sm" onClick={() => setActivityOpen(true)}>
                <ScrollText /> Activity
              </Button>
              <Button variant="outline" size="sm" disabled={busy} onClick={() => void rank()}>
                <Sparkles /> Rank
              </Button>
              <Button
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
            </div>
          </Card>

          {/* pipeline stage filter */}
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div className="flex flex-wrap gap-1.5">
              {STAGES.map((s) => (
                <StageChip
                  key={s.value}
                  active={stage === s.value}
                  label={s.label}
                  count={count(s.match)}
                  onClick={() => {
                    setStage(s.value);
                    setSelected(new Set());
                  }}
                />
              ))}
            </div>
            {selected.size > 0 && (
              <Button size="sm" disabled={busy} onClick={() => void approve([...selected])}>
                Approve · {selected.size}
              </Button>
            )}
          </div>

          {/* candidate list */}
          <Card>
            {!enrollments ? (
              <div className="space-y-3 p-5">
                <Skeleton className="h-10" />
                <Skeleton className="h-10" />
              </div>
            ) : rows.length === 0 ? (
              <div className="p-12 text-center text-sm text-muted-foreground">
                {stage === "proposed" || stage === "all"
                  ? "No candidates yet. The agent sources automatically — or hit Source now / Rank."
                  : "Nothing in this stage yet."}
              </div>
            ) : (
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-8" />
                    <TableHead>Candidate</TableHead>
                    <TableHead>Fit</TableHead>
                    <TableHead>Stage</TableHead>
                    <TableHead>Progress</TableHead>
                    <TableHead />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((e) => {
                    const proposed = e.state === "proposed";
                    return (
                      <TableRow key={e.id}>
                        <TableCell className="w-8">
                          {proposed && (
                            <Checkbox checked={selected.has(e.id)} onCheckedChange={() => toggle(e.id)} />
                          )}
                        </TableCell>
                        <TableCell>
                          <Link to={`/people/${e.contact_id}`} className="inline-block rounded-md transition-opacity hover:opacity-80">
                            <PersonCell name={e.contact_name} subtitle={e.contact_title ?? undefined} imageSrc={e.contact_avatar ?? undefined} />
                          </Link>
                        </TableCell>
                        <TableCell>
                          <ScoreBar value={e.score} />
                        </TableCell>
                        <TableCell>
                          <StateBadge state={e.state} />
                        </TableCell>
                        <TableCell className="max-w-xs text-xs text-muted-foreground">
                          {proposed ? (
                            <span className="line-clamp-1">{e.score_rationale}</span>
                          ) : IN_SEQUENCE.includes(e.state) ? (
                            `Touch ${Math.min(e.current_step + 1, seqLen)} of ${seqLen}`
                          ) : (
                            "—"
                          )}
                        </TableCell>
                        <TableCell className="text-right">
                          {proposed ? (
                            <Button variant="outline" size="sm" disabled={busy} onClick={() => void approve([e.id])}>
                              Approve
                            </Button>
                          ) : e.state === "awaiting_reply" || e.state === "awaiting_approval" ? (
                            <Button variant="ghost" size="sm" asChild>
                              <Link to="/inbox">
                                Open <ArrowRight />
                              </Link>
                            </Button>
                          ) : null}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            )}
          </Card>
        </>
      )}

      {/* ---- Setup drawer: audience + sequence + autonomy ---- */}
      <Sheet
        open={setupOpen}
        onClose={() => setSetupOpen(false)}
        title="Campaign setup"
        description={campaign?.name}
        className="max-w-5xl"
      >
        <div className="space-y-5 p-5">
          <div className="flex items-center justify-between gap-3">
            <Segmented
              value={setupTab}
              onChange={setSetupTab}
              options={[
                { value: "design", label: "Audience & sequence" },
                { value: "autonomy", label: "Autonomy" },
              ]}
            />
            {setupTab === "design" && (
              <span className="text-xs text-muted-foreground">
                {updateCampaign.isPending ? "Saving…" : dirty.current ? "Changes saved" : "Saves automatically"}
              </span>
            )}
          </div>

          {setupTab === "autonomy" ? (
            campaign ? (
              <div className="max-w-xl space-y-3">
                <AutonomyDial
                  variant="cards"
                  level={campaign.autonomy_level}
                  mode={campaign.autonomy_mode}
                  onChange={(patch) => updateCampaign.mutate({ id: campaign.id, patch })}
                />
                <p className="text-xs text-muted-foreground">
                  Daily send caps and connected channels are managed in{" "}
                  <Link to="/settings" className="font-medium text-primary hover:underline">
                    Settings
                  </Link>
                  .
                </p>
              </div>
            ) : null
          ) : criteria && steps ? (
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
      </Sheet>

      {/* ---- Activity drawer: the agent run feed ---- */}
      <Sheet
        open={activityOpen}
        onClose={() => setActivityOpen(false)}
        title="Agent activity"
        description={campaign?.name}
        className="max-w-xl"
      >
        <div className="p-5">{campaign && <CampaignActivity campaignId={campaign.id} />}</div>
      </Sheet>
    </PageLayout>
  );
}

function StageChip({
  active,
  label,
  count,
  onClick,
}: {
  active: boolean;
  label: string;
  count: number;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors",
        active
          ? "border-primary bg-primary text-primary-foreground"
          : "border-border bg-card text-muted-foreground hover:text-foreground",
      )}
    >
      {label}
      <span className={cn("font-mono tabular-nums", active ? "text-primary-foreground/80" : "text-muted-foreground/70")}>
        {count}
      </span>
    </button>
  );
}

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
