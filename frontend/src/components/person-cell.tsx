import { Avatar, AvatarFallback, AvatarImage } from "@/components/ui/avatar";
import { cn } from "@/lib/utils";

interface PersonCellProps {
  name: string;
  subtitle?: string;
  imageSrc?: string;
  initials?: string;
  className?: string;
}

function initialsOf(name: string) {
  return name
    .split(" ")
    .map((p) => p[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

/** Avatar + name + subtitle — the canonical "who" cell for tables and lists. */
function PersonCell({ name, subtitle, imageSrc, initials, className }: PersonCellProps) {
  return (
    <div className={cn("flex items-center gap-3", className)}>
      <Avatar className="size-8">
        {imageSrc && <AvatarImage src={imageSrc} alt={name} />}
        <AvatarFallback>{initials ?? initialsOf(name)}</AvatarFallback>
      </Avatar>
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold text-foreground">{name}</div>
        {subtitle && <div className="truncate text-xs text-muted-foreground">{subtitle}</div>}
      </div>
    </div>
  );
}

export { PersonCell };
export type { PersonCellProps };
