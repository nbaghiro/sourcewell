import { useQueryClient } from "@tanstack/react-query";
import { Play } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { PageHeader } from "@/components/page-header";
import { PageLayout } from "@/components/page-layout";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { api } from "@/lib/api";
import {
  type EnrollmentRow,
  useApproveEnrollment,
  useCampaignEnrollments,
  useCampaigns,
  useHandoff,
  useOptOut,
} from "@/lib/api/queries";
import { useAuth } from "@/lib/auth";
import { initials } from "@/lib/format";
import { cn } from "@/lib/utils";

const COLUMNS: { state: string; label: string }[] = [
  { state: "proposed", label: "Proposed" },
  { state: "awaiting_approval", label: "Awaiting approval" },
  { state: "scheduled", label: "Scheduled" },
  { state: "awaiting_reply", label: "Awaiting reply" },
  { state: "handed_off", label: "Handed off" },
  { state: "opted_out", label: "Opted out" },
];

export function PipelinePage() {
  const { me } = useAuth();
  const qc = useQueryClient();
  const { data: campaigns } = useCampaigns();
  const [campaignId, setCampaignId] = React.useState<string | null>(null);
  const [running, setRunning] = React.useState(false);

  React.useEffect(() => {
    if (campaigns && campaigns.length > 0 && !campaignId) {
      setCampaignId((campaigns.find((c) => c.status === "active") ?? campaigns[0]).id);
    }
  }, [campaigns, campaignId]);

  const { data: enrollments } = useCampaignEnrollments(campaignId ?? "");

  async function runDue() {
    setRunning(true);
    try {
      const r = await api<{ processed: number }>("/admin/run-due", { method: "POST" });
      toast.success(`Processed ${r.processed} due enrollment${r.processed === 1 ? "" : "s"}`);
      qc.invalidateQueries({ queryKey: ["enrollments"] });
    } finally {
      setRunning(false);
    }
  }

  return (
    <PageLayout width="wide" fill>
      <PageHeader eyebrow="Pipeline" title="Pipeline" description="Every enrolled candidate by state. The worker advances them; you act where a person is needed.">
        {me?.is_org_admin && (
          <Button variant="outline" size="sm" disabled={running} onClick={() => void runDue()}>
            <Play /> Run due now
          </Button>
        )}
      </PageHeader>

      <div className="flex flex-wrap gap-2">
        {(campaigns ?? []).map((c) => (
          <button
            key={c.id}
            onClick={() => setCampaignId(c.id)}
            className={cn(
              "rounded-full border px-3.5 py-1.5 text-sm font-medium transition-colors",
              campaignId === c.id
                ? "border-transparent bg-primary text-primary-foreground"
                : "border-border bg-card text-muted-foreground hover:text-foreground",
            )}
          >
            {c.name}
          </button>
        ))}
      </div>

      {!enrollments ? (
        <Skeleton className="h-96" />
      ) : (
        <div className="flex min-h-0 flex-1 gap-4 overflow-x-auto pb-2">
          {COLUMNS.map((col) => {
            const items = enrollments.filter((e) => e.state === col.state);
            return (
              <div key={col.state} className="flex w-64 shrink-0 flex-col gap-2.5 rounded-xl border border-border bg-secondary/30 p-3">
                <div className="flex items-center justify-between px-1 text-sm font-semibold text-foreground">
                  <span>{col.label}</span>
                  <span className="font-mono text-muted-foreground">{items.length}</span>
                </div>
                {items.slice(0, 12).map((e) => (
                  <PipelineCard key={e.id} e={e} />
                ))}
                {items.length > 12 && <div className="px-1 text-xs text-muted-foreground">+{items.length - 12} more</div>}
              </div>
            );
          })}
        </div>
      )}
    </PageLayout>
  );
}

const TERMINAL = new Set(["handed_off", "opted_out", "completed"]);

function PipelineCard({ e }: { e: EnrollmentRow }) {
  const qc = useQueryClient();
  const approve = useApproveEnrollment();
  const handoff = useHandoff();
  const optOut = useOptOut();
  const busy = approve.isPending || handoff.isPending || optOut.isPending;
  const refresh = () => qc.invalidateQueries({ queryKey: ["enrollments"] });

  return (
    <div className="rounded-lg border border-border bg-card p-3">
      <div className="flex items-center gap-2.5">
        <Avatar className="size-7">
          {e.contact_avatar && <AvatarImage src={e.contact_avatar} alt="" />}
          <AvatarFallback className="text-[0.6rem]">{initials(e.contact_name)}</AvatarFallback>
        </Avatar>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-foreground">{e.contact_name}</div>
          <div className="truncate text-[0.7rem] text-muted-foreground">{e.contact_title ?? "—"}</div>
        </div>
        <span className="font-mono text-xs font-semibold text-primary">{e.score}</span>
      </div>
      {!TERMINAL.has(e.state) && (
        <div className="mt-2.5 flex gap-1.5 border-t border-border/60 pt-2.5">
          {e.state === "proposed" ? (
            <Button
              size="sm"
              variant="outline"
              className="h-7 flex-1 text-xs"
              disabled={busy}
              onClick={() =>
                approve.mutate(e.id, { onSuccess: () => toast.success("Approved") })
              }
            >
              Approve
            </Button>
          ) : (
            <>
              <Button
                size="sm"
                variant="outline"
                className="h-7 flex-1 text-xs"
                disabled={busy}
                onClick={() =>
                  handoff.mutate(e.id, {
                    onSuccess: () => {
                      toast.success("Handed off");
                      refresh();
                    },
                  })
                }
              >
                Hand off
              </Button>
              <Button
                size="sm"
                variant="ghost"
                className="h-7 flex-1 text-xs text-muted-foreground"
                disabled={busy}
                onClick={() =>
                  optOut.mutate(e.id, {
                    onSuccess: () => {
                      toast.success("Opted out");
                      refresh();
                    },
                  })
                }
              >
                Opt out
              </Button>
            </>
          )}
        </div>
      )}
    </div>
  );
}
