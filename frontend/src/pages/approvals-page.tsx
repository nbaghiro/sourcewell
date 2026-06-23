import { CheckCircle2, Send } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { ChannelIcon } from "@/components/brand-icons";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PageLayout } from "@/components/page-layout";
import { PersonCell } from "@/components/person-cell";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { useApprovals, useApproveMessage, useEditMessage } from "@/lib/api/queries";
import { initials } from "@/lib/format";
import { cn } from "@/lib/utils";

export function ApprovalsPage() {
  const { data: items } = useApprovals();
  const editMessage = useEditMessage();
  const approveMessage = useApproveMessage();
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [subject, setSubject] = React.useState("");
  const [bodyText, setBodyText] = React.useState("");
  const busy = editMessage.isPending || approveMessage.isPending;

  const selected = items?.find((a) => a.id === selectedId) ?? null;

  React.useEffect(() => {
    if (items && items.length > 0 && (!selectedId || !items.some((a) => a.id === selectedId))) {
      setSelectedId(items[0].id);
    }
  }, [items, selectedId]);

  React.useEffect(() => {
    if (selected) {
      setSubject(selected.subject ?? "");
      setBodyText(selected.body);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  const dirty = !!selected && (subject !== (selected.subject ?? "") || bodyText !== selected.body);

  function save() {
    if (!selected) return;
    editMessage.mutate(
      { messageId: selected.id, subject, body: bodyText },
      { onSuccess: () => toast.success("Draft saved") },
    );
  }
  async function approve() {
    if (!selected) return;
    try {
      if (dirty) await editMessage.mutateAsync({ messageId: selected.id, subject, body: bodyText });
      await approveMessage.mutateAsync(selected.id);
      toast.success(`Sent to ${selected.contact_name}`);
      setSelectedId(null);
    } catch {
      toast.error("Couldn't send");
    }
  }
  function skip() {
    if (!items || !selected) return;
    const idx = items.findIndex((a) => a.id === selected.id);
    const next = items[idx + 1] ?? items[idx - 1] ?? null;
    setSelectedId(next?.id ?? null);
  }

  if (items && items.length === 0) {
    return (
      <PageLayout width="narrow">
        <EmptyState icon={CheckCircle2} title="All caught up" description="No drafts waiting for approval." />
      </PageLayout>
    );
  }

  return (
    <PageLayout width="wide" fill>
      <PageHeader
        eyebrow="Review queue"
        title="Approvals"
        description="Every drafted message waits here until you approve it. Edit inline, then send."
      />
      <div className="grid min-h-0 flex-1 grid-cols-[320px_1fr] overflow-hidden rounded-xl border border-border bg-card shadow-sm">
        {/* list */}
        <div className="min-h-0 overflow-y-auto border-r border-border">
          {!items
            ? [0, 1, 2].map((i) => <Skeleton key={i} className="m-3 h-16" />)
            : items.map((a) => (
                <button
                  key={a.id}
                  onClick={() => setSelectedId(a.id)}
                  className={cn(
                    "flex w-full gap-3 border-b border-border/50 px-4 py-3.5 text-left transition-colors",
                    selectedId === a.id ? "bg-accent/60" : "hover:bg-secondary/40",
                  )}
                >
                  <Avatar className="size-9">
                    {a.contact_avatar && <AvatarImage src={a.contact_avatar} alt="" />}
                    <AvatarFallback>{initials(a.contact_name)}</AvatarFallback>
                  </Avatar>
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center justify-between gap-2">
                      <span className="truncate text-sm font-semibold">{a.contact_name}</span>
                      <span className="shrink-0 font-mono text-xs font-semibold text-primary">{a.score}</span>
                    </div>
                    <div className="mt-0.5 flex items-center gap-1.5">
                      <ChannelIcon channel={a.channel} className="size-3 shrink-0 text-muted-foreground" />
                      <span className="truncate text-xs text-muted-foreground">
                        {a.subject || a.body.replace(/\n/g, " ")}
                      </span>
                    </div>
                  </div>
                </button>
              ))}
        </div>

        {/* detail */}
        {!selected ? (
          <div className="grid place-items-center text-sm text-muted-foreground">Select a draft</div>
        ) : (
          <div className="flex min-h-0 flex-col">
            <div className="flex items-center justify-between gap-3 border-b border-border px-6 py-3">
              <PersonCell name={selected.contact_name} subtitle={selected.contact_title ?? undefined} imageSrc={selected.contact_avatar ?? undefined} />
              <span className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs font-medium text-muted-foreground">
                <ChannelIcon channel={selected.channel} className="size-3.5" /> Touch {selected.step + 1}
              </span>
            </div>

            <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-5">
              {selected.channel === "email" && (
                <div>
                  <label className="mb-1 block font-mono text-[0.6rem] font-semibold uppercase tracking-wider text-muted-foreground">Subject</label>
                  <Input value={subject} onChange={(e) => setSubject(e.target.value)} />
                </div>
              )}
              <div>
                <label className="mb-1 block font-mono text-[0.6rem] font-semibold uppercase tracking-wider text-muted-foreground">Message</label>
                <Textarea rows={12} value={bodyText} onChange={(e) => setBodyText(e.target.value)} />
              </div>
            </div>

            <div className="flex items-center justify-between gap-2 border-t border-border px-6 py-3">
              <Button variant="ghost" size="sm" onClick={skip}>Skip</Button>
              <div className="flex items-center gap-2">
                {dirty && (
                  <Button variant="outline" size="sm" disabled={busy} onClick={() => void save()}>
                    Save
                  </Button>
                )}
                <Button size="sm" disabled={busy} onClick={() => void approve()}>
                  <Send /> Approve &amp; send
                </Button>
              </div>
            </div>
          </div>
        )}
      </div>
    </PageLayout>
  );
}
