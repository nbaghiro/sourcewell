import {
  ArrowLeft,
  Bot,
  ChevronDown,
  ChevronRight,
  Loader2,
  Lock,
  Send,
  Sparkles,
} from "lucide-react";
import * as React from "react";
import { Link, useParams } from "react-router-dom";

import { ChatEntities } from "@/components/cockpit/entities";
import { Markdown } from "@/components/markdown";
import { PageHeader } from "@/components/page-header";
import { PageLayout } from "@/components/page-layout";
import { PersonCell } from "@/components/person-cell";
import { ScoreBar } from "@/components/score-bar";
import { StateBadge } from "@/components/state-badge";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Segmented } from "@/components/ui/segmented";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  useCampaign,
  streamAgentChat,
  useCampaignEnrollments,
  useCampaignFunnel,
  useCampaignRuns,
} from "@/lib/api/queries";
import { longAgo } from "@/lib/format";
import { toTargeting, type Targeting } from "@/lib/targeting";

// ---- runtime shapes (CampaignOut's criteria/sequence/field_owners are loosely typed JSON) ----

interface Campaign {
  id: string;
  name: string;
  status: string;
  objective: string | null;
  autonomy_level: string;
  criteria: Partial<Targeting>;
  sequence: SequenceStep[];
  field_owners: Record<string, string>;
}

interface SequenceStep {
  channel?: string;
  delay_days?: number;
  subject?: string | null;
  body?: string;
}

interface Funnel {
  sourced: number;
  contacted: number;
  replied: number;
  handed_off: number;
}

type Tab = "structure" | "activity" | "candidates";

const TABS = [
  { value: "structure", label: "Structure" },
  { value: "activity", label: "Activity" },
  { value: "candidates", label: "Candidates" },
];

const FUNNEL_STAGES: { key: keyof Funnel; label: string }[] = [
  { key: "sourced", label: "Sourced" },
  { key: "contacted", label: "Contacted" },
  { key: "replied", label: "Replied" },
  { key: "handed_off", label: "Handed off" },
];

export function CampaignCockpitPage() {
  const { id } = useParams<{ id: string }>();
  const cid = id ?? "";
  const { data: campaignData } = useCampaign(cid);
  const campaign = campaignData as Campaign | undefined;
  const [tab, setTab] = React.useState<Tab>("structure");

  return (
    <PageLayout width="wide" fill>
      <Link
        to={`/campaigns/${cid}`}
        className="inline-flex items-center gap-1.5 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-4" /> Campaign
      </Link>

      {!campaign ? (
        <Skeleton className="h-40" />
      ) : (
        <>
          <PageHeader
            eyebrow="Campaign"
            title={campaign.name}
            description={campaign.objective ?? "Your agent's working surface for this campaign."}
          >
            <Badge variant="accent" className="capitalize">
              <Sparkles className="size-3" />
              {campaign.autonomy_level || "manual"}
            </Badge>
          </PageHeader>

          <FunnelStrip campaignId={cid} />

          <Segmented value={tab} onChange={(v) => setTab(v as Tab)} options={TABS} />

          <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
            <div className="min-w-0 space-y-4">
              {tab === "structure" && <StructureTab campaign={campaign} />}
              {tab === "activity" && <ActivityTab campaignId={cid} />}
              {tab === "candidates" && <CandidatesTab campaignId={cid} />}
            </div>
            <ChatPanel campaignId={cid} />
          </div>
        </>
      )}
    </PageLayout>
  );
}

// ---------------------------------------------------------------------------
// Live header strip — the funnel as a horizontal flow of stages
// ---------------------------------------------------------------------------

