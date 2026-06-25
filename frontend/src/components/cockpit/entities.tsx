import { Loader2, Users } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { useApplyAudience } from "@/lib/api/queries";
import { cn } from "@/lib/utils";

// ---------------------------------------------------------------------------
// Typed-UI entities arrive as loosely-typed JSON (catalog §12). Each is
// { type, id, data, action? }; we narrow the bits each renderer needs with the
// small helpers below rather than reaching for `any`.
// ---------------------------------------------------------------------------

export interface Entity {
  type: string;
  id: string;
  data: Record<string, unknown>;
  action?: EntityAction | null;
}

interface EntityAction {
  kind?: string;
  label?: string;
  params?: Record<string, unknown>;
}

type Json = Record<string, unknown>;

function asObject(v: unknown): Json {
  return v && typeof v === "object" && !Array.isArray(v) ? (v as Json) : {};
}

function asEntity(v: unknown): Entity {
  const o = asObject(v);
  return {
    type: typeof o.type === "string" ? o.type : "unknown",
    id: typeof o.id === "string" ? o.id : "",
    data: asObject(o.data),
    action: o.action ? (asObject(o.action) as EntityAction) : null,
  };
}

function str(v: unknown): string | null {
  return typeof v === "string" ? v : null;
}

function num(v: unknown): number | null {
  return typeof v === "number" ? v : null;
}

// ---------------------------------------------------------------------------
// funnel — four labeled stat chips
// ---------------------------------------------------------------------------

const FUNNEL_STAGES: { key: string; label: string }[] = [
  { key: "sourced", label: "Sourced" },
  { key: "contacted", label: "Contacted" },
  { key: "replied", label: "Replied" },
  { key: "handed_off", label: "Handed off" },
];

