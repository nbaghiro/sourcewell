import {
  Ban,
  Check,
  ChevronDown,
  Loader2,
  Mail,
  Search,
  Send,
  ShieldCheck,
  Sparkles,
  Target,
  UserPlus,
  Users,
  UserSearch,
} from "lucide-react";
import * as React from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { PageHeader } from "@/components/page-header";
import { PageLayout } from "@/components/page-layout";
import { PersonCell } from "@/components/person-cell";
import { TargetingEditor } from "@/components/targeting-editor";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Skeleton } from "@/components/ui/skeleton";
import { client, unwrap } from "@/lib/api/client";
import {
  type PersonHit,
  useCampaigns,
  useContacts,
  useImportPeople,
  useParsePeopleQuery,
  usePeopleProviders,
  useSearchPeople,
  useSuppressions,
} from "@/lib/api/queries";
import { emptyTargeting, targetingHasCriteria, toTargeting, type Targeting } from "@/lib/targeting";
import { cn } from "@/lib/utils";

const hitKey = (h: PersonHit, i: number) => h.external_id ?? h.email ?? `${h.full_name}-${i}`;
const norm = (s?: string | null) => (s ?? "").toLowerCase().trim().replace(/\/$/, "");

interface Example {
  label: string;
  targeting: Partial<Targeting>;
}
const CURATED: Example[] = [
  { label: "VPs of Sales in the EU", targeting: { titles: ["VP of Sales"], skills: ["Salesforce"], locations: ["EU"] } },
  { label: "Heads of Partnerships", targeting: { titles: ["Head of Partnerships"], skills: ["Channel"] } },
  { label: "Senior Backend Engineers · Go", targeting: { titles: ["Senior Backend Engineer"], skills: ["Go", "Postgres"], locations: ["EU"] } },
  { label: "RevOps leaders", targeting: { titles: ["Head of RevOps"], skills: ["HubSpot"] } },
];

