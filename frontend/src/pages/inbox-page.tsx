import { Inbox, Mail, Search, Send, Sparkles } from "lucide-react";
import * as React from "react";
import { Link } from "react-router-dom";

import { ChannelIcon, LinkedInIcon, LINKEDIN_BLUE as LI_BLUE } from "@/components/brand-icons";
import { clockTime as timeLabel, dayLabel, initials, shortAgo as relTime } from "@/lib/format";
import { EmptyState } from "@/components/empty-state";
import { PageLayout } from "@/components/page-layout";
import { Segmented } from "@/components/ui/segmented";
import { ApprovalsTab } from "@/pages/approvals-page";
import { ScoreBar } from "@/components/score-bar";
import { StateBadge } from "@/components/state-badge";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import {
  useConversation,
  useDraftReply,
  useHandoff,
  useInbox,
  useMarkRead,
  useOptOut,
  useSendReply,
} from "@/lib/api/queries";
import { cn } from "@/lib/utils";

// ---------- types (shapes flow from the generated API types; unions widened to string) ----------
interface Message {
  id: string;
  direction: string;
  channel: string;
  status: string;
  subject: string | null;
  body: string;
  created_at: string | null;
}
interface Conversation {
  enrollment: { state: string; score: number; current_step: number };
  contact: {
    id: string | null;
    name: string | null;
    title: string | null;
    company: string | null;
    location: string | null;
    email: string | null;
    linkedin_url: string | null;
    avatar_url: string | null;
    skills: string[];
  };
  campaign: { id: string | null; name: string | null; steps: number };
  channel: string;
  messages: Message[];
}

// ---------- helpers ----------
function summaryFor(state: string) {
  switch (state) {
    case "handed_off":
      return "Interested and a call is scheduled — ready to hand to the hiring team.";
    case "awaiting_reply":
      return "You've replied with the details they asked for. Waiting on their response.";
    case "opted_out":
      return "Politely declined — not looking right now. Conversation closed.";
    default:
      return "Outreach in progress.";
  }
}

function ChannelTag({ channel, detail }: { channel: string; detail?: string | null }) {
  const li = channel === "linkedin";
  return (
    <span
      className="inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs font-medium"
      style={
        li
          ? { color: LI_BLUE, borderColor: `${LI_BLUE}33`, backgroundColor: `${LI_BLUE}0d` }
          : undefined
      }
      data-email={!li}
    >
      <ChannelIcon channel={channel} className="size-3.5" />
      {li ? "LinkedIn" : "Email"}
      {detail && <span className="opacity-70">· {detail}</span>}
    </span>
  );
}

const QUICK = [
  { label: "Propose a call", body: "Would you be open to a quick 20-minute call this week? Happy to work around your schedule." },
  { label: "Share comp range", body: "Happy to share specifics — the range is €120–150k base + equity, depending on level." },
  { label: "Send JD", body: "I'll send the full job description over now so you can take a look." },
  { label: "Not a fit", body: "Thanks so much for the reply — I don't think this one's the right fit, but I'll keep you in mind for future roles." },
];

