import { ArrowRight, Mail } from "lucide-react";
import * as React from "react";
import { Link } from "react-router-dom";

import { LinkedInIcon, LINKEDIN_BLUE as LI_BLUE } from "@/components/brand-icons";
import { ScoreBar } from "@/components/score-bar";
import { StateBadge } from "@/components/state-badge";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Sheet } from "@/components/ui/sheet";
import { Skeleton } from "@/components/ui/skeleton";
import { useConversation } from "@/lib/api/queries";
import { initials } from "@/lib/format";
import { cn } from "@/lib/utils";

export interface CandidateEnrollment {
  id: string;
  contact_id: string;
  contact_name: string;
  contact_title: string | null;
  contact_avatar: string | null;
  score: number;
  score_rationale: string | null;
  state: string;
}

interface Conv {
  contact: { company: string | null; email: string | null; linkedin_url: string | null; skills: string[] };
  messages: { id: string; direction: string; subject: string | null; body: string; status: string }[];
}

/** A read-rich peek at one enrolled candidate without leaving the pipeline. */
export function CandidateSheet({
  enrollment,
  onClose,
  onApprove,
  approving,
}: {
  enrollment: CandidateEnrollment | null;
  onClose: () => void;
  onApprove: (id: string) => void;
  approving: boolean;
}) {
  const { data } = useConversation(enrollment?.id ?? null);
  const conv = data as Conv | undefined;
  const contact = conv?.contact;
  const messages = (conv?.messages ?? []).filter((m) => m.status !== "draft");
  const proposed = enrollment?.state === "proposed";

  return (
    <Sheet
      open={!!enrollment}
      onClose={onClose}
      title={enrollment?.contact_name}
      description={
        [enrollment?.contact_title, contact?.company].filter(Boolean).join(" · ") || undefined
      }
      className="max-w-md"
    >
      {enrollment && (
        <div className="flex h-full flex-col">
          <div className="min-h-0 flex-1 space-y-5 overflow-y-auto p-5">
            {/* identity */}
            <div className="flex items-center gap-3">
              <Avatar className="size-12">
                {enrollment.contact_avatar && <AvatarImage src={enrollment.contact_avatar} alt="" />}
                <AvatarFallback>{initials(enrollment.contact_name)}</AvatarFallback>
              </Avatar>
              <div className="flex items-center gap-2">
                <StateBadge state={enrollment.state} />
                {contact?.email && (
                  <a href={`mailto:${contact.email}`} className="grid size-7 place-items-center rounded-md border border-border text-muted-foreground transition-colors hover:text-foreground">
                    <Mail className="size-3.5" />
                  </a>
                )}
                {contact?.linkedin_url && (
                  <a href={contact.linkedin_url} target="_blank" rel="noreferrer" className="grid size-7 place-items-center rounded-md border border-border" style={{ color: LI_BLUE }}>
                    <LinkedInIcon className="size-3.5" />
                  </a>
                )}
              </div>
            </div>

            {/* fit */}
            <div>
              <FieldLabel>Fit score</FieldLabel>
              <div className="mt-1.5">
                <ScoreBar value={enrollment.score} />
              </div>
              {enrollment.score_rationale && (
                <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
                  {enrollment.score_rationale}
                </p>
              )}
            </div>

            {/* skills */}
            {contact && contact.skills.length > 0 && (
              <div>
                <FieldLabel>Skills</FieldLabel>
                <div className="mt-1.5 flex flex-wrap gap-1">
                  {contact.skills.map((s) => (
                    <Badge key={s} variant="secondary">
                      {s}
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            {/* conversation */}
            {!conv ? (
              <Skeleton className="h-16" />
            ) : messages.length > 0 ? (
              <div>
                <FieldLabel>Conversation</FieldLabel>
                <div className="mt-2 space-y-2">
                  {messages.map((m) => (
                    <div
                      key={m.id}
                      className={cn(
                        "max-w-[88%] rounded-xl border px-3 py-2 text-sm leading-relaxed",
                        m.direction === "outbound"
                          ? "ml-auto border-[var(--accent-line)] bg-accent"
                          : "border-border bg-secondary/40",
                      )}
                    >
                      {m.subject && <div className="mb-0.5 text-xs font-semibold">{m.subject}</div>}
                      <p className="whitespace-pre-line">{m.body}</p>
                    </div>
                  ))}
                </div>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">No messages yet.</p>
            )}
          </div>

          {/* actions */}
          <div className="space-y-2 border-t border-border p-4">
            {proposed && (
              <Button className="w-full" disabled={approving} onClick={() => onApprove(enrollment.id)}>
                Approve &amp; enroll
              </Button>
            )}
            {messages.length > 0 && (
              <Button variant="outline" className="w-full" asChild>
                <Link to="/inbox">
                  Open in Inbox <ArrowRight />
                </Link>
              </Button>
            )}
            <Button variant="ghost" className="w-full" asChild>
              <Link to={`/people/${enrollment.contact_id}`}>View full profile</Link>
            </Button>
          </div>
        </div>
      )}
    </Sheet>
  );
}

function FieldLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="font-mono text-[0.6rem] font-semibold uppercase tracking-wider text-muted-foreground">
      {children}
    </div>
  );
}
