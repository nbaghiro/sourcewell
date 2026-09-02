import { Inbox, Mail, PenLine, Search, Send, Sparkles } from "lucide-react";
import * as React from "react";
import { Link, useSearchParams } from "react-router-dom";
import { toast } from "sonner";

import { ChannelIcon, LinkedInIcon, LINKEDIN_BLUE as LI_BLUE } from "@/components/brand-icons";
import { clockTime as timeLabel, dayLabel, initials, shortAgo as relTime } from "@/lib/format";
import { EmptyState } from "@/components/empty-state";
import { PageLayout } from "@/components/page-layout";
import { ScoreBar } from "@/components/score-bar";
import { StateBadge, STATE_MAP, displayState } from "@/components/state-badge";
import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { Textarea } from "@/components/ui/textarea";
import {
  useApproveMessage,
  useApprovals,
  useConversation,
  useConversationChannels,
  useConversationSummary,
  useDraftReply,
  useEditMessage,
  useHandoff,
  useInbox,
  useMarkRead,
  useOptOut,
  useSendReply,
  type Channel,
  type ChannelOption,
  type Conversation,
  type InboxItem,
  type Message,
} from "@/lib/api/queries";
import { apiErrorMessage } from "@/lib/api/client";
import { cn } from "@/lib/utils";

/** Whether the inbox should show its "no messages yet" placeholder instead of the thread grid.
 *
 *  Exported and pure so the rule can be pinned in a test: the bug it encodes is invisible to
 *  type-checking and only reproduces on a workspace with no messages at all.
 *
 *  The list is built from *messages*, so a conversation opened with "Message" has no row until
 *  something is sent. On an empty workspace that made `rows` empty, replaced the whole grid, and
 *  left the thread pane nowhere to render — you'd click Message and land on "No messages yet"
 *  with no way to reach the person. A targeted thread therefore keeps the grid mounted, unless
 *  its id doesn't resolve, in which case the placeholder is right after all.
 */
export function showsEmptyState(opts: {
  loading: boolean;
  rowCount: number;
  selected: string | null;
  conversationMissing: boolean;
}): boolean {
  if (opts.loading) return false;
  if (opts.rowCount > 0) return false;
  return !opts.selected || opts.conversationMissing;
}

/** One row in the unified message list — either an inbound conversation or an outbound draft. */
interface Row {
  kind: "conversation" | "approval";
  enrollmentId: string;
  name: string;
  avatar?: string | null;
  channel: string;
  preview: string;
  at: string | null;
  state: string;
  unread: boolean;
}

const rowState = (it: InboxItem) => displayState(it.state ?? "active", it.reply_pending);

// Order the filter chips actionable-first; unknown states fall to the end.
const STATE_ORDER = [
  // A conversation you owe an answer to is the most actionable thing in the inbox.
  "needs_reply",
  "awaiting_approval",
  "awaiting_reply",
  "neutral",
  "interested",
  "scheduled",
  "active",
  "handed_off",
  "opted_out",
  "completed",
];
const stateRank = (s: string) => {
  const i = STATE_ORDER.indexOf(s);
  return i === -1 ? STATE_ORDER.length : i;
};
const stateLabel = (s: string) => STATE_MAP[s]?.label ?? s.replace(/_/g, " ");

// ---------- helpers ----------
/** Shown until `GET /inbox/{id}/summary` answers — the same wording as the server's
 *  deterministic fallback, so the rail never flashes empty. */
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

