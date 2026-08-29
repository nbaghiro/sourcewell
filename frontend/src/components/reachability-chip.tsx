import { Mail, MailCheck, UserPlus } from "lucide-react";

import { type FitContact, reachability } from "@/lib/targeting";
import { cn } from "@/lib/utils";

const META = {
  verified: { label: "Verified", Icon: MailCheck, color: "var(--accent-strong)" },
  reachable: { label: "Reachable", Icon: Mail, color: undefined },
  needs_enrichment: { label: "Needs email", Icon: UserPlus, color: undefined },
} as const;

/** The candidate's reachability — a separate axis from the fit score (can we act on them?). */
export function ReachabilityChip({
  contact,
  className,
}: {
  contact: FitContact;
  className?: string;
}) {
  const { label, Icon, color } = META[reachability(contact)];
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 whitespace-nowrap text-[0.65rem] font-medium text-muted-foreground",
        className,
      )}
      style={color ? { color } : undefined}
      title={`Reachability: ${label}`}
    >
      <Icon className="size-3" /> {label}
    </span>
  );
}
