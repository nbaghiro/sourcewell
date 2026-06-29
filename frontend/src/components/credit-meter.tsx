import { Coins, Mail, UserPlus } from "lucide-react";
import * as React from "react";

import { LinkedInIcon } from "@/components/brand-icons";
import { Badge } from "@/components/ui/badge";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from "@/components/ui/dialog";
import { Meter, usageTone } from "@/components/ui/meter";
import { useAccountUsage } from "@/lib/api/queries";

interface Cost {
  icon: React.ComponentType<{ className?: string }>;
  label: string;
  credits: number;
  desc: string;
}

const COSTS: Cost[] = [
  { icon: Mail, label: "Email sent", credits: 1, desc: "Each outbound email your sequences send." },
  {
    icon: LinkedInIcon,
    label: "LinkedIn InMail",
    credits: 2,
    desc: "Each InMail sent — LinkedIn caps these, so they cost more.",
  },
  {
    icon: UserPlus,
    label: "Candidate sourced",
    credits: 1,
    desc: "Each candidate the agent finds and adds to a campaign.",
  },
];

/** A glanceable credit meter for the sidebar that opens a "how credits work" modal. */
export function CreditMeter() {
  const { data } = useAccountUsage();
  if (!data) return null;
  const pct = Math.min(100, data.pct);
  const bar = usageTone(data.pct, data.over);
  const emails = data.breakdown.emails ?? 0;
  const inmails = data.breakdown.inmails ?? 0;
  const sourced = data.breakdown.sourced ?? 0;

  return (
    <Dialog>
      <DialogTrigger asChild>
        <button className="w-full rounded-md border border-sidebar-border bg-sidebar-hover/50 px-3 py-2.5 text-left transition-colors hover:bg-sidebar-hover">
          <div className="flex items-center justify-between text-xs">
            <span className="flex items-center gap-1.5 font-medium text-sidebar-active-foreground">
              <Coins className="size-3.5" /> Credits
            </span>
            <span className="text-sidebar-foreground/80">{data.pct}%</span>
          </div>
          <Meter pct={pct} tone={bar} className="mt-1.5 h-1.5 bg-black/25" />
          <div className="mt-1.5 text-[0.65rem] text-sidebar-foreground/70">
            {data.used.toLocaleString()} / {data.allowance.toLocaleString()} used
          </div>
        </button>
      </DialogTrigger>

      <DialogContent className="max-w-md">
        <DialogHeader>
          <div className="flex items-center justify-between">
            <DialogTitle>Credits &amp; usage</DialogTitle>
            <Badge variant="accent" className="capitalize">
              {data.plan}
            </Badge>
          </div>
        </DialogHeader>

        <div>
          <div className="flex items-baseline justify-between">
            <span className="font-display text-2xl font-semibold text-foreground">
              {data.used.toLocaleString()}
            </span>
            <span className="text-sm text-muted-foreground">
              of {data.allowance.toLocaleString()} · {data.pct}%
            </span>
          </div>
          <Meter pct={pct} tone={bar} className="mt-2" />
          {data.over ? (
            <p className="mt-2 text-xs text-destructive">
              You're {data.pct - 100}% over — work keeps going; overage is reconciled at billing.
            </p>
          ) : data.pct >= 80 ? (
            <p className="mt-2 text-xs" style={{ color: "var(--warning)" }}>
              You've used {data.pct}% of this period's credits.
            </p>
          ) : null}
        </div>

        <div className="space-y-2.5">
          <h3 className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            What uses credits
          </h3>
          {COSTS.map((c) => (
            <div key={c.label} className="flex items-start gap-3">
              <span className="mt-0.5 grid size-7 shrink-0 place-items-center rounded-md border border-border bg-secondary/40 text-muted-foreground">
                <c.icon className="size-3.5" />
              </span>
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                  <span className="text-sm font-medium text-foreground">{c.label}</span>
                  <Badge variant="secondary" className="text-[0.65rem]">
                    {c.credits} credit{c.credits > 1 ? "s" : ""}
                  </Badge>
                </div>
                <p className="text-xs text-muted-foreground">{c.desc}</p>
              </div>
            </div>
          ))}
        </div>

        <div className="rounded-lg border border-border bg-secondary/30 p-3 text-xs">
          <div className="mb-1.5 font-semibold text-foreground">This period so far</div>
          <PeriodRow label="Emails" count={emails} credits={emails} />
          <PeriodRow label="InMails" count={inmails} credits={inmails * 2} />
          <PeriodRow label="Sourced" count={sourced} credits={sourced} />
        </div>

        <p className="text-xs text-muted-foreground">
          One pooled balance, shared across your workspaces. Resets each billing period; overage is
          allowed and reconciled at billing.
        </p>
      </DialogContent>
    </Dialog>
  );
}

function PeriodRow({ label, count, credits }: { label: string; count: number; credits: number }) {
  return (
    <div className="flex items-center justify-between py-0.5 text-muted-foreground">
      <span>
        {label} · {count.toLocaleString()}
      </span>
      <span className="tabular-nums text-foreground">{credits.toLocaleString()} cr</span>
    </div>
  );
}
