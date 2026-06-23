import { Check, ChevronsUpDown, type LucideIcon } from "lucide-react";
import { NavLink } from "react-router-dom";

import { BrandMark } from "@/components/brand-mark";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import type { WorkspaceLite } from "@/lib/workspace";
import { cn } from "@/lib/utils";

interface NavItemDef {
  label: string;
  icon: LucideIcon;
  href?: string;
  active?: boolean;
  count?: number;
}

interface AppSidebarProps {
  workspace: string;
  org?: string;
  items: NavItemDef[];
  user: { name: string; role: string; initials: string };
  className?: string;
  workspaces?: WorkspaceLite[];
  currentWorkspaceId?: string | null;
  onSelectWorkspace?: (id: string) => void;
}

const ITEM_BASE =
  "flex cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-sm font-medium transition-colors";
const ITEM_ACTIVE = "bg-sidebar-active text-sidebar-active-foreground";
const ITEM_IDLE = "text-sidebar-foreground hover:bg-sidebar-hover";

function ItemBody({ icon: Icon, label, count }: NavItemDef) {
  return (
    <>
      <Icon className="size-[18px] opacity-90" />
      <span>{label}</span>
      {count != null && (
        <span className="ml-auto rounded-full bg-sidebar-active px-2 py-px text-[0.7rem] font-semibold text-sidebar-active-foreground">
          {count}
        </span>
      )}
    </>
  );
}

function SidebarNavItem(item: NavItemDef) {
  if (item.href) {
    return (
      <NavLink
        to={item.href}
        end={item.href === "/"}
        className={({ isActive }) => cn(ITEM_BASE, isActive ? ITEM_ACTIVE : ITEM_IDLE)}
      >
        <ItemBody {...item} />
      </NavLink>
    );
  }
  return (
    <a className={cn(ITEM_BASE, item.active ? ITEM_ACTIVE : ITEM_IDLE)}>
      <ItemBody {...item} />
    </a>
  );
}

const WS_BTN =
  "flex w-full items-center justify-between gap-2 rounded-md border border-sidebar-border bg-sidebar-hover px-3 py-2 text-xs font-medium text-sidebar-foreground transition-colors hover:text-sidebar-active-foreground";

function AppSidebar({
  workspace,
  org = "Acme",
  items,
  user,
  className,
  workspaces,
  currentWorkspaceId,
  onSelectWorkspace,
}: AppSidebarProps) {
  const switchable = onSelectWorkspace && workspaces && workspaces.length > 0;

  return (
    <aside className={cn("flex h-full w-60 shrink-0 flex-col gap-5 bg-sidebar p-4", className)}>
      <div className="flex items-center gap-2 px-2 font-display text-lg font-bold text-sidebar-active-foreground">
        <span className="grid size-7 place-items-center rounded-md bg-gradient-to-br from-score-from to-score-to text-primary-foreground">
          <BrandMark className="size-[18px]" />
        </span>
        Sourcewell
      </div>

      {switchable ? (
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button className={WS_BTN}>
              <span className="truncate">
                {org} · <span className="opacity-70">{workspace}</span>
              </span>
              <ChevronsUpDown className="size-3.5 shrink-0" />
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start" className="w-56">
            <DropdownMenuLabel>{org}</DropdownMenuLabel>
            {workspaces!.map((w) => (
              <DropdownMenuItem key={w.id} onClick={() => onSelectWorkspace!(w.id)}>
                <span className="flex-1 truncate">{w.name}</span>
                {w.id === currentWorkspaceId && <Check className="size-4 text-primary" />}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      ) : (
        <button className={WS_BTN}>
          <span className="truncate">
            {org} · <span className="opacity-70">{workspace}</span>
          </span>
          <ChevronsUpDown className="size-3.5 shrink-0" />
        </button>
      )}

      <nav className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
        {items.map((item) => (
          <SidebarNavItem key={item.label} {...item} />
        ))}
      </nav>

      <div className="flex items-center gap-2.5 border-t border-sidebar-border pt-4">
        <div className="grid size-8 place-items-center rounded-md bg-sidebar-active text-xs font-semibold text-sidebar-active-foreground">
          {user.initials}
        </div>
        <div className="min-w-0 leading-tight">
          <div className="truncate text-xs font-semibold text-sidebar-active-foreground">{user.name}</div>
          <div className="truncate text-[0.7rem] text-sidebar-foreground/80">{user.role}</div>
        </div>
      </div>
    </aside>
  );
}

export { AppSidebar };
export type { NavItemDef, AppSidebarProps };
