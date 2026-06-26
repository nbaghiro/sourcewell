import { Search, Send } from "lucide-react";
import * as React from "react";
import { useNavigate } from "react-router-dom";

import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { useSearch } from "@/lib/api/queries";
import { initials } from "@/lib/format";
import { cn } from "@/lib/utils";

interface FlatItem {
  key: string;
  label: string;
  sub?: string;
  avatar?: string | null;
  nav: string;
  group: string;
  isCampaign?: boolean;
}

export function CommandPalette() {
  const navigate = useNavigate();
  const [open, setOpen] = React.useState(false);
  const [q, setQ] = React.useState("");
  const [debounced, setDebounced] = React.useState("");
  const [active, setActive] = React.useState(0);
  const { data: results } = useSearch(debounced);

  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setOpen((o) => !o);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  React.useEffect(() => {
    const t = setTimeout(() => setDebounced(open ? q : ""), 160);
    return () => clearTimeout(t);
  }, [q, open]);

  const flat = React.useMemo<FlatItem[]>(() => {
    if (!results) return [];
    const f: FlatItem[] = [];
    results.contacts.forEach((c) => f.push({ key: "c" + c.id, label: c.full_name, sub: c.title ?? undefined, avatar: c.avatar_url, nav: `/people/${c.id}`, group: "People" }));
    results.campaigns.forEach((c) => f.push({ key: "k" + c.id, label: c.name, sub: c.status, nav: `/campaigns/${c.id}`, group: "Campaigns", isCampaign: true }));
    results.conversations.forEach((c) => f.push({ key: "v" + c.enrollment_id, label: c.contact_name, sub: c.state.replace(/_/g, " "), avatar: c.avatar_url, nav: `/inbox`, group: "Conversations" }));
    return f;
  }, [results]);

  React.useEffect(() => setActive(0), [flat.length]);

  function go(item: FlatItem) {
    navigate(item.nav);
    setOpen(false);
    setQ("");
    setDebounced("");
  }
  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((a) => Math.min(a + 1, flat.length - 1));
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((a) => Math.max(a - 1, 0));
    } else if (e.key === "Enter" && flat[active]) {
      e.preventDefault();
      go(flat[active]);
    }
  }

  const groups = ["People", "Campaigns", "Conversations"]
    .map((g) => ({ g, items: flat.filter((i) => i.group === g) }))
    .filter((x) => x.items.length > 0);

  return (
    <>
      <button
        onClick={() => setOpen(true)}
        className="flex items-center gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm text-muted-foreground transition-colors hover:text-foreground"
      >
        <Search className="size-4" />
        <span className="hidden sm:inline">Search</span>
        <kbd className="ml-1 hidden rounded border border-border bg-secondary/60 px-1.5 font-mono text-[0.65rem] sm:inline">⌘K</kbd>
      </button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-w-xl gap-0 overflow-hidden p-0">
          <div className="flex items-center gap-2 border-b border-border px-4 pr-10">
            <Search className="size-4 shrink-0 text-muted-foreground" />
            <input
              autoFocus
              value={q}
              onChange={(e) => setQ(e.target.value)}
              onKeyDown={onKeyDown}
              placeholder="Search people, campaigns, conversations…"
              className="w-full bg-transparent py-3.5 text-sm outline-none placeholder:text-muted-foreground"
            />
          </div>
          <div className="max-h-80 overflow-y-auto p-2">
            {flat.length === 0 ? (
              <div className="px-3 py-8 text-center text-sm text-muted-foreground">
                {q.trim() ? "No matches." : "Type to search."}
              </div>
            ) : (
              groups.map((grp) => (
                <div key={grp.g} className="mb-1">
                  <div className="px-2 py-1 font-mono text-[0.6rem] font-semibold uppercase tracking-wider text-muted-foreground">
                    {grp.g}
                  </div>
                  {grp.items.map((item) => {
                    const idx = flat.indexOf(item);
                    return (
                      <button
                        key={item.key}
                        onMouseEnter={() => setActive(idx)}
                        onClick={() => go(item)}
                        className={cn(
                          "flex w-full items-center gap-2.5 rounded-md px-2 py-2 text-left",
                          active === idx ? "bg-accent" : "hover:bg-secondary/40",
                        )}
                      >
                        {item.isCampaign ? (
                          <span className="grid size-7 shrink-0 place-items-center rounded-md bg-secondary text-muted-foreground">
                            <Send className="size-3.5" />
                          </span>
                        ) : (
                          <Avatar className="size-7">
                            {item.avatar && <AvatarImage src={item.avatar} alt="" />}
                            <AvatarFallback className="text-[0.6rem]">{initials(item.label)}</AvatarFallback>
                          </Avatar>
                        )}
                        <div className="min-w-0 flex-1">
                          <div className="truncate text-sm font-medium text-foreground">{item.label}</div>
                          {item.sub && <div className="truncate text-xs capitalize text-muted-foreground">{item.sub}</div>}
                        </div>
                      </button>
                    );
                  })}
                </div>
              ))
            )}
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