function FunnelStrip({ campaignId }: { campaignId: string }) {
  const { data, isLoading } = useCampaignFunnel(campaignId);
  const funnel = data as Funnel | undefined;

  return (
    <Card className="px-5 py-4">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-3">
        {FUNNEL_STAGES.map((s, i) => (
          <React.Fragment key={s.key}>
            {i > 0 && <ChevronRight className="size-4 shrink-0 text-muted-foreground" />}
            <div className="text-center">
              {isLoading ? (
                <Skeleton className="mx-auto h-8 w-10" />
              ) : (
                <p className="font-display text-2xl font-bold tabular-nums text-foreground">
                  {funnel?.[s.key] ?? 0}
                </p>
              )}
              <p className="font-mono text-[0.6rem] uppercase tracking-wide text-muted-foreground">
                {s.label}
              </p>
            </div>
          </React.Fragment>
        ))}
      </div>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Structure tab — Goal / Audience / Sequence cards with provenance chips
// ---------------------------------------------------------------------------

function ProvenanceChip({ owner }: { owner: string | undefined }) {
  if (owner === "human") {
    return (
      <Badge variant="secondary">
        <Lock className="size-3" /> You
      </Badge>
    );
  }
  // default to agent-authored when unspecified
  return (
    <Badge variant="accent">
      <Sparkles className="size-3" /> AI
    </Badge>
  );
}

function StructureCard({
  title,
  owner,
  children,
}: {
  title: string;
  owner: string | undefined;
  children: React.ReactNode;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">{title}</CardTitle>
        <ProvenanceChip owner={owner} />
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

function criteriaSummary(criteria: Partial<Targeting>): { label: string; values: string[] }[] {
  const t = toTargeting(criteria);
  const out: { label: string; values: string[] }[] = [];
  const push = (label: string, values: string[]) => {
    if (values.length > 0) out.push({ label, values });
  };
  push("Titles", t.titles);
  push("Skills", t.skills);
  push("Locations", t.locations);
  push("Companies", t.companies);
  push("Industries", t.industries);
  push("Seniority", t.seniorities);
  return out;
}

function StructureTab({ campaign }: { campaign: Campaign }) {
  const owners = campaign.field_owners ?? {};
  const summary = criteriaSummary(campaign.criteria);

  return (
    <div className="grid gap-4 md:grid-cols-3">
      <StructureCard title="Goal" owner={owners.goal ?? owners.objective}>
        <p className="text-sm text-foreground">
          {campaign.objective ?? "No objective set yet."}
        </p>
      </StructureCard>

      <StructureCard title="Audience" owner={owners.audience}>
        {summary.length === 0 ? (
          <p className="text-sm text-muted-foreground">No audience criteria yet.</p>
        ) : (
          <dl className="space-y-2">
            {summary.map((row) => (
              <div key={row.label}>
                <dt className="font-mono text-[0.6rem] uppercase tracking-wide text-muted-foreground">
                  {row.label}
                </dt>
                <dd className="mt-0.5 flex flex-wrap gap-1.5">
                  {row.values.map((v) => (
                    <Badge key={v} variant="outline" className="font-normal">
                      {v}
                    </Badge>
                  ))}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </StructureCard>

      <StructureCard title="Sequence" owner={owners.sequence}>
        {campaign.sequence.length === 0 ? (
          <p className="text-sm text-muted-foreground">No steps yet.</p>
        ) : (
          <ol className="space-y-2.5">
            {campaign.sequence.map((step, i) => (
              <li key={i} className="flex gap-3">
                <span className="grid size-6 shrink-0 place-items-center rounded-full bg-accent text-xs font-bold text-accent-foreground">
                  {i + 1}
                </span>
                <div className="min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium capitalize text-foreground">
                      {step.channel ?? "email"}
                    </span>
                    <span className="font-mono text-[0.65rem] text-muted-foreground">
                      {(step.delay_days ?? 0) === 0 ? "day 0" : `+${step.delay_days}d`}
                    </span>
                  </div>
                  {step.subject && (
                    <p className="truncate text-xs font-medium text-foreground">{step.subject}</p>
                  )}
                  {step.body && (
                    <p className="line-clamp-2 text-xs text-muted-foreground">{step.body}</p>
                  )}
                </div>
              </li>
            ))}
          </ol>
        )}
      </StructureCard>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Activity tab — the run feed as an expandable timeline (newest first)
// ---------------------------------------------------------------------------

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

function ActivityTab({ campaignId }: { campaignId: string }) {
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
            <p className="mt-2 text-sm text-muted-foreground">
              No agent runs yet for this campaign.
            </p>
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

// ---------------------------------------------------------------------------
// Candidates tab — the enrollments table
// ---------------------------------------------------------------------------

function CandidatesTab({ campaignId }: { campaignId: string }) {
  const { data: enrollments } = useCampaignEnrollments(campaignId);
  const rows = enrollments ?? [];

  return (
    <Card>
      {!enrollments ? (
        <div className="space-y-3 p-5">
          <Skeleton className="h-10" />
          <Skeleton className="h-10" />
        </div>
      ) : rows.length === 0 ? (
        <div className="p-10 text-center text-sm text-muted-foreground">
          No candidates enrolled yet.
        </div>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Candidate</TableHead>
              <TableHead>Fit</TableHead>
              <TableHead>State</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((e) => (
              <TableRow key={e.id}>
                <TableCell>
                  <Link
                    to={`/contacts/${e.contact_id}`}
                    className="inline-block rounded-md transition-opacity hover:opacity-80"
                  >
                    <PersonCell
                      name={e.contact_name}
                      subtitle={e.contact_title ?? undefined}
                      imageSrc={e.contact_avatar ?? undefined}
                    />
                  </Link>
                </TableCell>
                <TableCell>
                  <ScoreBar value={e.score} />
                </TableCell>
                <TableCell>
                  <StateBadge state={e.state} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
    </Card>
  );
}

// ---------------------------------------------------------------------------
// Chat panel — streams the agent turn token-by-token (streamAgentChat, SSE) and
// renders typed entities below each assistant turn.
// ---------------------------------------------------------------------------

interface ChatMessage {
  role: "user" | "agent";
  text: string;
  entities?: unknown;
}

const GREETING = "Hi — I'm your campaign agent. Ask how things are going or to adjust the audience.";

const SUGGESTIONS = ["How's it going?", "Find more senior people"];

function ChatPanel({ campaignId }: { campaignId: string }) {
  const [messages, setMessages] = React.useState<ChatMessage[]>([
    { role: "agent", text: GREETING },
  ]);
  const [input, setInput] = React.useState("");
  const [streaming, setStreaming] = React.useState(false);
  const scrollRef = React.useRef<HTMLDivElement>(null);

  // Mutate the trailing agent message (the one being streamed) in place.
  function patchLastAgent(patch: (m: ChatMessage) => ChatMessage) {
    setMessages((prev) => {
      const next = [...prev];
      const last = next[next.length - 1];
      if (last?.role === "agent") next[next.length - 1] = patch(last);
      return next;
    });
  }

  React.useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, streaming]);

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || streaming) return;
    setInput("");
    // append the user turn + an empty agent turn that the stream fills in.
    setMessages((m) => [...m, { role: "user", text: trimmed }, { role: "agent", text: "" }]);
    setStreaming(true);
    try {
      await streamAgentChat(
        { message: trimmed, campaign_id: campaignId },
        {
          onToken: (t) => patchLastAgent((m) => ({ ...m, text: m.text + t })),
          onDone: (entities) => patchLastAgent((m) => ({ ...m, entities })),
        },
      );
    } catch {
      patchLastAgent((m) => ({
        ...m,
        text: m.text || "Sorry — I couldn't reach the agent just now. Try again?",
      }));
    } finally {
      setStreaming(false);
    }
  }

  return (
    <Card className="flex h-[36rem] flex-col lg:sticky lg:top-4">
      <CardHeader>
        <CardTitle className="flex items-center gap-2">
          <Bot className="size-4 text-primary" />
          Copilot
        </CardTitle>
      </CardHeader>

      <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto px-5">
        {messages.map((m, i) =>
          m.role === "user" ? (
            <div key={i} className="flex justify-end">
              <div className="max-w-[80%] rounded-2xl rounded-br-sm bg-primary px-3.5 py-2 text-sm text-primary-foreground">
                {m.text}
              </div>
            </div>
          ) : (
            <div key={i} className="space-y-2">
              <div className="flex items-end gap-2">
                <Avatar className="size-7 shrink-0 rounded-full">
                  <AvatarFallback className="bg-accent text-accent-foreground">
                    <Bot className="size-3.5" />
                  </AvatarFallback>
                </Avatar>
                <div className="max-w-[80%] rounded-2xl rounded-bl-sm border border-border bg-card px-3.5 py-2 text-sm text-foreground shadow-sm">
                  {m.text ? (
                    <Markdown>{m.text}</Markdown>
                  ) : (
                    <Loader2 className="size-4 animate-spin text-muted-foreground" />
                  )}
                </div>
              </div>
              <ChatEntities entities={m.entities} className="pl-9" />
            </div>
          ),
        )}
      </div>

      <CardContent className="space-y-3 pt-4">
        <div className="flex flex-wrap gap-2">
          {SUGGESTIONS.map((s) => (
            <button
              key={s}
              type="button"
              disabled={streaming}
              onClick={() => void send(s)}
              className="rounded-full border border-border bg-secondary/40 px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground disabled:opacity-50"
            >
              {s}
            </button>
          ))}
        </div>

        <form
          className="flex items-center gap-2"
          onSubmit={(e) => {
            e.preventDefault();
            void send(input);
          }}
        >
          <Input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Ask your agent…"
            disabled={streaming}
          />
          <Button type="submit" size="icon" disabled={streaming || !input.trim()}>
            {streaming ? <Loader2 className="animate-spin" /> : <Send />}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}
