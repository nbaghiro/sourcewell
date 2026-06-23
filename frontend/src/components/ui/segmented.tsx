import { cn } from "@/lib/utils";

interface SegmentedProps {
  value: string;
  onChange: (v: string) => void;
  options: { value: string; label: string }[];
  className?: string;
}

/** A small segmented control (pill toggle). */
function Segmented({ value, onChange, options, className }: SegmentedProps) {
  return (
    <div className={cn("inline-flex rounded-lg border border-border bg-secondary/40 p-1", className)}>
      {options.map((o) => (
        <button
          key={o.value}
          onClick={() => onChange(o.value)}
          className={cn(
            "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
            value === o.value
              ? "bg-card text-foreground shadow-sm"
              : "text-muted-foreground hover:text-foreground",
          )}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

export { Segmented };