export function FindPeoplePage() {
  const navigate = useNavigate();
  const search = useSearchPeople();
  const parse = useParsePeopleQuery();
  const importPeople = useImportPeople();
  const { data: providers } = usePeopleProviders();
  const { data: pool } = useContacts();
  const { data: suppressions } = useSuppressions();
  const { data: campaigns } = useCampaigns();

  const [targeting, setTargeting] = React.useState<Targeting>(emptyTargeting());
  const [enabled, setEnabled] = React.useState<Set<string>>(new Set());
  const [results, setResults] = React.useState<PersonHit[]>([]);
  const [used, setUsed] = React.useState<string[]>([]);
  const [searched, setSearched] = React.useState(false);
  const [picked, setPicked] = React.useState<Set<string>>(new Set());
  const [enrolling, setEnrolling] = React.useState(false);

  const searchProviders = React.useMemo(() => (providers ?? []).filter((p) => p.search), [providers]);
  React.useEffect(() => {
    if (searchProviders.length && enabled.size === 0) setEnabled(new Set(searchProviders.map((p) => p.key)));
  }, [searchProviders, enabled.size]);

  const inWorkspace = React.useMemo(() => {
    const emails = new Set<string>();
    const links = new Set<string>();
    for (const c of pool ?? []) {
      if (c.email) emails.add(norm(c.email));
      if (c.linkedin_url) links.add(norm(c.linkedin_url));
    }
    return { emails, links };
  }, [pool]);
  const suppressed = React.useMemo(() => new Set((suppressions ?? []).map((s) => norm(s.email))), [suppressions]);

  function decorate(h: PersonHit) {
    const dup = (h.email && inWorkspace.emails.has(norm(h.email))) || (h.linkedin_url && inWorkspace.links.has(norm(h.linkedin_url)));
    return { dup: !!dup, blocked: !!(h.email && suppressed.has(norm(h.email))) };
  }

  const examples = React.useMemo<Example[]>(() => {
    const p = pool ?? [];
    if (!p.length) return CURATED;
    const counts = new Map<string, number>();
    for (const c of p) if (c.title) counts.set(c.title, (counts.get(c.title) ?? 0) + 1);
    const top = [...counts.entries()].sort((a, b) => b[1] - a[1])[0]?.[0];
    if (!top) return CURATED;
    const sk = (p.find((c) => c.title === top)?.skills ?? []).slice(0, 2);
    return [{ label: `Like your workspace · ${top}`, targeting: { titles: [top], skills: sk } }, ...CURATED];
  }, [pool]);

  const hasCriteria = targetingHasCriteria(targeting);

  // Clearing every filter abandons the prior search: drop the stale results and return to the
  // onboarding state instead of stranding results that no longer match the (now empty) criteria.
  React.useEffect(() => {
    if (!hasCriteria && searched) {
      setSearched(false);
      setResults([]);
      setPicked(new Set());
    }
  }, [hasCriteria, searched]);

  function runParse() {
    const kw = targeting.keywords.trim();
    if (!kw) return;
    parse.mutate(kw, {
      onSuccess: (r) => {
        setTargeting((t) => ({
          ...t,
          titles: [...new Set([...t.titles, ...r.titles])],
          skills: [...new Set([...t.skills, ...r.skills])],
          locations: [...new Set([...t.locations, ...r.locations])],
          keywords: r.keywords,
        }));
        toast.success("Parsed your search");
      },
      onError: () => toast.error("Couldn't parse"),
    });
  }

  function doSearch(t: Targeting) {
    if (!targetingHasCriteria(t)) return;
    search.mutate(
      { ...t, keywords: t.keywords.trim() || null, limit: 25, providers: [...enabled] },
      {
        onSuccess: (data) => {
          setResults(data.results);
          setUsed(data.providers);
          setPicked(new Set());
          setSearched(true);
        },
        onError: () => toast.error("Search failed"),
      },
    );
  }
  const runSearch = () => doSearch(targeting);
  function runExample(ex: Example) {
    const t = { ...emptyTargeting(), ...ex.targeting };
    setTargeting(t);
    doSearch(t);
  }

  function toggle(key: string) {
    setPicked((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }
  const pickedHits = () => results.filter((h, i) => picked.has(hitKey(h, i)));

  function importToContacts() {
    const hits = pickedHits();
    if (!hits.length) return;
    importPeople.mutate(hits, {
      onSuccess: (res) => {
        toast.success(`Imported ${res.imported} to Contacts`);
        navigate("/contacts");
      },
      onError: () => toast.error("Import failed"),
    });
  }
  async function enrollInto(campaignId: string, campaignName: string) {
    const hits = pickedHits();
    if (!hits.length) return;
    setEnrolling(true);
    try {
      const res = await importPeople.mutateAsync(hits);
      await Promise.all(
        res.contact_ids.map((cid) =>
          client.POST("/campaigns/{campaign_id}/enroll", { params: { path: { campaign_id: campaignId } }, body: { contact_id: cid } }).then(unwrap),
        ),
      );
      toast.success(`Enrolled ${res.contact_ids.length} into ${campaignName}`);
      navigate(`/campaigns/${campaignId}`);
    } catch {
      toast.error("Couldn't enroll");
    } finally {
      setEnrolling(false);
    }
  }

  // ---- shared blocks ----
  const criteria = (
    <Card className="h-fit lg:sticky lg:top-4">
      <CardHeader>
        <CardTitle>Criteria</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="grid gap-1.5">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">Describe who you want</span>
            <Sparkles className="size-3.5 text-muted-foreground" />
          </div>
          <Input value={targeting.keywords} onChange={(e) => setTargeting({ ...targeting, keywords: e.target.value })} onKeyDown={(e) => e.key === "Enter" && runParse()} placeholder="Heads of partnerships at EU dev-tool cos…" />
          <Button variant="outline" size="sm" className="justify-center" disabled={!targeting.keywords.trim() || parse.isPending} onClick={runParse}>
            {parse.isPending ? <Loader2 className="size-4 animate-spin" /> : <Sparkles className="size-4" />} Parse into filters
          </Button>
        </div>
        <TargetingEditor value={targeting} onChange={setTargeting} />
        <div className="flex flex-wrap gap-2">
          <SeedMenu campaigns={campaigns} pool={pool} onCriteria={(t) => setTargeting({ ...emptyTargeting(), ...t })} />
        </div>
        {searchProviders.length > 0 && (
          <div className="grid gap-1.5">
            <span className="text-sm font-medium">Providers</span>
            <div className="flex flex-wrap gap-1.5">
              {searchProviders.map((p) => {
                const on = enabled.has(p.key);
                return (
                  <button
                    key={p.key}
                    onClick={() => setEnabled((prev) => { const next = new Set(prev); if (next.has(p.key)) next.delete(p.key); else next.add(p.key); return next; })}
                    className={cn("rounded-full border px-2.5 py-1 font-mono text-[11px] uppercase transition-colors", on ? "border-primary bg-primary/10 text-primary" : "border-border text-muted-foreground")}
                  >
                    {p.key}
                  </button>
                );
              })}
            </div>
          </div>
        )}
        <Button className="w-full" disabled={!hasCriteria || search.isPending} onClick={runSearch}>
          {search.isPending ? <Loader2 className="size-4 animate-spin" /> : <Search className="size-4" />} Search
        </Button>
      </CardContent>
    </Card>
  );

  const resultRow = (h: PersonHit, i: number) => {
    const key = hitKey(h, i);
    const sel = picked.has(key);
    const { dup, blocked } = decorate(h);
    return (
      <button
        key={key}
        disabled={dup}
        onClick={() => toggle(key)}
        className={cn(
          "flex w-full items-center gap-3 rounded-xl border p-4 text-left transition-colors",
          dup ? "border-border bg-secondary/30 opacity-70" : sel ? "border-primary bg-primary/5" : "border-border bg-card hover:border-primary/40",
        )}
      >
        <span className={cn("grid size-5 shrink-0 place-items-center rounded border", sel ? "border-primary bg-primary text-primary-foreground" : "border-input")}>
          {sel && <Check className="size-3.5" />}
        </span>
        <PersonCell name={h.full_name} subtitle={[h.title, h.company, h.location].filter(Boolean).join(" · ") || undefined} imageSrc={h.avatar_url ?? undefined} />
        <div className="ml-auto flex shrink-0 items-center gap-2">
          {dup && <Badge variant="secondary" className="text-[10px]">In workspace</Badge>}
          {blocked && <Badge variant="warning" className="gap-1 text-[10px]"><Ban className="size-3" /> Suppressed</Badge>}
          <Mail className={cn("size-4", h.email ? "text-muted-foreground" : "text-muted-foreground/25")} />
          <Badge variant="outline" className="font-mono text-[10px] uppercase">{h.provider}</Badge>
          <span className="w-7 text-right font-mono text-sm font-semibold tabular-nums text-primary">{h.score}</span>
        </div>
      </button>
    );
  };

  const renderResults = (cols: 1 | 2) => (
    <div className="space-y-3">
      {searched && !search.isPending && (
        <div className="flex items-center justify-between text-sm text-muted-foreground">
          <span>{results.length} result{results.length === 1 ? "" : "s"}{used.length > 0 && <> · via {used.join(", ")}</>}</span>
          {results.length > 0 && (
            <button className="text-primary hover:underline" onClick={() => setPicked(new Set(results.map((h, i) => ({ h, k: hitKey(h, i) })).filter(({ h }) => !decorate(h).dup).map(({ k }) => k)))}>
              Select all
            </button>
          )}
        </div>
      )}
      {search.isPending ? (
        <div className={cols === 2 ? "grid gap-3 sm:grid-cols-2" : "space-y-3"}>
          {[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-[76px] rounded-xl" />)}
        </div>
      ) : results.length > 0 ? (
        <div className={cols === 2 ? "grid gap-3 sm:grid-cols-2" : "space-y-3"}>{results.map(resultRow)}</div>
      ) : (
        <Hint icon={<UserSearch className="size-6" />} title="No matches" body="Try broadening the titles, skills, or locations." />
      )}
    </div>
  );

  const enrollMenu = (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="outline" className="w-full" disabled={enrolling || !(campaigns ?? []).length}>
          {enrolling ? <Loader2 className="size-4 animate-spin" /> : null} Enroll into campaign <ChevronDown className="ml-auto size-4" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="max-h-72 overflow-y-auto">
        <DropdownMenuLabel>Enroll {picked.size} into…</DropdownMenuLabel>
        {(campaigns ?? []).map((c) => (
          <DropdownMenuItem key={c.id} onClick={() => void enrollInto(c.id, c.name)}>{c.name}</DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );

  const shortlistPanel = (
    <Card className="h-fit lg:sticky lg:top-4">
      <CardHeader>
        <CardTitle>Shortlist</CardTitle>
        <span className="text-xs text-muted-foreground">~{picked.size} credit{picked.size === 1 ? "" : "s"}</span>
      </CardHeader>
      <CardContent className="space-y-3">
        {picked.size === 0 ? (
          <p className="py-6 text-center text-sm text-muted-foreground">Pick people from the results to build a shortlist.</p>
        ) : (
          <>
            <div className="max-h-64 space-y-1.5 overflow-y-auto">
              {pickedHits().map((h, i) => (
                <div key={hitKey(h, i)} className="flex items-center justify-between gap-2 text-sm">
                  <span className="truncate text-foreground">{h.full_name}</span>
                  <span className="shrink-0 font-mono text-xs text-primary">{h.score}</span>
                </div>
              ))}
            </div>
            <Button className="w-full" disabled={importPeople.isPending || enrolling} onClick={importToContacts}>
              {importPeople.isPending && !enrolling ? <Loader2 className="size-4 animate-spin" /> : <UserPlus className="size-4" />} Import {picked.size} to Contacts
            </Button>
            {enrollMenu}
          </>
        )}
      </CardContent>
    </Card>
  );

  // Onboarding (nothing searched yet) drops the empty results + shortlist columns for a roomy
  // intro; once you search, the full criteria · results · shortlist working layout takes over.
  const working = searched || search.isPending;

  return (
    <PageLayout width="wide">
      <PageHeader eyebrow="Sourcing" title="Find people" description="Search public B2B people data, vetted against your contacts and audience, then import or enroll." />
      {working ? (
        <div className="grid gap-5 lg:grid-cols-[300px_minmax(0,1fr)_280px]">
          {criteria}
          {renderResults(1)}
          {shortlistPanel}
        </div>
      ) : (
        <div className="grid gap-5 lg:grid-cols-[300px_minmax(0,1fr)]">
          {criteria}
          <StudioIntro examples={examples} onPick={runExample} />
        </div>
      )}
    </PageLayout>
  );
}

function SeedMenu({
  campaigns,
  pool,
  onCriteria,
}: {
  campaigns: { id: string; name: string; criteria: Partial<Targeting> }[] | undefined;
  pool: { title?: string | null; skills?: string[]; location?: string | null; industry?: string | null }[] | undefined;
  onCriteria: (t: Partial<Targeting>) => void;
}) {
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm">
            <Sparkles className="size-3.5" /> Use audience <ChevronDown className="size-3.5" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="max-h-72 overflow-y-auto">
          <DropdownMenuLabel>From a campaign's audience</DropdownMenuLabel>
          {(campaigns ?? []).map((c) => (
            <DropdownMenuItem key={c.id} onClick={() => onCriteria(toTargeting(c.criteria))}>
              {c.name}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button variant="outline" size="sm">
            <Users className="size-3.5" /> Lookalike <ChevronDown className="size-3.5" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="max-h-72 overflow-y-auto">
          <DropdownMenuLabel>Find people like…</DropdownMenuLabel>
          {(pool ?? []).slice(0, 20).map((c, i) => (
            <DropdownMenuItem
              key={i}
              onClick={() =>
                onCriteria({
                  titles: c.title ? [c.title] : [],
                  skills: (c.skills ?? []).slice(0, 3),
                  locations: c.location ? [c.location] : [],
                  industries: c.industry ? [c.industry] : [],
                })
              }
            >
              {c.title ?? "Contact"} {c.location ? `· ${c.location}` : ""}
            </DropdownMenuItem>
          ))}
        </DropdownMenuContent>
      </DropdownMenu>
    </>
  );
}

function StudioIntro({ examples, onPick }: { examples: Example[]; onPick: (e: Example) => void }) {
  return (
    <Card className="h-fit">
      <CardContent className="p-7 sm:p-8">
        <div className="flex items-start gap-4">
          <div className="grid size-12 shrink-0 place-items-center rounded-xl bg-primary/10 text-primary">
            <UserSearch className="size-6" />
          </div>
          <div>
            <h3 className="font-display text-xl font-bold text-foreground">Source your next contacts</h3>
            <p className="mt-1 max-w-lg text-sm text-muted-foreground">
              Describe who you want in plain English — or use the filters on the left. Results come back
              scored to your audience, checked against your contacts, ready to import or enroll.
            </p>
          </div>
        </div>

        <div className="mt-7 font-mono text-[0.65rem] font-semibold uppercase tracking-wider text-muted-foreground">
          Try a search
        </div>
        <div className="mt-3 grid gap-2.5 sm:grid-cols-2 xl:grid-cols-3">
          {examples.map((e) => (
            <button
              key={e.label}
              onClick={() => onPick(e)}
              className="group flex flex-col gap-2 rounded-lg bg-secondary/40 p-3.5 text-left transition-colors hover:bg-secondary/80"
            >
              <div className="flex items-center justify-between gap-2">
                <span className="truncate text-sm font-semibold text-foreground">{e.label}</span>
                <Search className="size-3.5 shrink-0 text-muted-foreground transition-colors group-hover:text-primary" />
              </div>
              <div className="flex flex-wrap gap-1">
                {[...(e.targeting.titles ?? []), ...(e.targeting.skills ?? []), ...(e.targeting.locations ?? [])].slice(0, 4).map((t) => (
                  <span key={t} className="rounded bg-background/70 px-1.5 py-0.5 text-[10px] text-muted-foreground">{t}</span>
                ))}
              </div>
            </button>
          ))}
        </div>

        <div className="mt-8 grid gap-5 border-t border-border pt-6 sm:grid-cols-2 xl:grid-cols-4">
          <Feature icon={<Sparkles className="size-4" />} title="Plain-English search" body="Describe who you want; we turn it into filters." />
          <Feature icon={<Target className="size-4" />} title="Scored to your audience" body="Ranked by the same fit model your campaigns use." />
          <Feature icon={<ShieldCheck className="size-4" />} title="Deduped & compliant" body="Flags people already in Contacts or suppressed." />
          <Feature icon={<Send className="size-4" />} title="Import or enroll" body="Add to Contacts or drop into a campaign in one click." />
        </div>
      </CardContent>
    </Card>
  );
}

function Feature({ icon, title, body }: { icon: React.ReactNode; title: string; body: string }) {
  return (
    <div>
      <span className="grid size-8 place-items-center rounded-md bg-accent text-accent-foreground">{icon}</span>
      <div className="mt-2 text-sm font-semibold text-foreground">{title}</div>
      <div className="mt-0.5 text-xs leading-relaxed text-muted-foreground">{body}</div>
    </div>
  );
}

function Hint({ icon, title, body }: { icon: React.ReactNode; title: string; body: string }) {
  return (
    <div className="grid place-items-center rounded-xl border border-border bg-card px-6 py-16 text-center">
      <div className="mb-3 grid size-12 place-items-center rounded-full bg-secondary/50 text-muted-foreground">{icon}</div>
      <div className="text-sm font-semibold text-foreground">{title}</div>
      <p className="mt-1 max-w-sm text-sm text-muted-foreground">{body}</p>
    </div>
  );
}
