import { X } from "lucide-react";
import * as React from "react";

import { Badge } from "@/components/ui/badge";
import { Label } from "@/components/ui/label";

/** A tag/chip text input — type and press Enter to add, click × to remove. */
export function ChipInput({
  label,
  values,
  onChange,
  placeholder = "Add…",
}: {
  label?: string;
  values: string[];
  onChange: (v: string[]) => void;
  placeholder?: string;
}) {
  const [v, setV] = React.useState("");
  return (
    <div>
      {label && <Label>{label}</Label>}
      <div className="mt-1.5 flex flex-wrap gap-1.5 rounded-md border border-input bg-card p-2">
        {values.map((x) => (
          <Badge key={x} variant="secondary" className="gap-1">
            {x}
            <button onClick={() => onChange(values.filter((y) => y !== x))}>
              <X className="size-3" />
            </button>
          </Badge>
        ))}
        <input
          className="min-w-[80px] flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
          placeholder={placeholder}
          value={v}
          onChange={(e) => setV(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && v.trim()) {
              e.preventDefault();
              onChange([...values, v.trim()]);
              setV("");
            }
          }}
        />
      </div>
    </div>
  );
}