function FunnelEntity({ entity }: { entity: Entity }) {
  return (
    <div className="grid grid-cols-4 gap-2">
      {FUNNEL_STAGES.map((s) => (
        <div
          key={s.key}
          className="rounded-lg border border-border bg-secondary/30 px-3 py-2 text-center"
        >
          <p className="font-display text-xl font-bold tabular-nums text-foreground">
            {num(entity.data[s.key]) ?? 0}
          </p>
          <p className="font-mono text-[0.6rem] uppercase tracking-wide text-muted-foreground">
            {s.label}
          </p>
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// candidate_list — compact stacked rows: name · title · score badge
// ---------------------------------------------------------------------------

interface CandidateRow {
  name: string;
  title: string | null;
  score: number | null;
}

function asCandidates(v: unknown): CandidateRow[] {
  if (!Array.isArray(v)) return [];
  return v.map((raw) => {
    const o = asObject(raw);
    return {
      name: str(o.name) ?? str(o.contact_name) ?? "Unknown",
      title: str(o.title) ?? str(o.contact_title),
      score: num(o.score),
    };
  });
}

function CandidateListEntity({ entity }: { entity: Entity }) {
  const rows = asCandidates(entity.data.candidates ?? entity.data.items);
  if (rows.length === 0) {
    return (
      <p className="text-xs text-muted-foreground">No candidates to show.</p>
    );
  }
  return (
    <div className="divide-y divide-border rounded-lg border border-border bg-card">
      {rows.map((c, i) => (
        <div key={i} className="flex items-center justify-between gap-3 px-3 py-2">
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-foreground">{c.name}</p>
            {c.title && (
              <p className="truncate text-xs text-muted-foreground">{c.title}</p>
            )}
          </div>
          {c.score !== null && (
            <Badge variant="secondary" className="shrink-0 font-mono tabular-nums">
              {Math.round(c.score)}
            </Badge>
          )}
        </div>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------------------
// audience_preview — criteria summary + estimate + Apply button
// ---------------------------------------------------------------------------

function summarizeCriteria(criteria: Json): string[] {
  const parts: string[] = [];
  for (const [key, value] of Object.entries(criteria)) {
    if (Array.isArray(value) && value.length > 0) {
      const label = key.replace(/_/g, " ");
      parts.push(`${label}: ${value.map(String).join(", ")}`);
    } else if (typeof value === "string" && value.trim().length > 0) {
      parts.push(`${key.replace(/_/g, " ")}: ${value}`);
    }
  }
  return parts;
}

function AudiencePreviewEntity({ entity }: { entity: Entity }) {
  const applyAudience = useApplyAudience();
  const criteria = asObject(entity.data.criteria);
  const summary = summarizeCriteria(criteria);
  const estimate = num(entity.data.estimate);
  const params = asObject(entity.action?.params);
  const campaignId = str(params.campaign_id);
  const applyCriteria = asObject(params.criteria ?? criteria);
  const canApply = !!campaignId;

  function apply() {
    if (!campaignId) return;
    applyAudience.mutate(
      { campaign_id: campaignId, criteria: applyCriteria },
      { onSuccess: () => toast.success("Audience applied") },
    );
  }

  return (
    <Card className="bg-secondary/20">
      <CardHeader>
        <CardTitle className="flex items-center gap-2 text-sm">
          <Users className="size-4 text-primary" />
          {str(entity.data.title) ?? "Audience preview"}
        </CardTitle>
        {estimate !== null && (
          <Badge variant="accent" className="font-mono tabular-nums">
            ~{estimate} match{estimate === 1 ? "" : "es"}
          </Badge>
        )}
      </CardHeader>
      <CardContent className="space-y-3">
        {summary.length > 0 ? (
          <ul className="space-y-1 text-xs text-muted-foreground">
            {summary.map((line, i) => (
              <li key={i}>{line}</li>
            ))}
          </ul>
        ) : (
          <p className="text-xs text-muted-foreground">No criteria specified.</p>
        )}
        <Button
          size="sm"
          disabled={!canApply || applyAudience.isPending}
          onClick={() => apply()}
        >
          {applyAudience.isPending && <Loader2 className="animate-spin" />}
          {str(entity.action?.label) ?? "Apply audience"}
        </Button>
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// GenericEntity — titled key/value card (fallback for unknown types)
// ---------------------------------------------------------------------------

function renderValue(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (Array.isArray(v)) return v.map(String).join(", ");
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function GenericEntity({ entity }: { entity: Entity }) {
  const entries = Object.entries(entity.data);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm capitalize">
          {entity.type.replace(/_/g, " ")}
        </CardTitle>
      </CardHeader>
      <CardContent>
        {entries.length === 0 ? (
          <p className="text-xs text-muted-foreground">No details.</p>
        ) : (
          <dl className="space-y-1.5">
            {entries.map(([key, value]) => (
              <div key={key} className="flex items-start justify-between gap-3 text-xs">
                <dt className="font-mono uppercase tracking-wide text-muted-foreground">
                  {key.replace(/_/g, " ")}
                </dt>
                <dd className="min-w-0 text-right font-medium text-foreground">
                  {renderValue(value)}
                </dd>
              </div>
            ))}
          </dl>
        )}
      </CardContent>
    </Card>
  );
}

// ---------------------------------------------------------------------------
// registry + <ChatEntities />
// ---------------------------------------------------------------------------

const REGISTRY: Record<string, React.ComponentType<{ entity: Entity }>> = {
  funnel: FunnelEntity,
  candidate_list: CandidateListEntity,
  audience_preview: AudiencePreviewEntity,
};

function EntityRenderer({ entity }: { entity: Entity }) {
  const Component = REGISTRY[entity.type] ?? GenericEntity;
  return <Component entity={entity} />;
}

export function ChatEntities({
  entities,
  className,
}: {
  entities: unknown;
  className?: string;
}) {
  const list = Array.isArray(entities) ? entities.map(asEntity) : [];
  if (list.length === 0) return null;
  return (
    <div className={cn("space-y-2", className)}>
      {list.map((e, i) => (
        <EntityRenderer key={e.id || i} entity={e} />
      ))}
    </div>
  );
}
