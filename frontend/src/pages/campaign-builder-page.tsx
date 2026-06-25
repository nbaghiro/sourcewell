import * as React from "react";
import { useNavigate } from "react-router-dom";
import { toast } from "sonner";

import { CampaignComposer, type Step } from "@/components/campaign-composer";
import { CampaignIntake, type IntakeResult } from "@/components/campaign-intake";
import { PageLayout } from "@/components/page-layout";
import { Button } from "@/components/ui/button";
import { Segmented } from "@/components/ui/segmented";
import { useContacts, useCreateCampaign } from "@/lib/api/queries";
import { emptyTargeting, type Targeting } from "@/lib/targeting";
import { useWorkspaceId } from "@/lib/workspace";

const DEFAULT_STEPS: Step[] = [
  {
    channel: "email",
    delay_days: 0,
    subject: "Quick question, {first_name}",
    body: "Came across your work at {company} — open to a quick chat?",
  },
  {
    channel: "linkedin",
    delay_days: 3,
    subject: "",
    body: "Following up here, {first_name} — still worth a conversation?",
  },
];

export function CampaignBuilderPage() {
  // Remount on workspace switch so all local state resets for the new workspace.
  const ws = useWorkspaceId();
  return <CampaignBuilderInner key={ws ?? "none"} />;
}

function CampaignBuilderInner() {
  const navigate = useNavigate();
  const { data: pool } = useContacts();
  const createCampaign = useCreateCampaign();

  const [phase, setPhase] = React.useState<"brief" | "build">("brief");
  const [name, setName] = React.useState("New campaign");
  const [objective, setObjective] = React.useState("");
  const [seedContactIds, setSeedContactIds] = React.useState<string[]>([]);
  const [autonomy, setAutonomy] = React.useState<"approve_each" | "auto">("approve_each");
  const [criteria, setCriteria] = React.useState<Targeting>(emptyTargeting());
  const [steps, setSteps] = React.useState<Step[]>(DEFAULT_STEPS);
  const saving = createCampaign.isPending;

  function onIntakeComplete(r: IntakeResult) {
    setName(r.name || "New campaign");
    setObjective(r.objective);
    setCriteria(r.criteria);
    setSeedContactIds(r.seedContactIds);
    setPhase("build");
  }

  function save() {
    createCampaign.mutate(
      { name, criteria, sequence: steps, autonomy_mode: autonomy },
      {
        onSuccess: () => {
          toast.success("Campaign created", {
            description: `${name} · ${steps.length} touchpoints`,
          });
          navigate("/campaigns");
        },
        onError: () => toast.error("Couldn't save the campaign"),
      },
    );
  }

  if (phase === "brief") {
    return (
      <PageLayout>
        <CampaignIntake
          pool={pool}
          onComplete={onIntakeComplete}
          onCancel={() => navigate("/campaigns")}
        />
      </PageLayout>
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
          {(objective || seedContactIds.length > 0) && (
            <p className="mt-0.5 truncate text-xs text-muted-foreground">
              {objective || `Sourcing people like ${seedContactIds.length} example${seedContactIds.length === 1 ? "" : "s"}`}
            </p>
          )}
        </div>
        <Segmented
          value={autonomy}
          onChange={(v) => setAutonomy(v as "approve_each" | "auto")}
          options={[
            { value: "approve_each", label: "Approve each" },
            { value: "auto", label: "Auto-send" },
          ]}
        />
        <Button variant="ghost" size="sm" onClick={() => setPhase("brief")}>
          Start over
        </Button>
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
