import { Send } from "lucide-react";
import * as React from "react";
import { toast } from "sonner";

import { ChannelIcon } from "@/components/brand-icons";
import { PersonCell } from "@/components/person-cell";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useApproveMessage, useEditMessage } from "@/lib/api/queries";
import { cn } from "@/lib/utils";

/** A drafted outbound message awaiting human approval (ApprovalOut). */
export interface Approval {
  id: string;
  enrollment_id: string;
  channel: string;
  subject: string | null;
  body: string;
  created_at: string | null;
  contact_name: string;
  contact_title: string | null;
  contact_avatar: string | null;
  score: number;
  step: number;
}

/** The draft-approval editor for one queued message — edit inline, then approve & send. */
export function ApprovalDetail({
  approval,
  onApproved,
  className,
}: {
  approval: Approval;
  onApproved: () => void;
  className?: string;
}) {
  const editMessage = useEditMessage();
  const approveMessage = useApproveMessage();
  const [subject, setSubject] = React.useState(approval.subject ?? "");
  const [bodyText, setBodyText] = React.useState(approval.body);
  const busy = editMessage.isPending || approveMessage.isPending;

  // Reset the editor when the user switches to a different draft.
  React.useEffect(() => {
    setSubject(approval.subject ?? "");
    setBodyText(approval.body);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [approval.id]);

  const dirty = subject !== (approval.subject ?? "") || bodyText !== approval.body;

  function save() {
    editMessage.mutate(
      { messageId: approval.id, subject, body: bodyText },
      { onSuccess: () => toast.success("Draft saved") },
    );
  }
  async function approve() {
    try {
      if (dirty) await editMessage.mutateAsync({ messageId: approval.id, subject, body: bodyText });
      await approveMessage.mutateAsync(approval.id);
      toast.success(`Sent to ${approval.contact_name}`);
      onApproved();
    } catch {
      toast.error("Couldn't send");
    }
  }

  return (
    <div className={cn("flex min-h-0 flex-col", className)}>
      <div className="flex items-center justify-between gap-3 border-b border-border px-6 py-3">
        <PersonCell
          name={approval.contact_name}
          subtitle={approval.contact_title ?? undefined}
          imageSrc={approval.contact_avatar ?? undefined}
        />
        <span className="inline-flex items-center gap-1.5 rounded-md border border-border px-2 py-1 text-xs font-medium text-muted-foreground">
          <ChannelIcon channel={approval.channel} className="size-3.5" /> Touchpoint {approval.step + 1}
        </span>
      </div>

      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-5">
        {approval.channel === "email" && (
          <div>
            <label className="mb-1 block font-mono text-[0.6rem] font-semibold uppercase tracking-wider text-muted-foreground">
              Subject
            </label>
            <Input value={subject} onChange={(e) => setSubject(e.target.value)} />
          </div>
        )}
        <div>
          <label className="mb-1 block font-mono text-[0.6rem] font-semibold uppercase tracking-wider text-muted-foreground">
            Message
          </label>
          <Textarea rows={12} value={bodyText} onChange={(e) => setBodyText(e.target.value)} />
        </div>
      </div>

      <div className="flex items-center justify-end gap-2 border-t border-border px-6 py-3">
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
  );
}
