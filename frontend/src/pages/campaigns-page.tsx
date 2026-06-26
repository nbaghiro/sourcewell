import { Plus, Send } from "lucide-react";
import { useNavigate } from "react-router-dom";

import { AUTONOMY, stopFrom } from "@/components/autonomy-dial";
import { DataError } from "@/components/data-error";
import { EmptyState } from "@/components/empty-state";
import { PageHeader } from "@/components/page-header";
import { PageLayout } from "@/components/page-layout";
import { StateBadge } from "@/components/state-badge";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useCampaigns } from "@/lib/api/queries";

export function CampaignsPage() {
  const { data, isLoading, isError, refetch } = useCampaigns();
  const navigate = useNavigate();

  return (
    <PageLayout>
      <PageHeader
        eyebrow="Outreach"
        title="Campaigns"
        description="Each campaign defines who to reach (criteria) and how (a multi-touchpoint sequence)."
      >
        <Button size="sm" onClick={() => navigate("/campaigns/new")}>
          <Plus /> New campaign
        </Button>
      </PageHeader>

      {isError ? (
        <DataError onRetry={() => void refetch()} />
      ) : isLoading ? (
        <Skeleton className="h-80" />
      ) : !data || data.length === 0 ? (
        <EmptyState icon={Send} title="No campaigns yet" description="Create a campaign to start sourcing." />
      ) : (
        <Card>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Campaign</TableHead>
                <TableHead>Status</TableHead>
                <TableHead>Autonomy</TableHead>
                <TableHead>Targets</TableHead>
                <TableHead className="text-right">Touchpoints</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {data.map((c) => (
                <TableRow key={c.id} className="cursor-pointer" onClick={() => navigate(`/campaigns/${c.id}`)}>
                  <TableCell className="font-semibold text-foreground">{c.name}</TableCell>
                  <TableCell>
                    <StateBadge state={c.status} />
                  </TableCell>
                  <TableCell className="font-mono text-xs text-muted-foreground">
                    {AUTONOMY[stopFrom(c.autonomy_level, c.autonomy_mode)].label}
                  </TableCell>
                  <TableCell>
                    <div className="flex flex-wrap gap-1">
                      {((c.criteria as { skills?: string[] }).skills ?? []).slice(0, 3).map((s) => (
                        <Badge key={s} variant="secondary">
                          {s}
                        </Badge>
                      ))}
                    </div>
                  </TableCell>
                  <TableCell className="text-right font-mono tabular-nums">{c.sequence.length}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Card>
      )}
    </PageLayout>
  );
}
