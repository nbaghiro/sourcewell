import * as React from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { CampaignComposer, type Step } from "@/components/campaign-composer";
import { PageLayout } from "@/components/page-layout";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/segmented";
import { useContacts, useCreateCampaign } from "@/lib/api/queries";
import { emptyTargeting, type Targeting } from "@/lib/targeting";
import { useWorkspaceId } from "@/lib/workspace";

// Most frequent values first (for deriving sensible starting criteria from a contact pool).
function topOf(items: string[], n = 1): string[] {
  const m = new Map<string, number>();
  for (const x of items) m.set(x, (m.get(x) ?? 0) + 1);
  return [...m.entries()]
    .sort((a, b) => b[1] - a[1])
    .slice(0, n)
    .map(([k]) => k);
}
const EU_TOKENS = ["de", "uk", "nl", "pt", "ie", "fr", "es", "it", "remote · eu"];
const isEU = (loc?: string | null) => EU_TOKENS.some((t) => (loc ?? "").toLowerCase().includes(t));

export function CampaignBuilderPage() {
  // Remount on workspace switch so all local state (name, audience, sequence) resets and the
  // audience re-seeds from the new workspace's contacts instead of keeping the old vertical's.
  const ws = useWorkspaceId();
  return <CampaignBuilderInner key={ws ?? "none"} />;
}

function CampaignBuilderInner() {
  const navigate = useNavigate();
  const { data: pool } = useContacts();
  const createCampaign = useCreateCampaign();

  const [name, setName] = React.useState("New campaign");
  const [autonomy, setAutonomy] = React.useState<"approve_each" | "auto">("approve_each");
  const [criteria, setCriteria] = React.useState<Targeting>(emptyTargeting());
  const [steps, setSteps] = React.useState<Step[]>([
    { channel: "email", delay_days: 0, subject: "Quick question, {first_name}", body: "Came across your work at {company} — open to a quick chat?" },
    { channel: "linkedin", delay_days: 3, subject: "", body: "Following up here, {first_name} — still worth a conversation?" },
  ]);
  const saving = createCampaign.isPending;

  // Seed the starting criteria from the workspace's own contacts (most common title + skills +
  // dominant region), so a new campaign begins with a sensible, on-target audience for THIS vertical.
  const seeded = React.useRef(false);
  React.useEffect(() => {
    if (seeded.current || !pool || pool.length === 0) return;
    seeded.current = true;
    const topTitle = topOf(pool.map((c) => c.title).filter((t): t is string => !!t))[0];
    const topSkills = topOf(pool.flatMap((c) => c.skills ?? []), 2);
    const euShare = pool.filter((c) => isEU(c.location)).length / pool.length;
    setName(topTitle ? `${topTitle} outreach` : "New campaign");
    setCriteria({ ...emptyTargeting(), titles: topTitle ? [topTitle] : [], skills: topSkills, locations: euShare > 0.5 ? ["EU"] : [] });
  }, [pool]);

  function save() {
    createCampaign.mutate(
      { name, criteria, sequence: steps, autonomy_mode: autonomy },
      {
        onSuccess: () => {
          toast.success("Campaign created", { description: `${name} · ${steps.length} touches` });
          navigate("/campaigns");
        },
        onError: () => toast.error("Couldn't save the campaign"),
      },
    );
  }

  return (
    <PageLayout>
      <div className="flex flex-wrap items-center gap-3 border-b border-border pb-4">
        <div className="min-w-[220px] flex-1">
          <p className="font-mono text-[0.6rem] font-semibold uppercase tracking-[0.14em] text-muted-foreground">
            New campaign
          </p>
          <input
            value={name}
            onChange={(e) => setName(e.target.value)}
            placeholder="Campaign name"
            className="w-full bg-transparent font-display text-2xl font-bold tracking-tight text-foreground outline-none placeholder:text-muted-foreground/50"
          />
        </div>
        <Segmented
          value={autonomy}
          onChange={(v) => setAutonomy(v as "approve_each" | "auto")}
          options={[
            { value: "approve_each", label: "Approve each" },
            { value: "auto", label: "Auto-send" },
          ]}
        />
        <Button variant="ghost" size="sm" onClick={() => navigate("/campaigns")}>
          Cancel
        </Button>
        <Button size="sm" disabled={saving} onClick={() => void save()}>
          {saving ? "Saving…" : "Save"}
        </Button>
      </div>

      <CampaignComposer
        criteria={criteria}
        steps={steps}
        pool={pool}
        onCriteriaChange={setCriteria}
        onStepsChange={setSteps}
      />
    </PageLayout>
  );
}
