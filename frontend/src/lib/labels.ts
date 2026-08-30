import { useWorkspaceSettings } from "@/lib/api/queries";
import { useAuth } from "@/lib/auth";
import type { components } from "@/lib/api/schema";

export type Labels = components["schemas"]["LabelPack"];

/** Used before /auth/me resolves; the backend sends the real pack once it does. */
const FALLBACK: Labels = {
  contact: "contact",
  contact_plural: "contacts",
  campaign: "campaign",
  campaign_plural: "campaigns",
  workspace: "Workspace",
  goal: "goal",
};

/** The vertical's nouns for the active workspace (candidate/role, lead/sequence, ...).
 *
 * Workspace settings carry the pack resolved for that workspace's kind, so they win once loaded;
 * /auth/me's pack covers the pages that render before (or without) a workspace.
 */
export function useLabels(): Labels {
  const { me } = useAuth();
  const { data } = useWorkspaceSettings();
  return data?.labels ?? me?.labels ?? FALLBACK;
}

/** Sentence-case a label for a heading ("candidates" → "Candidates"). */
export function title(label: string): string {
  return label.charAt(0).toUpperCase() + label.slice(1);
}
