import {
  ArrowDown,
  ArrowUp,
  Ban,
  Clock,
  Copy,
  CornerDownRight,
  Flag,
  Plus,
  Reply,
  Sparkles,
  Target,
  Trash2,
  Users,
} from "lucide-react";
import * as React from "react";

import { ChannelIcon } from "@/components/brand-icons";
import { TargetingEditor } from "@/components/targeting-editor";
import { Badge } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Segmented } from "@/components/ui/segmented";
import { Textarea } from "@/components/ui/textarea";
import { useRegenerateMessage } from "@/lib/api/queries";
import { evaluateFit, type Targeting } from "@/lib/targeting";
import { cn } from "@/lib/utils";

export type Channel = "email" | "linkedin";
export interface Step {
  channel: Channel;
  delay_days: number;
  subject: string;
  body: string;
}
export type { Targeting };

type ContactLike = Parameters<typeof evaluateFit>[0];

const TOKENS = ["{first_name}", "{company}", "{title}"];
const DEFAULT_BODY = "Hi {first_name}, came across your work at {company} — open to a quick chat?";
const SAMPLE = { first: "Jane", name: "Jane Doe", company: "Acme", title: "Senior Backend Engineer" };
const fill = (t: string) =>
  (t || "")
    .replace(/\{first_name\}/g, SAMPLE.first)
    .replace(/\{name\}/g, SAMPLE.name)
    .replace(/\{company\}/g, SAMPLE.company)
    .replace(/\{title\}/g, SAMPLE.title);

const NODE = "w-[380px] max-w-full rounded-xl border bg-card text-left transition-shadow";
const SELECTED = "border-primary ring-2 ring-ring/30";

/**
 * The visual campaign composer: an audience node + sequence timeline on the left, and a contextual
 * inspector (audience criteria or step editor) on the right. Fully controlled — the parent owns
 * `criteria`/`steps` and persists changes (create on the builder, autosave on the detail page).
 */
export function CampaignComposer({
  criteria,
  steps,
  pool,
  onCriteriaChange,
  onStepsChange,
}: {
  criteria: Targeting;
  steps: Step[];
  pool: ContactLike[] | undefined;
  onCriteriaChange: (c: Targeting) => void;
  onStepsChange: (s: Step[]) => void;
}) {
  const [sel, setSel] = React.useState<"audience" | number | null>("audience");

  const match = React.useMemo(() => {
    const p = pool ?? [];
    const n = p.filter((c) => evaluateFit(c, criteria).matched).length;
    return { n, total: p.length };
  }, [pool, criteria]);

  const cumulativeDay = (i: number) => steps.slice(0, i + 1).reduce((d, s) => d + s.delay_days, 0);

  function addStep(at: number) {
    onStepsChange([
      ...steps.slice(0, at),
      { channel: "email", delay_days: 3, subject: "", body: "" },
      ...steps.slice(at),
    ]);
    setSel(at);
  }
  function updateStep(i: number, patch: Partial<Step>) {
    onStepsChange(steps.map((step, idx) => (idx === i ? { ...step, ...patch } : step)));
  }
  function removeStep(i: number) {
    onStepsChange(steps.filter((_, idx) => idx !== i));
    setSel(null);
  }
  function duplicate(i: number) {
    onStepsChange([...steps.slice(0, i + 1), { ...steps[i] }, ...steps.slice(i + 1)]);
    setSel(i + 1);
  }
  function move(i: number, dir: -1 | 1) {
    const j = i + dir;
    if (j < 0 || j >= steps.length) return;
    const next = [...steps];
    [next[i], next[j]] = [next[j], next[i]];
    onStepsChange(next);
    setSel(j);
  }

  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_380px]">
      {/* ---- flow ---- */}
      <div className="flex flex-col items-center py-2">
        <button
          onClick={() => setSel("audience")}
          className={cn(NODE, "p-4", sel === "audience" ? SELECTED : "border-border")}
        >
          <div className="flex items-center gap-1.5 font-mono text-[0.65rem] font-semibold uppercase tracking-wider text-muted-foreground">
            <Target className="size-3.5" /> Audience
          </div>
          <div className="mt-1 flex items-center justify-between">
            <div className="font-display font-semibold">Who we reach</div>
            <span className="rounded-full bg-accent px-2.5 py-0.5 text-xs font-semibold text-accent-foreground">
              ~{match.n} match
            </span>
          </div>
          <div className="mt-2 flex flex-wrap gap-1">
            {[...criteria.titles, ...criteria.companies, ...criteria.skills, ...criteria.industries, ...criteria.locations]
              .slice(0, 6)
              .map((t) => (
                <Badge key={t} variant="secondary">
                  {t}
                </Badge>
              ))}
          </div>
        </button>

        {steps.map((s, i) => {
          const switched = i > 0 && steps[i - 1].channel !== s.channel;
          return (
            <React.Fragment key={i}>
              <Connector
                delay={s.delay_days}
                switchedTo={switched ? s.channel : undefined}
                onAdd={() => addStep(i)}
              />
              <div className={cn(NODE, sel === i ? SELECTED : "border-border")}>
                <button onClick={() => setSel(i)} className="block w-full p-4 text-left">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-1.5 font-mono text-[0.65rem] font-semibold uppercase tracking-wider text-muted-foreground">
                      <ChannelIcon channel={s.channel} className="size-3.5" /> Touchpoint {i + 1} ·{" "}
                      {s.channel}
                    </div>
                    <span className="rounded-full border border-border px-2 py-0.5 text-[0.65rem] font-medium text-muted-foreground">
                      Day {cumulativeDay(i)}
                    </span>
                  </div>
                  <div className="mt-1.5 truncate font-semibold">
                    {s.channel === "email" ? s.subject || "(no subject)" : "LinkedIn message"}
                  </div>
                  <div className="truncate text-xs text-muted-foreground">
                    {s.body || "Empty message"}
                  </div>
                </button>
              </div>
            </React.Fragment>
          );
        })}

        <Connector onAdd={() => addStep(steps.length)} addLabel="Add step" />

        <div className="w-[380px] max-w-full rounded-xl border border-border bg-card p-4">
          <div className="flex items-center gap-1.5 font-mono text-[0.65rem] font-semibold uppercase tracking-wider text-muted-foreground">
            <Flag className="size-3.5" /> When the sequence ends
          </div>
          <ul className="mt-2.5 space-y-2 text-sm">
            <Rule icon={Reply} accent>
              Any reply → moves to your <b className="font-semibold">Inbox</b> (you take over)
            </Rule>
            <Rule icon={Ban}>Opt-out → stops automatically</Rule>
            <Rule icon={Clock}>No reply after the last touchpoint → marked no-response</Rule>
          </ul>
        </div>
      </div>

      {/* ---- inspector ---- */}
      <Card className="h-fit p-5 lg:sticky lg:top-2">
        {sel === "audience" ? (
          <div className="space-y-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2 font-display font-semibold">
                <Users className="size-4 text-muted-foreground" /> Audience
              </div>
              <span className="text-xs text-muted-foreground">
                ~{match.n} of {match.total}
              </span>
            </div>
            <TargetingEditor value={criteria} onChange={onCriteriaChange} />
            <p className="text-xs text-muted-foreground">
              The Evaluator ranks every matching contact by fit before anyone is enrolled.
            </p>
          </div>
        ) : typeof sel === "number" && steps[sel] ? (
          <StepEditor
            step={steps[sel]}
            index={sel}
            count={steps.length}
            onChange={(p) => updateStep(sel, p)}
            onDelete={() => removeStep(sel)}
            onDuplicate={() => duplicate(sel)}
            onMove={(d) => move(sel, d)}
          />
        ) : (
          <div className="py-10 text-center text-sm text-muted-foreground">
            Select a node to edit it.
          </div>
        )}
      </Card>
    </div>
  );
}

