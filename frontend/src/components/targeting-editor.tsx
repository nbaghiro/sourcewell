import { ChevronDown } from "lucide-react";
import * as React from "react";

import { ChipInput } from "@/components/chip-input";
import { TARGETING_FIELDS, type ChipField, type Targeting } from "@/lib/targeting";
import { cn } from "@/lib/utils";

/**
 * The one audience/search editor — used by the campaign composer's audience card AND Find People.
 * Scored fields (titles/skills/locations/companies/industries/company size) are always shown; the
 * search-only refinements and the exclude list collapse so the common case stays simple.
 */
const PRIMARY: ChipField[] = [
  "titles",
  "skills",
  "locations",
  "companies",
  "industries",
  "company_sizes",
];
const REFINE: ChipField[] = ["seniorities", "functions", "technologies"];
const EXCLUDE: ChipField[] = ["exclude_companies", "exclude_titles"];

const META = Object.fromEntries(TARGETING_FIELDS.map((f) => [f.key, f])) as Record<
  ChipField,
  (typeof TARGETING_FIELDS)[number]
>;

function Field({
  k,
  value,
  onChange,
}: {
  k: ChipField;
  value: Targeting;
  onChange: (t: Targeting) => void;
}) {
  const f = META[k];
  return (
    <ChipInput
      label={f.label}
      values={value[k]}
      onChange={(v) => onChange({ ...value, [k]: v })}
      placeholder={f.placeholder}
    />
  );
}

function Section({
  title,
  fields,
  value,
  onChange,
}: {
  title: string;
  fields: ChipField[];
  value: Targeting;
  onChange: (t: Targeting) => void;
}) {
  const count = fields.reduce((n, k) => n + value[k].length, 0);
  const [open, setOpen] = React.useState(count > 0);
  return (
    <div className="border-t border-border pt-3">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="flex w-full items-center justify-between text-sm font-medium text-foreground"
      >
        <span>
          {title}
          {count > 0 && <span className="ml-1.5 text-xs font-normal text-muted-foreground">· {count}</span>}
        </span>
        <ChevronDown className={cn("size-4 text-muted-foreground transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="mt-3 space-y-3">
          {fields.map((k) => (
            <Field key={k} k={k} value={value} onChange={onChange} />
          ))}
        </div>
      )}
    </div>
  );
}

export function TargetingEditor({
  value,
  onChange,
}: {
  value: Targeting;
  onChange: (t: Targeting) => void;
}) {
  return (
    <div className="space-y-3">
      {PRIMARY.map((k) => (
        <Field key={k} k={k} value={value} onChange={onChange} />
      ))}
      <Section title="Refine search" fields={REFINE} value={value} onChange={onChange} />
      <Section title="Exclude" fields={EXCLUDE} value={value} onChange={onChange} />
    </div>
  );
}
