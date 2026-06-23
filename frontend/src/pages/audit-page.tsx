import { Ban, CheckCircle2, Link2, LogIn, Reply, Shield, UserCheck, UserPlus } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import { DataError } from "@/components/data-error";
import { PageHeader } from "@/components/page-header";
import { PageLayout } from "@/components/page-layout";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { useAudit } from "@/lib/api/queries";
import { longAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

const ACTION: Record<string, { label: string; icon: LucideIcon; tone: string }> = {
  "message.approved": { label: "Approved", icon: CheckCircle2, tone: "text-primary bg-accent" },
  "reply.received": { label: "Reply", icon: Reply, tone: "text-muted-foreground bg-secondary" },
  "reply.sent": { label: "Replied", icon: Reply, tone: "text-primary bg-accent" },
  "enrollment.handed_off": { label: "Hand-off", icon: UserCheck, tone: "text-primary bg-accent" },
  "enrollment.opted_out": { label: "Opt-out", icon: Ban, tone: "text-muted-foreground bg-secondary" },
  "auth.login": { label: "Sign-in", icon: LogIn, tone: "text-muted-foreground bg-secondary" },
  "connection.connected": { label: "Connection", icon: Link2, tone: "text-muted-foreground bg-secondary" },
  "member.invited": { label: "Member", icon: UserPlus, tone: "text-muted-foreground bg-secondary" },
};

export function AuditPage() {
  const { data, isLoading, isError, refetch } = useAudit();

  return (
    <PageLayout width="narrow">
      <PageHeader
        eyebrow="Compliance"
        title="Audit log"
        description="An append-only record of every consequential action across the organization."
      />
      {isError ? (
        <DataError onRetry={() => void refetch()} />
      ) : isLoading ? (
        <Skeleton className="h-96" />
      ) : (
        <Card>
          <CardContent className="py-1">
            {(data ?? []).map((e) => {
              const meta = ACTION[e.action] ?? { label: e.action, icon: Shield, tone: "text-muted-foreground bg-secondary" };
              const Icon = meta.icon;
              return (
                <div key={e.id} className="flex items-center gap-3 border-b border-border/50 py-3 last:border-0">
                  <span className={cn("grid size-8 shrink-0 place-items-center rounded-lg", meta.tone)}>
                    <Icon className="size-4" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-foreground">{e.summary}</div>
                    <div className="text-xs text-muted-foreground">
                      {e.actor_name ?? "System"}
                      <span className="mx-1.5 font-mono">·</span>
                      <span className="font-mono">{e.action}</span>
                    </div>
                  </div>
                  <span className="shrink-0 text-xs text-muted-foreground">{longAgo(e.created_at)}</span>
                </div>
              );
            })}
            {data && data.length === 0 && (
              <p className="py-10 text-center text-sm text-muted-foreground">No activity recorded yet.</p>
            )}
          </CardContent>
        </Card>
      )}
    </PageLayout>
  );
}