function Rule({
  icon: Icon,
  accent,
  children,
}: {
  icon: typeof Reply;
  accent?: boolean;
  children: React.ReactNode;
}) {
  return (
    <li className="flex items-start gap-2.5">
      <span
        className={cn(
          "mt-0.5 grid size-5 shrink-0 place-items-center rounded-md",
          accent ? "bg-accent text-accent-foreground" : "bg-secondary text-muted-foreground",
        )}
      >
        <Icon className="size-3" />
      </span>
      <span className="text-muted-foreground">{children}</span>
    </li>
  );
}

function Connector({
  delay,
  switchedTo,
  onAdd,
  addLabel,
}: {
  delay?: number;
  switchedTo?: Channel;
  onAdd: () => void;
  addLabel?: string;
}) {
  return (
    <div className="flex flex-col items-center">
      <div className="h-4 w-px bg-border" />
      <div className="flex flex-wrap items-center justify-center gap-2">
        {delay !== undefined && (
          <span className="rounded-full border border-border bg-card px-2.5 py-0.5 text-[0.7rem] font-medium text-muted-foreground">
            {delay === 0 ? "Send immediately" : `Wait ${delay} day${delay > 1 ? "s" : ""}`}
            <span className="opacity-60"> · stop if they reply</span>
          </span>
        )}
        {switchedTo && (
          <span className="inline-flex items-center gap-1 rounded-full border border-border bg-card px-2 py-0.5 text-[0.7rem] font-medium text-muted-foreground">
            <CornerDownRight className="size-3" /> switches to{" "}
            {switchedTo === "linkedin" ? "LinkedIn" : "Email"}
          </span>
        )}
        <button
          onClick={onAdd}
          className="inline-flex items-center gap-1.5 rounded-full border border-dashed border-border px-2 py-1 text-xs text-muted-foreground transition-colors hover:border-primary hover:text-primary"
        >
          <Plus className="size-3.5" /> {addLabel}
        </button>
      </div>
      <div className="h-4 w-px bg-border" />
    </div>
  );
}

