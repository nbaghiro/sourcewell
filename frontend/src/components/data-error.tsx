import { AlertCircle, RefreshCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** Consistent "couldn't load" state with a retry. */
export function DataError({ onRetry, className }: { onRetry: () => void; className?: string }) {
  return (
    <div
      className={cn(
        "flex flex-col items-center gap-3 rounded-xl border border-dashed border-border bg-card/60 px-6 py-12 text-center",
        className,
      )}
    >
      <AlertCircle className="size-7 text-muted-foreground" />
      <div className="text-sm text-muted-foreground">
        Couldn't load this. Check your connection and try again.
      </div>
      <Button variant="outline" size="sm" onClick={onRetry}>
        <RefreshCw className="size-4" /> Retry
      </Button>
    </div>
  );
}
