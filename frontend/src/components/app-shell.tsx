import * as React from "react";

import { cn } from "@/lib/utils";

interface AppShellProps {
  /** The left rail, typically <AppSidebar />. Stays pinned full-height. */
  sidebar: React.ReactNode;
  /** Optional top bar pinned above the scrolling content. */
  topbar?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

/** App frame: a viewport-height row with a pinned sidebar + pinned topbar; only `main` scrolls. */
function AppShell({ sidebar, topbar, children, className }: AppShellProps) {
  return (
    <div className={cn("flex h-screen overflow-hidden bg-background", className)}>
      {sidebar}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {topbar && (
          <header className="shrink-0 border-b border-border bg-card">{topbar}</header>
        )}
        <main className="min-h-0 flex-1 overflow-y-auto px-6 py-5">{children}</main>
      </div>
    </div>
  );
}

export { AppShell };
export type { AppShellProps };
