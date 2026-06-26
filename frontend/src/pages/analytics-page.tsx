import { ChannelIcon } from "@/components/brand-icons";
import { DataError } from "@/components/data-error";
import { StateBadge } from "@/components/state-badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useAnalytics } from "@/lib/api/queries";
import { shortAgo } from "@/lib/format";
import { cn } from "@/lib/utils";

const pct = (x: number) => `${Math.round(x * 100)}%`;

/** Reporting (Analytics) — rendered as a tab inside Settings. */
export function ReportingTab() {
  const { data, isError, refetch } = useAnalytics();

  if (isError) return <DataError onRetry={() => void refetch()} />;

  return (
    <div className="space-y-6">
      {!data ? (
        <div className="space-y-6">
          <Skeleton className="h-56" />
          <div className="grid gap-4 sm:grid-cols-2">
            <Skeleton className="h-28" />
            <Skeleton className="h-28" />
          </div>
        </div>
      ) : (
        <>
          {/* funnel */}
          <Card>
            <CardHeader>
              <CardTitle>Funnel</CardTitle>
              <span className="text-xs text-muted-foreground">conversion at each stage</span>
            </CardHeader>
            <CardContent className="space-y-3">
              <FunnelRow label="Sourced" value={data.funnel.sourced} total={data.funnel.sourced} />
              <FunnelRow label="Contacted" value={data.funnel.contacted} total={data.funnel.sourced} conv={ratio(data.funnel.contacted, data.funnel.sourced)} />
              <FunnelRow label="Replied" value={data.funnel.replied} total={data.funnel.sourced} conv={ratio(data.funnel.replied, data.funnel.contacted)} />
              <FunnelRow label="Handed off" value={data.funnel.handed_off} total={data.funnel.sourced} conv={ratio(data.funnel.handed_off, data.funnel.replied)} accent />
            </CardContent>
          </Card>

          {/* channels */}
          <div className="grid gap-4 sm:grid-cols-2">
            {data.channels.map((ch) => (
              <Card key={ch.channel} className="p-5">
                <div className="flex items-center gap-2 font-display font-semibold">
                  <ChannelIcon channel={ch.channel} className="size-4 text-muted-foreground" />
                  {ch.channel === "linkedin" ? "LinkedIn" : "Email"}
                </div>
                <div className="mt-3 flex items-end justify-between">
                  <div>
                    <div className="font-display text-3xl font-bold tracking-tight">{pct(ch.reply_rate)}</div>
                    <div className="text-xs text-muted-foreground">reply rate</div>
                  </div>
                  <div className="text-right text-sm text-muted-foreground">
                    <div><span className="font-mono font-semibold text-foreground">{ch.sent}</span> sent</div>
                    <div><span className="font-mono font-semibold text-foreground">{ch.replied}</span> replied</div>
                  </div>
                </div>
              </Card>
            ))}
          </div>

          {/* campaigns */}
          <Card>
            <CardHeader>
              <CardTitle>Campaign performance</CardTitle>
            </CardHeader>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Campaign</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead className="text-right">Sourced</TableHead>
                  <TableHead className="text-right">Replied</TableHead>
                  <TableHead className="text-right">Reply rate</TableHead>
                  <TableHead className="text-right">Handed off</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {data.campaigns.map((c) => (
                  <TableRow key={c.id}>
                    <TableCell className="font-semibold text-foreground">{c.name}</TableCell>
                    <TableCell><StateBadge state={c.status} /></TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{c.sourced}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{c.replied}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{pct(c.reply_rate)}</TableCell>
                    <TableCell className="text-right font-mono tabular-nums">{c.handed_off}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </Card>

          {/* activity / audit */}
          <Card>
            <CardHeader>
              <CardTitle>Activity</CardTitle>
              <span className="text-xs text-muted-foreground">recent sends &amp; replies</span>
            </CardHeader>
            <CardContent className="space-y-1">
              {data.activity.map((a) => (
                <div key={a.id} className="flex items-center gap-3 border-b border-border/50 py-2.5 last:border-0">
                  <span className={cn("grid size-7 shrink-0 place-items-center rounded-md", a.type === "reply" ? "bg-accent text-[var(--accent-strong)]" : "bg-secondary text-muted-foreground")}>
                    <ChannelIcon channel={a.channel} className="size-3.5" />
                  </span>
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-foreground">{a.title}</div>
                    <div className="truncate text-xs text-muted-foreground">{a.campaign_name} · {a.body}</div>
                  </div>
                  <span className="shrink-0 text-[0.65rem] text-muted-foreground">{shortAgo(a.created_at)}</span>
                </div>
              ))}
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

function ratio(num: number, den: number) {
  return den ? num / den : 0;
}

function FunnelRow({ label, value, total, conv, accent }: { label: string; value: number; total: number; conv?: number; accent?: boolean }) {
  const width = total ? Math.max((value / total) * 100, 4) : 0;
  return (
    <div>
      <div className="mb-1 flex items-center justify-between text-sm">
        <span className="font-medium text-foreground">{label}</span>
        <span className="flex items-center gap-2">
          {conv !== undefined && <span className="font-mono text-xs text-muted-foreground">{pct(conv)}</span>}
          <span className="font-mono font-semibold tabular-nums">{value}</span>
        </span>
      </div>
      <div className="h-7 overflow-hidden rounded-md bg-secondary/50">
        <div
          className={cn("h-full rounded-md", accent ? "bg-gradient-to-r from-score-from to-score-to" : "bg-accent")}
          style={{ width: `${width}%` }}
        />
      </div>
    </div>
  );
}