// ---------- page ----------
export function InboxPage() {
  const { data: items } = useInbox();
  const [selected, setSelected] = React.useState<string | null>(null);
  const [draft, setDraft] = React.useState("");
  const [query, setQuery] = React.useState("");
  const [tab, setTab] = React.useState<"replies" | "approvals">("replies");
  const { data: conv } = useConversation(selected);
  const sendReplyM = useSendReply();
  const handoffM = useHandoff();
  const optOutM = useOptOut();
  const markRead = useMarkRead();
  const draftAI = useDraftReply();
  const busy = sendReplyM.isPending || handoffM.isPending || optOutM.isPending;
  const aiDraft = () => selected && draftAI.mutate(selected, { onSuccess: (r) => setDraft(r.text) });

  React.useEffect(() => {
    if (items && items.length > 0 && !selected) setSelected(items[0].enrollment_id);
  }, [items, selected]);

  // Opening a conversation clears the composer and marks it read.
  React.useEffect(() => {
    setDraft("");
    if (selected) markRead.mutate(selected);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  const sendReply = (text: string) =>
    selected && sendReplyM.mutate({ id: selected, text }, { onSuccess: () => setDraft("") });
  const handoff = () => selected && handoffM.mutate(selected);
  const optOut = () => selected && optOutM.mutate(selected);

  const filtered = (items ?? []).filter((it) =>
    (it.contact_name ?? "").toLowerCase().includes(query.toLowerCase()),
  );

  return (
    <PageLayout width="wide" fill>
      <Segmented
        value={tab}
        onChange={(v) => setTab(v as "replies" | "approvals")}
        options={[
          { value: "replies", label: "Replies" },
          { value: "approvals", label: "Approvals" },
        ]}
      />
      {tab === "approvals" ? (
        <ApprovalsTab />
      ) : items && items.length === 0 ? (
        <EmptyState icon={Inbox} title="No conversations yet" description="Replies appear here once messages go out." />
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-[300px_1fr] overflow-hidden rounded-xl border border-border bg-card shadow-sm xl:grid-cols-[300px_1fr_300px]">
        {/* ---- list ---- */}
        <div className="flex min-h-0 flex-col border-r border-border">
          <div className="border-b border-border px-4 py-3">
            <div className="mb-2 flex items-center justify-between">
              <h2 className="font-display text-base font-semibold">Inbox</h2>
              <span className="font-mono text-xs text-muted-foreground">{filtered.length}</span>
            </div>
            <div className="flex items-center gap-2 rounded-md border border-border bg-secondary/40 px-2.5 py-1.5 text-sm text-muted-foreground">
              <Search className="size-4" />
              <input
                className="w-full bg-transparent text-foreground outline-none placeholder:text-muted-foreground"
                placeholder="Search conversations"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
              />
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {!items
              ? [0, 1, 2, 3].map((i) => <Skeleton key={i} className="m-3 h-14" />)
              : filtered.map((it) => (
                  <button
                    key={it.enrollment_id}
                    onClick={() => setSelected(it.enrollment_id)}
                    className={cn(
                      "flex w-full gap-3 border-b border-border/50 px-4 py-3.5 text-left transition-colors",
                      selected === it.enrollment_id ? "bg-accent/60" : "hover:bg-secondary/40",
                    )}
                  >
                    <Avatar className="size-9">
                      {it.contact_avatar && <AvatarImage src={it.contact_avatar} alt="" />}
                      <AvatarFallback>{initials(it.contact_name)}</AvatarFallback>
                    </Avatar>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <span className={cn("truncate text-sm", it.unread ? "font-bold" : "font-semibold")}>
                          {it.contact_name}
                        </span>
                        <span className="shrink-0 text-[0.65rem] text-muted-foreground">{relTime(it.last_at)}</span>
                      </div>
                      <div className="mt-0.5 flex items-center gap-1.5">
                        <ChannelIcon
                          channel={it.channel}
                          className="size-3 shrink-0"
                        />
                        <span className="truncate text-xs text-muted-foreground">
                          {it.last_message.body.replace(/\n/g, " ")}
                        </span>
                      </div>
                      <div className="mt-1.5">
                        {it.state && <StateBadge state={it.state} />}
                      </div>
                    </div>
                  </button>
                ))}
          </div>
        </div>

        {/* ---- thread ---- */}
        {!conv ? (
          <div className="space-y-4 p-6">
            <Skeleton className="h-12" />
            <Skeleton className="ml-auto h-20 w-2/3" />
            <Skeleton className="h-24 w-2/3" />
          </div>
        ) : (
          <Thread conv={conv} draft={draft} setDraft={setDraft} onSend={sendReply} busy={busy} onAiDraft={aiDraft} aiDrafting={draftAI.isPending} />
        )}

        {/* ---- context rail ---- */}
        {conv && <ContextRail conv={conv} onHandoff={handoff} onOptOut={optOut} busy={busy} />}
        </div>
      )}
    </PageLayout>
  );
}

function Thread({
  conv,
  draft,
  setDraft,
  onSend,
  busy,
  onAiDraft,
  aiDrafting,
}: {
  conv: Conversation;
  draft: string;
  setDraft: (s: string) => void;
  onSend: (text: string) => void;
  busy: boolean;
  onAiDraft: () => void;
  aiDrafting: boolean;
}) {
  const sent = conv.messages.filter((m) => m.status !== "draft");
  const suggestion = conv.messages.find((m) => m.status === "draft");
  const channelLabel = conv.channel === "linkedin" ? "LinkedIn" : "Email";
  const detail = conv.channel === "linkedin" ? conv.contact.linkedin_url?.replace(/^https?:\/\//, "") : conv.contact.email;

  let lastDay = "";
  let lastChannel = "";

  return (
    <div className="flex min-h-0 flex-col">
      {/* header */}
      <header className="flex items-center justify-between gap-3 border-b border-border px-6 py-3">
        <div className="flex items-center gap-3">
          <Avatar className="size-9">
            {conv.contact.avatar_url && <AvatarImage src={conv.contact.avatar_url} alt="" />}
            <AvatarFallback>{initials(conv.contact.name)}</AvatarFallback>
          </Avatar>
          <div>
            <div className="text-sm font-semibold">{conv.contact.name}</div>
            <div className="text-xs text-muted-foreground">
              {conv.contact.title} · {conv.contact.company}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <ChannelTag channel={conv.channel} detail={detail} />
          <StateBadge state={conv.enrollment.state} />
        </div>
      </header>

      {/* messages */}
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-6 py-5">
        {sent.map((m) => {
          const day = m.created_at ? dayLabel(m.created_at) : "";
          const showDay = day && day !== lastDay;
          const showSwitch = lastChannel && m.channel !== lastChannel;
          lastDay = day;
          lastChannel = m.channel;
          return (
            <React.Fragment key={m.id}>
              {showDay && (
                <div className="flex justify-center py-1">
                  <span className="rounded-full bg-secondary px-3 py-0.5 text-[0.65rem] font-medium text-muted-foreground">
                    {day}
                  </span>
                </div>
              )}
              {showSwitch && (
                <div className="flex items-center justify-center gap-2 py-1 text-[0.65rem] text-muted-foreground">
                  <span className="h-px w-8 bg-border" />
                  <ChannelIcon channel={m.channel} className="size-3" />
                  moved to {m.channel === "linkedin" ? "LinkedIn" : "Email"}
                  <span className="h-px w-8 bg-border" />
                </div>
              )}
              <Bubble m={m} initials={initials(conv.contact.name)} avatar={conv.contact.avatar_url} />
            </React.Fragment>
          );
        })}

        {suggestion && (
          <div className="ml-auto max-w-[85%] rounded-2xl border border-dashed p-3.5" style={{ borderColor: "var(--accent-line)", backgroundColor: "color-mix(in srgb, var(--accent) 55%, white)" }}>
            <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-[var(--accent-strong)]">
              <Sparkles className="size-3.5" /> Suggested reply
            </div>
            <p className="whitespace-pre-line text-sm leading-relaxed text-foreground">{suggestion.body}</p>
            <div className="mt-3 flex justify-end gap-2">
              <Button variant="outline" size="sm" onClick={() => setDraft(suggestion.body)}>Edit</Button>
              <Button size="sm" disabled={busy} onClick={() => onSend(suggestion.body)}>
                <Send /> Send
              </Button>
            </div>
          </div>
        )}
      </div>

      {/* composer */}
      <div className="border-t border-border px-4 py-3">
        <div className="mb-2 flex flex-wrap gap-1.5">
          {QUICK.map((q) => (
            <button
              key={q.label}
              onClick={() => setDraft(q.body)}
              className="rounded-full border border-border bg-secondary/40 px-3 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground"
            >
              {q.label}
            </button>
          ))}
        </div>
        <div className="rounded-xl border border-border bg-card focus-within:border-ring">
          <textarea
            rows={2}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            placeholder={`Reply via ${channelLabel}…`}
            className="w-full resize-none bg-transparent px-3.5 py-2.5 text-sm text-foreground outline-none placeholder:text-muted-foreground"
          />
          <div className="flex items-center justify-between px-3 pb-2.5">
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <ChannelIcon channel={conv.channel} className="size-3.5" /> via {channelLabel}
              {detail && <span className="opacity-70">· {detail}</span>}
            </span>
            <div className="flex items-center gap-2">
              <Button variant="ghost" size="sm" disabled={aiDrafting} onClick={onAiDraft}>
                <Sparkles /> {aiDrafting ? "Drafting…" : "Draft with AI"}
              </Button>
              <Button size="sm" disabled={!draft.trim() || busy} onClick={() => onSend(draft)}>
                <Send /> Send
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function Bubble({
  m,
  initials: ini,
  avatar,
}: {
  m: Message;
  initials: string;
  avatar?: string | null;
}) {
  const out = m.direction === "outbound";
  return (
    <div className={cn("flex items-end gap-2.5", out ? "flex-row-reverse" : "flex-row")}>
      {!out && (
        <Avatar className="size-7">
          {avatar && <AvatarImage src={avatar} alt="" />}
          <AvatarFallback className="text-[0.6rem]">{ini}</AvatarFallback>
        </Avatar>
      )}
      <div className={cn("max-w-[76%]", out && "items-end")}>
        <div
          className={cn(
            "rounded-2xl border px-3.5 py-2.5 text-sm leading-relaxed",
            out
              ? "rounded-br-sm border-[var(--accent-line)] bg-accent text-foreground"
              : "rounded-bl-sm border-border bg-secondary/40",
          )}
        >
          {m.subject && <div className="mb-1 font-semibold">{m.subject}</div>}
          <p className="whitespace-pre-line">{m.body}</p>
        </div>
        <div
          className={cn(
            "mt-1 flex items-center gap-1.5 px-1 text-[0.65rem] text-muted-foreground",
            out ? "justify-end" : "justify-start",
          )}
        >
          <ChannelIcon channel={m.channel} className="size-3" />
          <span>{timeLabel(m.created_at)}</span>
          {out && <span>· {m.status === "sent" ? "Sent" : m.status}</span>}
        </div>
      </div>
    </div>
  );
}

function ContextRail({
  conv,
  onHandoff,
  onOptOut,
  busy,
}: {
  conv: Conversation;
  onHandoff: () => void;
  onOptOut: () => void;
  busy: boolean;
}) {
  const c = conv.contact;
  const terminal = conv.enrollment.state === "handed_off" || conv.enrollment.state === "opted_out";
  return (
    <aside className="hidden min-h-0 flex-col gap-5 overflow-y-auto border-l border-border p-5 xl:flex">
      <div className="flex flex-col items-center text-center">
        <Link
          to={c.id ? `/people/${c.id}` : "#"}
          className="flex flex-col items-center text-center transition-opacity hover:opacity-90"
        >
          <Avatar className="size-14">
            {c.avatar_url && <AvatarImage src={c.avatar_url} alt="" />}
            <AvatarFallback className="text-base">{initials(c.name)}</AvatarFallback>
          </Avatar>
          <div className="mt-2.5 font-display text-base font-semibold hover:underline">{c.name}</div>
          <div className="text-xs text-muted-foreground">{c.title}</div>
          <div className="text-xs text-muted-foreground">
            {c.company}
            {c.location ? ` · ${c.location}` : ""}
          </div>
        </Link>
        <div className="mt-3 flex gap-2">
          {c.email && (
            <a
              href={`mailto:${c.email}`}
              className="grid size-8 place-items-center rounded-md border border-border text-muted-foreground transition-colors hover:text-foreground"
            >
              <Mail className="size-4" />
            </a>
          )}
          {c.linkedin_url && (
            <a
              href={c.linkedin_url}
              target="_blank"
              rel="noreferrer"
              className="grid size-8 place-items-center rounded-md border border-border transition-colors"
              style={{ color: LI_BLUE }}
            >
              <LinkedInIcon className="size-4" />
            </a>
          )}
        </div>
      </div>

      <div>
        <div className="mb-1.5 font-mono text-[0.6rem] font-semibold uppercase tracking-wider text-muted-foreground">
          Fit score
        </div>
        <ScoreBar value={conv.enrollment.score} />
      </div>

      <div>
        <div className="mb-1.5 font-mono text-[0.6rem] font-semibold uppercase tracking-wider text-muted-foreground">
          Skills
        </div>
        <div className="flex flex-wrap gap-1">
          {c.skills.map((s) => (
            <Badge key={s} variant="secondary">
              {s}
            </Badge>
          ))}
        </div>
      </div>

      <Link
        to={conv.campaign.id ? `/campaigns/${conv.campaign.id}` : "#"}
        className="block rounded-lg border border-border bg-secondary/30 p-3 transition-colors hover:border-primary/40"
      >
        <div className="font-mono text-[0.6rem] font-semibold uppercase tracking-wider text-muted-foreground">
          Campaign
        </div>
        <div className="mt-0.5 text-sm font-semibold">{conv.campaign.name}</div>
        <div className="text-xs text-muted-foreground">
          Touchpoint {conv.enrollment.current_step + 1} of {conv.campaign.steps}
        </div>
      </Link>

      <div
        className="rounded-lg border p-3"
        style={{ borderColor: "var(--accent-line)", backgroundColor: "color-mix(in srgb, var(--accent) 45%, white)" }}
      >
        <div className="flex items-center gap-1.5 text-xs font-semibold text-[var(--accent-strong)]">
          <Sparkles className="size-3.5" /> Summary
        </div>
        <p className="mt-1 text-sm leading-relaxed text-foreground">{summaryFor(conv.enrollment.state)}</p>
      </div>

      <div className="mt-auto space-y-2">
        <Button className="w-full" disabled={busy || terminal} onClick={onHandoff}>
          {conv.enrollment.state === "handed_off" ? "Handed off ✓" : "Hand off to team"}
        </Button>
        <Button variant="outline" className="w-full" disabled={busy || terminal} onClick={onOptOut}>
          {conv.enrollment.state === "opted_out" ? "Opted out" : "Mark not interested"}
        </Button>
      </div>
    </aside>
  );
}
