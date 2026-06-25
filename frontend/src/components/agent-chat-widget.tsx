import { Feather, Loader2, Send, X } from "lucide-react";
import * as React from "react";
import { useLocation } from "react-router-dom";

import { ChatEntities } from "@/components/cockpit/entities";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useAgentChat } from "@/lib/api/queries";

interface ChatMessage {
  role: "user" | "agent";
  text: string;
  entities?: unknown;
}

/** The campaign id when the chat is on a campaign route, else undefined (general chat). */
function campaignIdFrom(pathname: string): string | undefined {
  const m = pathname.match(/^\/campaigns\/([^/]+)/);
  return m && m[1] !== "new" ? m[1] : undefined;
}

/**
 * Always-available agent chat — a bottom-right toggle on every page. It attaches the active
 * campaign id when opened on a campaign route (so the agent can show/adjust that campaign), and
 * runs as a general chat everywhere else.
 */
export function AgentChatWidget() {
  const [open, setOpen] = React.useState(false);
  const [messages, setMessages] = React.useState<ChatMessage[]>([]);
  const [input, setInput] = React.useState("");
  const chat = useAgentChat();
  const scrollRef = React.useRef<HTMLDivElement>(null);
  const campaignId = campaignIdFrom(useLocation().pathname);

  const greeting = campaignId
    ? "Ask about this campaign — how it's doing, or how to adjust the audience or sequence."
    : "I'm Wren. Ask what needs you, why I skipped someone, or to find people.";
  const suggestions = campaignId
    ? ["How's this campaign doing?", "Find more senior people"]
    : ["What needs me today?", "Find VPs of Sales in EU fintech"];

  React.useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight });
  }, [messages, chat.isPending, open]);

  async function send(text: string) {
    const trimmed = text.trim();
    if (!trimmed || chat.isPending) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", text: trimmed }]);
    try {
      const res = await chat.mutateAsync({ message: trimmed, campaign_id: campaignId });
      setMessages((m) => [...m, { role: "agent", text: res.reply, entities: res.entities }]);
    } catch {
      setMessages((m) => [
        ...m,
        { role: "agent", text: "Sorry — I couldn't reach the agent just now. Try again?" },
      ]);
    }
  }

  return (
    <>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label={open ? "Close agent chat" : "Open agent chat"}
        className="fixed bottom-5 right-5 z-50 grid size-12 place-items-center rounded-full bg-gradient-to-br from-score-from to-score-to text-primary-foreground shadow-lg ring-1 ring-black/5 transition-transform hover:scale-105 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        {/* gentle "live" pulse — the agent is always working in the background */}
        {!open && (
          <span
            aria-hidden
            className="absolute inset-0 rounded-full bg-primary/30 motion-safe:animate-ping [animation-duration:2.6s]"
          />
        )}
        <span className="relative grid place-items-center">
          {open ? (
            <X className="size-5" />
          ) : (
            <Feather className="size-[1.35rem] -rotate-12" strokeWidth={1.9} />
          )}
        </span>
      </button>

      {open && (
        <div className="fixed bottom-20 right-5 z-50 flex h-[34rem] w-[24rem] max-w-[calc(100vw-2.5rem)] flex-col overflow-hidden rounded-2xl border border-border bg-card shadow-2xl">
          <div className="flex items-center gap-2.5 border-b border-border px-4 py-3">
            <Avatar className="size-7 rounded-full">
              <AvatarFallback className="bg-accent text-[var(--accent-foreground)]">
                <Feather className="size-3.5 -rotate-12" />
              </AvatarFallback>
            </Avatar>
            <div className="leading-tight">
              <div className="text-sm font-semibold text-foreground">Wren</div>
              <div className="text-xs text-muted-foreground">
                {campaignId ? "This campaign" : "Your agent"}
              </div>
            </div>
          </div>

          <div ref={scrollRef} className="flex-1 space-y-4 overflow-y-auto px-4 py-4">
            {messages.length === 0 && (
              <p className="text-sm text-muted-foreground">{greeting}</p>
            )}
            {messages.map((m, i) =>
              m.role === "user" ? (
                <div key={i} className="flex justify-end">
                  <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-primary px-3.5 py-2 text-sm text-primary-foreground">
                    {m.text}
                  </div>
                </div>
              ) : (
                <div key={i} className="space-y-2">
                  <div className="flex items-end gap-2">
                    <Avatar className="size-7 shrink-0 rounded-full">
                      <AvatarFallback className="bg-accent text-[var(--accent-foreground)]">
                        <Feather className="size-3.5 -rotate-12" />
                      </AvatarFallback>
                    </Avatar>
                    <div className="max-w-[85%] rounded-2xl rounded-bl-sm border border-border bg-card px-3.5 py-2 text-sm text-foreground shadow-sm">
                      {m.text}
                    </div>
                  </div>
                  <ChatEntities entities={m.entities} className="pl-9" />
                </div>
              ),
            )}
            {chat.isPending && (
              <div className="flex items-end gap-2">
                <Avatar className="size-7 shrink-0 rounded-full">
                  <AvatarFallback className="bg-accent text-[var(--accent-foreground)]">
                    <Feather className="size-3.5 -rotate-12" />
                  </AvatarFallback>
                </Avatar>
                <div className="rounded-2xl rounded-bl-sm border border-border bg-card px-3.5 py-2.5 shadow-sm">
                  <Loader2 className="size-4 animate-spin text-muted-foreground" />
                </div>
              </div>
            )}
          </div>

          <div className="space-y-2.5 border-t border-border px-4 py-3">
            <div className="flex flex-wrap gap-2">
              {suggestions.map((s) => (
                <button
                  key={s}
                  type="button"
                  disabled={chat.isPending}
                  onClick={() => void send(s)}
                  className="rounded-full border border-border bg-secondary/40 px-3 py-1 text-xs font-medium text-muted-foreground transition-colors hover:border-primary/40 hover:text-foreground disabled:opacity-50"
                >
                  {s}
                </button>
              ))}
            </div>
            <form
              className="flex items-center gap-2"
              onSubmit={(e) => {
                e.preventDefault();
                void send(input);
              }}
            >
              <Input
                value={input}
                onChange={(e) => setInput(e.target.value)}
                placeholder="Ask Wren…"
                disabled={chat.isPending}
              />
              <Button type="submit" size="icon" disabled={chat.isPending || !input.trim()}>
                {chat.isPending ? <Loader2 className="animate-spin" /> : <Send />}
              </Button>
            </form>
          </div>
        </div>
      )}
    </>
  );
}