/** The handle a channel would deliver to, shown next to the composer so the sender can see it. */
function targetFor(channel: string, contact: Conversation["contact"]) {
  return channel === "linkedin"
    ? contact.linkedin_url?.replace(/^https?:\/\/(www\.)?linkedin\.com\/in\//, "").replace(/\/+$/, "")
    : contact.email;
}

/** Email ↔ LinkedIn segmented picker. An unreachable channel is disabled and says why. */
function ChannelPicker({
  options,
  value,
  onChange,
}: {
  options: ChannelOption[];
  value: Channel;
  onChange: (c: Channel) => void;
}) {
  return (
    <div className="inline-flex rounded-md border border-border bg-secondary/40 p-0.5">
      {options.map((o) => {
        const active = o.channel === value;
        return (
          <button
            key={o.channel}
            type="button"
            disabled={!o.available}
            title={o.reason ?? o.target ?? undefined}
            onClick={() => onChange(o.channel as Channel)}
            className={cn(
              "inline-flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors",
              active ? "bg-card text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
              !o.available && "cursor-not-allowed opacity-40 hover:text-muted-foreground",
            )}
            style={active && o.channel === "linkedin" ? { color: LI_BLUE } : undefined}
          >
            <ChannelIcon channel={o.channel} className="size-3.5" />
            {o.channel === "linkedin" ? "LinkedIn" : "Email"}
          </button>
        );
      })}
    </div>
  );
}

function ChannelTag({ channel, detail }: { channel: string; detail?: string | null }) {
  const li = channel === "linkedin";
  return (
    <span
      className="inline-flex max-w-full items-center gap-1.5 whitespace-nowrap rounded-md border px-2 py-1 text-xs font-medium"
      style={
        li
          ? { color: LI_BLUE, borderColor: `${LI_BLUE}33`, backgroundColor: `${LI_BLUE}0d` }
          : undefined
      }
      data-email={!li}
    >
      <ChannelIcon channel={channel} className="size-3.5 shrink-0" />
      {li ? "LinkedIn" : "Email"}
      {detail && <span className="truncate opacity-70">· {detail}</span>}
    </span>
  );
}

function FilterChip({
  active,
  label,
  count,
  onClick,
}: {
  active: boolean;
  label: string;
  count: number;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors",
        active
          ? "border-primary bg-primary text-primary-foreground"
          : "border-border bg-card text-muted-foreground hover:text-foreground",
      )}
    >
      {label}
      <span
        className={cn(
          "font-mono tabular-nums",
          active ? "text-primary-foreground/80" : "text-muted-foreground/70",
        )}
      >
        {count}
      </span>
    </button>
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
  const { data: inboxData } = useInbox();
  const { data: approvalsData } = useApprovals();
  // `?enrollment=` is how "Message <person>" arrives here from a contact page.
  const [params] = useSearchParams();
  const targeted = params.get("enrollment");
  const [selected, setSelected] = React.useState<string | null>(targeted); // enrollment id
  const [draft, setDraft] = React.useState("");
  const [subject, setSubject] = React.useState("");
  const [channel, setChannel] = React.useState<Channel | null>(null); // null = the thread default
  const [query, setQuery] = React.useState("");
  const [filter, setFilter] = React.useState<string>("all");

  const sendReplyM = useSendReply();
  const handoffM = useHandoff();
  const optOutM = useOptOut();
  const markRead = useMarkRead();
  const draftAI = useDraftReply();
  const busy = sendReplyM.isPending || handoffM.isPending || optOutM.isPending;

  const loading = !inboxData || !approvalsData;

  // Unified message list: outbound drafts awaiting approval + inbound conversations,
  // one row per enrollment, each tagged with a state we can filter on.
  const rows = React.useMemo<Row[]>(() => {
    const approvals = approvalsData ?? [];
    const inbox = inboxData ?? [];
    const apprEnrollments = new Set(approvals.map((a) => a.enrollment_id));
    return [
      ...approvals.map(
        (a): Row => ({
          kind: "approval",
          enrollmentId: a.enrollment_id,
          name: a.contact_name,
          avatar: a.contact_avatar,
          channel: a.channel,
          preview: a.subject || a.body,
          at: a.created_at,
          state: "awaiting_approval",
          unread: true,
        }),
      ),
      ...inbox
        .filter((it) => !apprEnrollments.has(it.enrollment_id))
        .map(
          (it): Row => ({
            kind: "conversation",
            enrollmentId: it.enrollment_id,
            name: it.contact_name ?? "Unknown",
            avatar: it.contact_avatar,
            channel: it.channel,
            preview: it.last_message.body,
            at: it.last_at,
            state: rowState(it),
            unread: it.unread,
          }),
        ),
    ];
  }, [approvalsData, inboxData]);

  const counts = React.useMemo(() => {
    const c: Record<string, number> = {};
    rows.forEach((r) => {
      c[r.state] = (c[r.state] ?? 0) + 1;
    });
    return c;
  }, [rows]);
  const states = React.useMemo(
    () => Object.keys(counts).sort((a, b) => stateRank(a) - stateRank(b)),
    [counts],
  );

  const visible = rows.filter(
    (r) =>
      (filter === "all" || r.state === filter) &&
      r.name.toLowerCase().includes(query.toLowerCase()),
  );

  const selectedRow = rows.find((r) => r.enrollmentId === selected) ?? null;
  // One thread view for both kinds: approvals load the same conversation, where the queued
  // draft renders in-thread as a recommended bubble to approve.
  const { data: conv, isError: convMissing } = useConversation(selected);
  const { data: channelData } = useConversationChannels(selected);
  const { data: summaryData } = useConversationSummary(selected);
  const aiDraft = () => selected && draftAI.mutate(selected, { onSuccess: (r) => setDraft(r.text) });

  // Keep a sensible selection: when the current row isn't in view, pick the first visible one.
  // Which thread was asked for by URL, and therefore must not be tidied away. A ref, not the
  // parameter itself: clearing `?enrollment=` after honouring it flips this back to null, which
  // re-arms the effect below and it replaces the selection with the first row — the very bug the
  // parameter exists to prevent.
  const pinned = React.useRef<string | null>(targeted);
  React.useEffect(() => {
    if (targeted && pinned.current !== targeted) {
      pinned.current = targeted;
      setSelected(targeted);
    }
  }, [targeted]);

  // Keep a sensible selection: when the current row isn't in view, pick the first visible one.
  // A pinned thread is exempt — a conversation with no messages yet isn't in the list at all, so
  // "not in view" is its normal state rather than a sign the selection went stale.
  const visibleKeys = visible.map((r) => r.enrollmentId).join(",");
  React.useEffect(() => {
    if (selected && pinned.current === selected) return;
    if (visible.length > 0 && !visible.some((r) => r.enrollmentId === selected)) {
      setSelected(visible[0].enrollmentId);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [visibleKeys, selected]);

  // Opening a conversation clears the composer and marks it read.
  React.useEffect(() => {
    setDraft("");
    setSubject("");
    setChannel(null);
    if (selected && selectedRow?.kind === "conversation") markRead.mutate(selected);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  // The picked channel, falling back to the backend's default for this thread until one is picked.
  const activeChannel = (channel ?? channelData?.default ?? "email") as Channel;

  const sendReply = (text: string, origin?: string) => {
    if (!selected) return;
    sendReplyM.mutate(
      {
        id: selected,
        text,
        channel: activeChannel,
        subject: activeChannel === "email" ? subject || undefined : undefined,
        origin,
      },
      {
        onSuccess: () => {
          setDraft("");
          setSubject("");
        },
        onError: (err) => toast.error(apiErrorMessage(err, "Couldn't send that message")),
      },
    );
  };
  const handoff = () => selected && handoffM.mutate(selected);
  const optOut = () => selected && optOutM.mutate(selected);

  return (
    <PageLayout width="wide" fill>
      {/* generic message filter — any state becomes a chip; approvals are just "Awaiting approval" */}
      <div className="flex flex-wrap items-center gap-1.5">
        <FilterChip active={filter === "all"} label="All" count={rows.length} onClick={() => setFilter("all")} />
        {states.map((s) => (
          <FilterChip
            key={s}
            active={filter === s}
            label={stateLabel(s)}
            count={counts[s]}
            onClick={() => setFilter(s)}
          />
        ))}
      </div>

      {showsEmptyState({
        loading,
        rowCount: rows.length,
        selected,
        conversationMissing: convMissing,
      }) ? (
        <EmptyState icon={Inbox} title="No messages yet" description="Replies and drafts to approve appear here." />
      ) : (
        <div className="grid min-h-0 flex-1 grid-cols-[300px_1fr] overflow-hidden rounded-xl border border-border bg-card shadow-sm xl:grid-cols-[300px_1fr_300px]">
          {/* ---- list ---- */}
          <div className="flex min-h-0 flex-col border-r border-border">
            <div className="border-b border-border px-4 py-3">
              <div className="mb-2 flex items-center justify-between">
                <h2 className="font-display text-base font-semibold">Inbox</h2>
                <span className="font-mono text-xs text-muted-foreground">{visible.length}</span>
              </div>
              <div className="flex items-center gap-2 rounded-md border border-border bg-secondary/40 px-2.5 py-1.5 text-sm text-muted-foreground">
                <Search className="size-4" />
                <input
                  className="w-full bg-transparent text-foreground outline-none placeholder:text-muted-foreground"
                  placeholder="Search messages"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                />
              </div>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto">
              {loading
                ? [0, 1, 2, 3].map((i) => <Skeleton key={i} className="m-3 h-14" />)
                : visible.map((r) => (
                    <button
                      key={r.enrollmentId}
                      onClick={() => setSelected(r.enrollmentId)}
                      className={cn(
                        "flex w-full gap-3 border-b border-border/50 px-4 py-3.5 text-left transition-colors",
                        selected === r.enrollmentId ? "bg-accent/60" : "hover:bg-secondary/40",
                      )}
                    >
                      <Avatar className="size-9">
                        {r.avatar && <AvatarImage src={r.avatar} alt="" />}
                        <AvatarFallback>{initials(r.name)}</AvatarFallback>
                      </Avatar>
                      <div className="min-w-0 flex-1">
                        <div className="flex items-center justify-between gap-2">
                          <span className={cn("truncate text-sm", r.unread ? "font-bold" : "font-semibold")}>
                            {r.name}
                          </span>
                          <span className="shrink-0 text-[0.65rem] text-muted-foreground">
                            {r.at ? relTime(r.at) : ""}
                          </span>
                        </div>
                        <div className="mt-0.5 flex items-center gap-1.5">
                          <ChannelIcon channel={r.channel} className="size-3 shrink-0" />
                          <span className="truncate text-xs text-muted-foreground">
                            {r.preview.replace(/\n/g, " ")}
                          </span>
                        </div>
                        <div className="mt-1.5">
                          <StateBadge state={r.state} />
                        </div>
                      </div>
                    </button>
                  ))}
              {/* A conversation with nothing sent yet has no row here, so without this the column
                  is a blank panel next to the thread you just opened. */}
              {!loading && visible.length === 0 && (
                <p className="px-4 py-6 text-xs leading-relaxed text-muted-foreground">
                  {query
                    ? "No conversations match that search."
                    : "No conversations yet. Send your first message and it'll appear here."}
                </p>
              )}
            </div>
          </div>

          {/* ---- detail: one conversation thread; queued drafts approve in-thread ---- */}
          {!conv ? (
            <div className="space-y-4 p-6">
              <Skeleton className="h-12" />
              <Skeleton className="ml-auto h-20 w-2/3" />
              <Skeleton className="h-24 w-2/3" />
            </div>
          ) : (
            <>
              <Thread
                conv={conv}
                draft={draft}
                setDraft={setDraft}
                subject={subject}
                setSubject={setSubject}
                channel={activeChannel}
                setChannel={setChannel}
                channelOptions={channelData?.options ?? []}
                onSend={sendReply}
                busy={busy}
                onAiDraft={aiDraft}
                aiDrafting={draftAI.isPending}
              />
              <ContextRail conv={conv} summary={summaryData?.summary} onHandoff={handoff} onOptOut={optOut} busy={busy} />
            </>
          )}
        </div>
      )}
    </PageLayout>
  );
}

function Thread({
  conv,
  draft,
  setDraft,
  subject,
  setSubject,
  channel,
  setChannel,
  channelOptions,
  onSend,
  busy,
  onAiDraft,
  aiDrafting,
}: {
  conv: Conversation;
  draft: string;
  setDraft: (s: string) => void;
  subject: string;
  setSubject: (s: string) => void;
  channel: Channel;
  setChannel: (c: Channel) => void;
  channelOptions: ChannelOption[];
  onSend: (text: string, origin?: string) => void;
  busy: boolean;
  onAiDraft: () => void;
  aiDrafting: boolean;
}) {
  const sent = conv.messages.filter((m) => m.status !== "draft");
  const suggestion = conv.messages.find((m) => m.status === "draft");
  // awaiting_approval → the draft is a queued first touchpoint the user approves in-thread;
  // otherwise it's an AI-suggested reply to send.
  const isApproval = conv.enrollment.state === "awaiting_approval";
  const channelLabel = channel === "linkedin" ? "LinkedIn" : "Email";
  const detail = targetFor(channel, conv.contact);
  const blocked = channelOptions.find((o) => o.channel === channel && !o.available);

  let lastDay = "";
  let lastChannel = "";

  // Keep the thread pinned to the newest message — on open, on a new message, and after an approve.
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const scrollKey = [conv.contact.id, conv.messages.length, sent.length, suggestion?.id ?? ""].join(
    "|",
  );
  React.useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [scrollKey]);

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
          <ChannelTag channel={conv.channel} detail={targetFor(conv.channel, conv.contact)} />
          <StateBadge state={conv.enrollment.state} replyPending={conv.enrollment.reply_pending} />
        </div>
      </header>

      {/* messages — bottom-aligned so short threads and the approval card sit above the composer */}
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        <div className="flex min-h-full flex-col justify-end space-y-3">
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

        {suggestion &&
          (isApproval ? (
            <RecommendedBubble
              msg={suggestion}
              channel={conv.channel}
              contactName={conv.contact.name ?? "them"}
            />
          ) : (
            <div className="ml-auto max-w-[85%] rounded-2xl border border-dashed p-3.5" style={{ borderColor: "var(--accent-line)", backgroundColor: "color-mix(in srgb, var(--accent) 55%, white)" }}>
              <div className="mb-1.5 flex items-center gap-1.5 text-xs font-semibold text-[var(--accent-strong)]">
                <Sparkles className="size-3.5" /> Suggested reply
              </div>
              <p className="whitespace-pre-line text-sm leading-relaxed text-foreground">{suggestion.body}</p>
              <div className="mt-3 flex justify-end gap-2">
                <Button variant="outline" size="sm" onClick={() => setDraft(suggestion.body)}>Edit</Button>
                <Button size="sm" disabled={busy} onClick={() => onSend(suggestion.body, "ai")}>
                  <Send /> Send
                </Button>
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* composer — always visible; disabled while a queued draft awaits approval, enabled once sent */}
      <div className="border-t border-border px-4 py-3">
        <div className="mb-2 flex flex-wrap items-center gap-1.5">
          <ChannelPicker options={channelOptions} value={channel} onChange={setChannel} />
          <span className="mx-1 h-4 w-px bg-border" />
          {QUICK.map((q) => (
            <button
              key={q.label}
              onClick={() => setDraft(q.body)}
              disabled={isApproval}
              className="rounded-full border border-border bg-secondary/40 px-3 py-1 text-xs text-muted-foreground transition-colors hover:text-foreground disabled:cursor-not-allowed disabled:opacity-50"
            >
              {q.label}
            </button>
          ))}
        </div>
        <div
          className={cn(
            "rounded-xl border border-border bg-card focus-within:border-ring",
            isApproval && "opacity-60",
          )}
        >
          {/* LinkedIn messages have no subject line — the field only appears for email. */}
          {channel === "email" && !isApproval && (
            <input
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              placeholder="Subject (optional)"
              className="w-full border-b border-border bg-transparent px-3.5 py-2 text-sm font-medium text-foreground outline-none placeholder:font-normal placeholder:text-muted-foreground"
            />
          )}
          <textarea
            rows={2}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            disabled={isApproval}
            placeholder={
              isApproval
                ? "Approve the recommended message to start the conversation…"
                : `Message via ${channelLabel}…`
            }
            className="w-full resize-none bg-transparent px-3.5 py-2.5 text-sm text-foreground outline-none placeholder:text-muted-foreground disabled:cursor-not-allowed"
          />
          <div className="flex items-center justify-between gap-3 px-3 pb-2.5">
            <span className="flex min-w-0 items-center gap-1.5 text-xs text-muted-foreground">
              <ChannelIcon channel={channel} className="size-3.5 shrink-0" /> via {channelLabel}
              {blocked ? (
                <span className="truncate text-destructive">· {blocked.reason}</span>
              ) : (
                detail && <span className="truncate opacity-70">· {detail}</span>
              )}
            </span>
            <div className="flex shrink-0 items-center gap-2">
              <Button
                variant="ghost"
                size="sm"
                disabled={aiDrafting || isApproval}
                onClick={onAiDraft}
              >
                <Sparkles /> {aiDrafting ? "Drafting…" : "Draft with AI"}
              </Button>
              <Button
                size="sm"
                disabled={!draft.trim() || busy || isApproval || !!blocked}
                onClick={() => onSend(draft)}
              >
                <Send /> Send
              </Button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

/** A tiny per-message tag on outbound bubbles: AI-drafted vs typed by the recruiter. */
function OriginFlag({ origin }: { origin: string }) {
  const ai = origin === "ai";
  return (
    <span
      className="inline-flex items-center gap-0.5 font-medium"
      style={ai ? { color: "var(--accent-strong)" } : undefined}
      title={ai ? "Drafted by the AI agent" : "Typed by you"}
    >
      {ai ? <Sparkles className="size-2.5" /> : <PenLine className="size-2.5" />}
      {ai ? "AI" : "You"}
    </span>
  );
}

/** The AI-recommended first touchpoint, shown in-thread as a pending bubble the user approves —
 *  editable inline, and on approval it sends and re-renders as a normal sent message. */
function RecommendedBubble({
  msg,
  channel,
  contactName,
}: {
  msg: Message;
  channel: string;
  contactName: string;
}) {
  const editMessage = useEditMessage();
  const approveMessage = useApproveMessage();
  const [editing, setEditing] = React.useState(false);
  const [subject, setSubject] = React.useState(msg.subject ?? "");
  const [body, setBody] = React.useState(msg.body);
  const busy = editMessage.isPending || approveMessage.isPending;

  // Reset the editor when switching to a different recommended draft.
  React.useEffect(() => {
    setSubject(msg.subject ?? "");
    setBody(msg.body);
    setEditing(false);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [msg.id]);

  const dirty = subject !== (msg.subject ?? "") || body !== msg.body;

  async function approve() {
    try {
      if (dirty) await editMessage.mutateAsync({ messageId: msg.id, subject, body });
      // Approving isn't always sending: the governor can defer the message to the next sending
      // window or hold it at the daily cap, and the endpoint returns the status it really has.
      // Reporting "Sent" for all of them told the recruiter a message had gone out when it was
      // queued for the morning.
      const result = await approveMessage.mutateAsync(msg.id);
      if (result.status === "sent") toast.success(`Sent to ${contactName}`);
      else if (result.status === "failed") toast.error(`Couldn't send to ${contactName}`);
      else toast.success("Queued — it'll send in your next sending window");
      // Stay on the thread — the invalidated conversation refetches and the draft
      // re-renders as a normal sent message.
    } catch {
      toast.error("Couldn't send");
    }
  }

  return (
    <div
      className="ml-auto max-w-[85%] rounded-2xl border border-dashed p-3.5"
      style={{ borderColor: "var(--accent-line)", backgroundColor: "color-mix(in srgb, var(--accent) 55%, white)" }}
    >
      <div className="mb-2 flex items-center gap-1.5 text-xs font-semibold text-[var(--accent-strong)]">
        <Sparkles className="size-3.5" /> Recommended message · awaiting your approval
      </div>
      {editing ? (
        <div className="space-y-2">
          {channel === "email" && (
            <Input value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Subject" />
          )}
          <Textarea rows={8} value={body} onChange={(e) => setBody(e.target.value)} />
        </div>
      ) : (
        <>
          {channel === "email" && subject && (
            <div className="mb-1 text-sm font-semibold text-foreground">{subject}</div>
          )}
          <p className="whitespace-pre-line text-sm leading-relaxed text-foreground">{body}</p>
        </>
      )}
      <div className="mt-3 flex justify-end gap-2">
        <Button variant="outline" size="sm" disabled={busy} onClick={() => setEditing((e) => !e)}>
          {editing ? "Done" : "Edit"}
        </Button>
        <Button size="sm" disabled={busy} onClick={() => void approve()}>
          <Send /> Approve &amp; send
        </Button>
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
          {/* Only email has a subject line; a LinkedIn bubble showing one would be showing
              something the transport never sent (older rows still carry it). */}
          {m.subject && m.channel !== "linkedin" && (
            <div className="mb-1 font-semibold">{m.subject}</div>
          )}
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
          {out && <OriginFlag origin={m.origin} />}
          {out && <span>· {m.status === "sent" ? "Sent" : m.status}</span>}
        </div>
      </div>
    </div>
  );
}

function ContextRail({
  conv,
  summary,
  onHandoff,
  onOptOut,
  busy,
}: {
  conv: Conversation;
  summary?: string;
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
        {/* A direct conversation has no campaign, so no criteria, so no fit to report. */}
        <ScoreBar value={conv.enrollment.score} unscored={!conv.campaign.id} />
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

      {/* A direct conversation has no campaign behind it — showing the card anyway rendered a
          blank name and "Touchpoint 1 of 0", which reads like something failed to load. */}
      {conv.campaign.id ? (
        <Link
          to={`/campaigns/${conv.campaign.id}`}
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
      ) : (
        <div className="rounded-lg border border-dashed border-border p-3">
          <div className="font-mono text-[0.6rem] font-semibold uppercase tracking-wider text-muted-foreground">
            Direct message
          </div>
          <div className="mt-0.5 text-xs text-muted-foreground">
            Not part of a campaign — nothing sends automatically here.
          </div>
        </div>
      )}

      <div
        className="rounded-lg border p-3"
        style={{ borderColor: "var(--accent-line)", backgroundColor: "color-mix(in srgb, var(--accent) 45%, white)" }}
      >
        <div className="flex items-center gap-1.5 text-xs font-semibold text-[var(--accent-strong)]">
          <Sparkles className="size-3.5" /> Summary
        </div>
        <p className="mt-1 text-sm leading-relaxed text-foreground">{summary ?? summaryFor(conv.enrollment.state)}</p>
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