function StepEditor({
  step,
  index,
  count,
  onChange,
  onDelete,
  onDuplicate,
  onMove,
}: {
  step: Step;
  index: number;
  count: number;
  onChange: (patch: Partial<Step>) => void;
  onDelete: () => void;
  onDuplicate: () => void;
  onMove: (dir: -1 | 1) => void;
}) {
  const regen = useRegenerateMessage();
  function regenerate() {
    regen.mutate(
      { body: step.body, objective: "", channel: step.channel },
      { onSuccess: (r) => onChange({ body: r.body }) },
    );
  }
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 font-display font-semibold">
          <ChannelIcon channel={step.channel} className="size-4 text-muted-foreground" /> Touchpoint{" "}
          {index + 1}
        </div>
        <div className="flex items-center gap-0.5">
          <IconBtn title="Move up" disabled={index === 0} onClick={() => onMove(-1)}>
            <ArrowUp className="size-4" />
          </IconBtn>
          <IconBtn title="Move down" disabled={index === count - 1} onClick={() => onMove(1)}>
            <ArrowDown className="size-4" />
          </IconBtn>
          <IconBtn title="Duplicate" onClick={onDuplicate}>
            <Copy className="size-4" />
          </IconBtn>
          <IconBtn title="Remove" onClick={onDelete}>
            <Trash2 className="size-4" />
          </IconBtn>
        </div>
      </div>

      <div>
        <Label className="block">Channel</Label>
        <Segmented
          className="mt-1.5"
          value={step.channel}
          onChange={(v) => onChange({ channel: v as Channel })}
          options={[
            { value: "email", label: "Email" },
            { value: "linkedin", label: "LinkedIn" },
          ]}
        />
      </div>

      <div>
        <Label htmlFor="delay">Wait before sending</Label>
        <div className="mt-1.5 flex items-center gap-2">
          <Input
            id="delay"
            type="number"
            min={0}
            className="w-24"
            value={step.delay_days}
            onChange={(e) => onChange({ delay_days: Math.max(0, Number(e.target.value) || 0) })}
          />
          <span className="text-sm text-muted-foreground">days after the previous touchpoint</span>
        </div>
      </div>

      {step.channel === "email" && (
        <div>
          <Label htmlFor="subject">Subject</Label>
          <Input
            id="subject"
            className="mt-1.5"
            value={step.subject}
            onChange={(e) => onChange({ subject: e.target.value })}
          />
        </div>
      )}

      <div>
        <div className="flex items-center justify-between">
          <Label htmlFor="body">Message</Label>
          <div className="flex items-center gap-2">
            <button
              type="button"
              disabled={regen.isPending}
              onClick={regenerate}
              className="inline-flex items-center gap-1 rounded-md border border-border px-2 py-0.5 text-[0.7rem] font-medium text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground disabled:opacity-50"
            >
              <Sparkles className="size-3" />
              {regen.isPending ? "Regenerating…" : "Regenerate"}
            </button>
            <button
              type="button"
              onClick={() => onChange({ body: DEFAULT_BODY })}
              className="rounded-md border border-border px-2 py-0.5 text-[0.7rem] font-medium text-muted-foreground transition-colors hover:text-foreground"
            >
              Reset
            </button>
          </div>
        </div>
        <Textarea
          id="body"
          rows={5}
          className="mt-1.5"
          value={step.body}
          onChange={(e) => onChange({ body: e.target.value })}
        />
        <div className="mt-2 flex flex-wrap items-center gap-1.5">
          <span className="text-[0.7rem] text-muted-foreground">Insert:</span>
          {TOKENS.map((t) => (
            <button
              key={t}
              onClick={() =>
                onChange({
                  body: `${step.body}${!step.body || step.body.endsWith(" ") ? "" : " "}${t}`,
                })
              }
              className="rounded-md border border-border bg-secondary/40 px-2 py-0.5 font-mono text-[0.7rem] text-muted-foreground transition-colors hover:text-foreground"
            >
              {t}
            </button>
          ))}
        </div>
      </div>

      <div className="rounded-lg border border-border bg-secondary/30 p-3">
        <div className="flex items-center gap-1.5 font-mono text-[0.6rem] font-semibold uppercase tracking-wider text-muted-foreground">
          <Sparkles className="size-3" /> Preview · Jane Doe
        </div>
        {step.channel === "email" && (
          <div className="mt-1.5 text-xs text-muted-foreground">To: jane@acme.com</div>
        )}
        {step.channel === "email" && step.subject && (
          <div className="mt-0.5 text-sm font-semibold">{fill(step.subject)}</div>
        )}
        <p className="mt-1 whitespace-pre-line text-sm text-foreground">
          {fill(step.body) || "Your message preview appears here."}
        </p>
      </div>
    </div>
  );
}

function IconBtn({
  title,
  disabled,
  onClick,
  children,
}: {
  title: string;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      title={title}
      disabled={disabled}
      onClick={onClick}
      className="grid size-8 place-items-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-40"
    >
      {children}
    </button>
  );
}
