import * as React from "react";

import { cn } from "@/lib/utils";

/**
 * The single source of truth for page width + vertical rhythm. Every page wraps its content in
 * one of these so the content's left/right gutters line up as you navigate.
 *
 *  - `narrow`  — reading / forms / logs; more side padding (Settings, Audit)
 *  - `default` — most pages (Dashboard, Contacts, Campaigns, Analytics, Find people, …)
 *  - `wide`    — wide tools that need extra room (reserved; opt-in)
 *  - `full`    — full-bleed app surfaces that own their own height (Inbox, Pipeline, Approvals)
 */
const WIDTHS = {
  narrow: "max-w-4xl",
  default: "max-w-5xl",
  wide: "max-w-6xl",
  full: "max-w-none",
} as const;

export type PageWidth = keyof typeof WIDTHS;

export function PageLayout({
  width = "default",
  fill = false,
  className,
  children,
}: {
  width?: PageWidth;
  /** Full-height app surfaces (Inbox, Approvals, Pipeline) that own their own internal scroll. */
  fill?: boolean;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <div
      className={cn(
        "mx-auto w-full",
        fill ? "flex h-full flex-col gap-5" : "space-y-6",
        WIDTHS[width],
        className,
      )}
    >
      {children}
    </div>
  );
}
