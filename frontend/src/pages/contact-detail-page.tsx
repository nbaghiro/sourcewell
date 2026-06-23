import { ArrowLeft, Check, Clock, Mail, MapPin, MessageSquare, Pencil, Plus } from "lucide-react";
import * as React from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";

import { ChannelIcon, LinkedInIcon as LinkedInGlyph, LINKEDIN_BLUE as LI_BLUE } from "@/components/brand-icons";
import { initials, longAgo as relTime } from "@/lib/format";
import { PageLayout } from "@/components/page-layout";
import { ScoreBar } from "@/components/score-bar";
import { StateBadge } from "@/components/state-badge";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import { type ContactDetail, useCampaigns, useContact, useDeleteContact, useEnrollContact, useForgetContact, useUpdateContact } from "@/lib/api/queries";
import { cn } from "@/lib/utils";

interface Activity {
  id: string;
  direction: string;
  channel: string;
  status: string;
  subject: string | null;
  body: string;
  created_at: string | null;
  scheduled_at: string | null;
  campaign_name: string;
}

function shortDate(iso?: string | null): string {
  if (!iso) return "soon";
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function ContactDetailPage() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { data: c, isLoading: loading } = useContact(id ?? "");

  return (
    <PageLayout>
      <Link
        to="/contacts"
        className="inline-flex items-center gap-1.5 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="size-4" /> Contacts
      </Link>

      {loading || !c ? (
        <Skeleton className="h-80" />
      ) : (
        <>
          {/* hero */}
          <Card className="overflow-hidden">
            <div
              className="h-24"
              style={{ background: "linear-gradient(110deg, var(--sidebar), var(--sidebar-active) 55%, var(--score-from))" }}
            />
            <div className="px-6 pb-6">
              <div className="-mt-12 flex flex-wrap items-end gap-4">
                <Avatar className="size-24 rounded-2xl shadow-sm ring-4 ring-card">
                  {c.avatar_url && <AvatarImage src={c.avatar_url} alt={c.full_name} />}
                  <AvatarFallback className="rounded-2xl text-2xl">{initials(c.full_name)}</AvatarFallback>
                </Avatar>
                <div className="flex flex-1 flex-wrap items-end justify-between gap-3 pb-1">
                  <div className="min-w-0">
                    <h1 className="font-display text-2xl font-bold tracking-tight text-white [text-shadow:0_1px_3px_rgb(0_0_0/0.35)]">
                      {c.full_name}
                    </h1>
                    <p className="text-sm text-muted-foreground">
                      {c.title}
                      {c.company ? ` · ${c.company}` : ""}
                    </p>
                    <div className="mt-1.5 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
                      {c.location && (
                        <span className="inline-flex items-center gap-1">
                          <MapPin className="size-3.5" /> {c.location}
                        </span>
                      )}
                      {c.email && (
                        <a href={`mailto:${c.email}`} className="inline-flex items-center gap-1 hover:text-foreground">
                          <Mail className="size-3.5" /> {c.email}
                          {c.email_status && c.email_status !== "unverified" && (
                            <Badge
                              variant={
                                c.email_status === "valid"
                                  ? "success"
                                  : c.email_status === "invalid"
                                    ? "warning"
                                    : "secondary"
                              }
                              className="ml-1 text-[10px]"
                            >
                              {c.email_status}
                            </Badge>
                          )}
                        </a>
                      )}
                      {c.linkedin_url && (
                        <a href={c.linkedin_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1" style={{ color: LI_BLUE }}>
                          <LinkedInGlyph className="size-3.5" /> LinkedIn
                        </a>
                      )}
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <EditContactDialog contact={c} />
                    <AddToCampaignDialog contactId={c.id} />
                    <Button size="sm" onClick={() => navigate("/inbox")}>
                      <MessageSquare /> Message
                    </Button>
                  </div>
                </div>
              </div>
              {(c.skills.length > 0 || c.tags.length > 0) && (
                <div className="mt-4 flex flex-wrap items-center gap-1.5">
                  {c.skills.map((s) => (
                    <Badge key={s} variant="secondary">
                      {s}
                    </Badge>
                  ))}
                  {c.tags.map((t) => (
                    <Badge key={t} variant="outline" className="text-muted-foreground">
                      {t}
                    </Badge>
                  ))}
                </div>
              )}
            </div>
          </Card>

          {/* stats */}
          <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
            <Tile label="Best fit" value={c.stats.best_score} mono />
            <Tile label="Campaigns" value={c.stats.campaigns} />
            <Tile label="Replies" value={c.stats.replies} />
            <Tile label="Last contacted" value={relTime(c.stats.last_activity_at)} small />
          </div>

          <div className="grid gap-6 lg:grid-cols-[1.6fr_1fr]">
            {/* activity timeline */}
            <Card>
              <CardHeader>
                <CardTitle>Activity</CardTitle>
                <span className="text-xs text-muted-foreground">{c.activity.length} events</span>
              </CardHeader>
              <CardContent>
                {c.activity.length === 0 ? (
                  <p className="py-6 text-center text-sm text-muted-foreground">No messages yet.</p>
                ) : (
                  <div>
                    {c.activity.map((a, i) => (
                      <TimelineItem key={a.id} a={a} last={i === c.activity.length - 1} />
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            {/* sidebar */}
            <div className="space-y-6">
              <Card>
                <CardHeader>
                  <CardTitle>Campaigns</CardTitle>
                  <span className="text-xs text-muted-foreground">{c.enrollments.length}</span>
                </CardHeader>
                <CardContent className="space-y-2.5">
                  {c.enrollments.length === 0 && (
                    <p className="text-sm text-muted-foreground">Not in any campaign yet.</p>
                  )}
                  {c.enrollments.map((e) => (
                    <Link
                      key={e.id}
                      to={`/campaigns/${e.campaign_id}`}
                      className="block rounded-lg border border-border p-3 transition-colors hover:border-primary/40 hover:bg-secondary/30"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="truncate text-sm font-semibold text-foreground">{e.campaign_name}</span>
                        <StateBadge state={e.state} />
                      </div>
                      <div className="mt-2">
                        <ScoreBar value={e.score} />
                      </div>
                    </Link>
                  ))}
                </CardContent>
              </Card>

              {c.notes && (
                <Card>
                  <CardHeader>
                    <CardTitle>Notes</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <p className="text-sm leading-relaxed text-muted-foreground">{c.notes}</p>
                  </CardContent>
                </Card>
              )}

              <Card>
                <CardHeader>
                  <CardTitle>Details</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2.5 text-sm">
                  <Detail label="Industry" value={c.industry} />
                  <Detail label="Company size" value={c.company_size} />
                  <Detail label="Email" value={c.email} />
                  <Detail label="Location" value={c.location} />
                  <Detail label="Source" value={c.source} mono />
                </CardContent>
              </Card>
            </div>
          </div>
        </>
      )}
    </PageLayout>
  );
}

function Tile({ label, value, mono, small }: { label: string; value: string | number; mono?: boolean; small?: boolean }) {
  return (
    <Card className="p-4">
      <div className="text-xs font-medium text-muted-foreground">{label}</div>
      <div
        className={cn(
          "mt-1 font-display font-bold tracking-tight text-foreground",
          small ? "text-lg" : "text-2xl",
          mono && "font-mono tabular-nums",
        )}
      >
        {value}
      </div>
    </Card>
  );
}

function TimelineItem({ a, last }: { a: Activity; last: boolean }) {
  const out = a.direction === "outbound";
  const scheduled = a.status === "draft" && !!a.scheduled_at;
  return (
    <div className="flex gap-3">
      <div className="flex flex-col items-center">
        <div
          className={cn(
            "grid size-8 shrink-0 place-items-center rounded-full border",
            scheduled
              ? "border-dashed border-border bg-secondary/40 text-muted-foreground"
              : out
                ? "border-[var(--accent-line)] bg-accent text-[var(--accent-strong)]"
                : "border-border bg-secondary/60 text-muted-foreground",
          )}
        >
          {scheduled ? <Clock className="size-3.5" /> : <ChannelIcon channel={a.channel} className="size-3.5" />}
        </div>
        {!last && <div className="my-1 w-px flex-1 bg-border" />}
      </div>
      <div className={cn("min-w-0 flex-1", !last && "pb-5")}>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <span className="font-semibold text-foreground">{scheduled ? "Scheduled" : out ? "Sent" : "Reply"}</span>
          <span>·</span>
          <span className="truncate">{a.campaign_name}</span>
          <span className="ml-auto shrink-0">{scheduled ? `sends ${shortDate(a.scheduled_at)}` : relTime(a.created_at)}</span>
        </div>
        <div className={cn("mt-1.5 rounded-lg border p-3", scheduled ? "border-dashed border-border bg-secondary/20" : "border-border bg-card")}>
          {a.subject && <div className="mb-0.5 text-sm font-semibold">{a.subject}</div>}
          <p className="line-clamp-3 whitespace-pre-line text-sm text-muted-foreground">{a.body}</p>
        </div>
      </div>
    </div>
  );
}

function Detail({ label, value, mono }: { label: string; value?: string | null; mono?: boolean }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="text-muted-foreground">{label}</span>
      <span className={cn("truncate font-medium text-foreground", mono && "font-mono text-xs")}>
        {value ?? "—"}
      </span>
    </div>
  );
}

const csv = (s: string) => s.split(",").map((x) => x.trim()).filter(Boolean);

function EditContactDialog({ contact }: { contact: ContactDetail }) {
  const navigate = useNavigate();
  const update = useUpdateContact(contact.id);
  const del = useDeleteContact();
  const forget = useForgetContact();
  const [open, setOpen] = React.useState(false);
  const [f, setF] = React.useState({
    full_name: "",
    title: "",
    company: "",
    location: "",
    email: "",
    industry: "",
    company_size: "",
    skills: "",
    tags: "",
    notes: "",
  });

  React.useEffect(() => {
    if (open)
      setF({
        full_name: contact.full_name,
        title: contact.title ?? "",
        company: contact.company ?? "",
        location: contact.location ?? "",
        email: contact.email ?? "",
        industry: contact.industry ?? "",
        company_size: contact.company_size ?? "",
        skills: contact.skills.join(", "),
        tags: contact.tags.join(", "),
        notes: contact.notes ?? "",
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open]);

  function set(k: keyof typeof f, v: string) {
    setF((s) => ({ ...s, [k]: v }));
  }
  function save() {
    update.mutate(
      {
        full_name: f.full_name,
        title: f.title || null,
        company: f.company || null,
        location: f.location || null,
        email: f.email || null,
        industry: f.industry || null,
        company_size: f.company_size || null,
        skills: csv(f.skills),
        tags: csv(f.tags),
        notes: f.notes || null,
      },
      {
        onSuccess: () => {
          toast.success("Contact updated");
          setOpen(false);
        },
        onError: () => toast.error("Couldn't save"),
      },
    );
  }
  function remove() {
    del.mutate(contact.id, {
      onSuccess: () => {
        toast.success("Contact deleted");
        navigate("/contacts");
      },
    });
  }
  function forgetContact() {
    forget.mutate(contact.id, {
      onSuccess: () => {
        toast.success("Erased & suppressed");
        navigate("/contacts");
      },
    });
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <Pencil /> Edit
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Edit contact</DialogTitle>
        </DialogHeader>
        <div className="grid gap-3">
          <Field label="Name"><Input value={f.full_name} onChange={(e) => set("full_name", e.target.value)} /></Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Title"><Input value={f.title} onChange={(e) => set("title", e.target.value)} /></Field>
            <Field label="Company"><Input value={f.company} onChange={(e) => set("company", e.target.value)} /></Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Email"><Input value={f.email} onChange={(e) => set("email", e.target.value)} /></Field>
            <Field label="Location"><Input value={f.location} onChange={(e) => set("location", e.target.value)} /></Field>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Industry"><Input value={f.industry} onChange={(e) => set("industry", e.target.value)} /></Field>
            <Field label="Company size"><Input value={f.company_size} onChange={(e) => set("company_size", e.target.value)} /></Field>
          </div>
          <Field label="Skills (comma-separated)"><Input value={f.skills} onChange={(e) => set("skills", e.target.value)} /></Field>
          <Field label="Tags (comma-separated)"><Input value={f.tags} onChange={(e) => set("tags", e.target.value)} /></Field>
          <Field label="Notes"><Textarea rows={3} value={f.notes} onChange={(e) => set("notes", e.target.value)} /></Field>
        </div>
        <DialogFooter className="sm:justify-between">
          <div className="flex gap-2">
            <Button variant="ghost" className="text-destructive hover:text-destructive" disabled={del.isPending} onClick={remove}>
              Delete
            </Button>
            <Button variant="ghost" className="text-muted-foreground" disabled={forget.isPending} onClick={forgetContact} title="GDPR erase: delete and add to do-not-contact">
              Forget
            </Button>
          </div>
          <div className="flex gap-2">
            <DialogClose asChild>
              <Button variant="ghost">Cancel</Button>
            </DialogClose>
            <Button disabled={update.isPending || !f.full_name.trim()} onClick={save}>
              Save
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid gap-1.5">
      <Label>{label}</Label>
      {children}
    </div>
  );
}

function AddToCampaignDialog({ contactId }: { contactId: string }) {
  const { data: campaigns } = useCampaigns();
  const enrollContact = useEnrollContact();
  const [open, setOpen] = React.useState(false);
  const [busy, setBusy] = React.useState<string | null>(null);

  function enroll(cid: string) {
    setBusy(cid);
    enrollContact.mutate(
      { campaignId: cid, contactId },
      {
        onSuccess: () => {
          toast.success("Added to campaign");
          setOpen(false);
        },
        onSettled: () => setBusy(null),
      },
    );
  }

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogTrigger asChild>
        <Button variant="outline" size="sm">
          <Plus /> Add to campaign
        </Button>
      </DialogTrigger>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add to campaign</DialogTitle>
        </DialogHeader>
        <div className="space-y-1.5">
          {(campaigns ?? []).map((c) => (
            <button
              key={c.id}
              disabled={!!busy}
              onClick={() => void enroll(c.id)}
              className="flex w-full items-center justify-between gap-2 rounded-lg border border-border px-3.5 py-2.5 text-left text-sm transition-colors hover:bg-secondary/40 disabled:opacity-50"
            >
              <span className="font-medium text-foreground">{c.name}</span>
              {busy === c.id ? <Check className="size-4 text-primary" /> : <StateBadge state={c.status} />}
            </button>
          ))}
          {campaigns && campaigns.length === 0 && (
            <p className="text-sm text-muted-foreground">No campaigns yet.</p>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
