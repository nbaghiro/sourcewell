import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { useWorkspaceId } from "@/lib/workspace";
import { client, unwrap } from "./client";
import type { components } from "./schema";

type S = components["schemas"];
export type Contact = S["ContactOut"];
export type ContactDetail = S["ContactDetailOut"];
export type ContactIn = S["ContactIn"];
export type Campaign = S["CampaignOut"];
export type EnrollmentRow = S["EnrollmentRowOut"];
export type Approval = S["ApprovalOut"];
export type InboxItem = S["InboxItemOut"];
export type Conversation = S["ConversationOut"];
export type Message = S["MessageOut"];
export type DashboardSummary = S["DashboardSummary"];
export type Analytics = S["AnalyticsOut"];
export type Notifications = S["NotificationsOut"];
export type Member = S["MemberOut"];
export type Connection = S["ConnectionOut"];
export type WorkspaceSettings = S["WorkspaceSettingsOut"];
export type AuditEvent = S["AuditEventOut"];
export type SearchResults = S["SearchOut"];
export type DataProvider = S["DataProviderOut"];
export type PersonHit = S["PersonHit"];

// Keys are namespaced by workspace so switching workspaces refetches; mutations invalidate by the
// resource-name prefix (only mounted queries actually refetch).
const k = {
  contacts: (ws: string | null, q?: string) => ["contacts", ws, q ?? ""] as const,
  contact: (ws: string | null, id: string) => ["contact", ws, id] as const,
  campaigns: (ws: string | null) => ["campaigns", ws] as const,
  campaign: (ws: string | null, id: string) => ["campaign", ws, id] as const,
  enrollments: (ws: string | null, id: string, state?: string) =>
    ["enrollments", ws, id, state ?? ""] as const,
  estimate: (ws: string | null, id: string) => ["estimate", ws, id] as const,
  approvals: (ws: string | null) => ["approvals", ws] as const,
  inbox: (ws: string | null) => ["inbox", ws] as const,
  conversation: (ws: string | null, id: string) => ["conversation", ws, id] as const,
  dashboard: (ws: string | null) => ["dashboard", ws] as const,
  analytics: (ws: string | null) => ["analytics", ws] as const,
  notifications: (ws: string | null) => ["notifications", ws] as const,
  members: (ws: string | null) => ["members", ws] as const,
  connections: (ws: string | null) => ["connections", ws] as const,
  workspaceSettings: (ws: string | null) => ["workspaceSettings", ws] as const,
  audit: (ws: string | null) => ["audit", ws] as const,
  search: (ws: string | null, q: string) => ["search", ws, q] as const,
};

// ---------- contacts ----------

export function useContacts(q?: string) {
  const ws = useWorkspaceId();
  return useQuery({
    queryKey: k.contacts(ws, q),
    enabled: !!ws,
    queryFn: async () => unwrap(await client.GET("/contacts", { params: { query: q ? { q } : {} } })),
  });
}

export function useContact(id: string) {
  const ws = useWorkspaceId();
  return useQuery({
    queryKey: k.contact(ws, id),
    enabled: !!ws && !!id,
    queryFn: async () =>
      unwrap(await client.GET("/contacts/{contact_id}", { params: { path: { contact_id: id } } })),
  });
}

export function useGenerateSample() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (count: number) =>
      unwrap(await client.POST("/contacts/sample", { body: { count } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["contacts"] }),
  });
}

export function useImportContacts() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (contacts: ContactIn[]) =>
      unwrap(await client.POST("/contacts/import", { body: { contacts } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["contacts"] }),
  });
}

export function useUpdateContact(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (patch: S["ContactPatch"]) =>
      unwrap(await client.PATCH("/contacts/{contact_id}", { params: { path: { contact_id: id } }, body: patch })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["contact"] });
      qc.invalidateQueries({ queryKey: ["contacts"] });
    },
  });
}

export function useDeleteContact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap(await client.DELETE("/contacts/{contact_id}", { params: { path: { contact_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["contacts"] }),
  });
}

export function useEnrollContact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { campaignId: string; contactId: string }) =>
      unwrap(
        await client.POST("/campaigns/{campaign_id}/enroll", {
          params: { path: { campaign_id: vars.campaignId } },
          body: { contact_id: vars.contactId },
        }),
      ),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["contact"] });
      qc.invalidateQueries({ queryKey: ["enrollments"] });
    },
  });
}

// ---------- campaigns ----------

export function useCampaigns() {
  const ws = useWorkspaceId();
  return useQuery({
    queryKey: k.campaigns(ws),
    enabled: !!ws,
    queryFn: async () => unwrap(await client.GET("/campaigns")),
  });
}

export function useCampaign(id: string) {
  const ws = useWorkspaceId();
  return useQuery({
    queryKey: k.campaign(ws, id),
    enabled: !!ws && !!id,
    queryFn: async () =>
      unwrap(await client.GET("/campaigns/{campaign_id}", { params: { path: { campaign_id: id } } })),
  });
}

export function useCreateCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: S["CampaignIn"]) => unwrap(await client.POST("/campaigns", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });
}

function invalidateCampaign(qc: ReturnType<typeof useQueryClient>) {
  qc.invalidateQueries({ queryKey: ["campaign"] });
  qc.invalidateQueries({ queryKey: ["campaigns"] });
}

export function useUpdateCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (v: { id: string; patch: S["CampaignPatch"] }) =>
      unwrap(await client.PATCH("/campaigns/{campaign_id}", { params: { path: { campaign_id: v.id } }, body: v.patch })),
    onSuccess: () => invalidateCampaign(qc),
  });
}

export function useCampaignLifecycle() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (v: { id: string; action: "pause" | "resume" | "archive" }) => {
      const p = { params: { path: { campaign_id: v.id } } };
      const r =
        v.action === "pause"
          ? await client.POST("/campaigns/{campaign_id}/pause", p)
          : v.action === "resume"
            ? await client.POST("/campaigns/{campaign_id}/resume", p)
            : await client.POST("/campaigns/{campaign_id}/archive", p);
      return unwrap(r);
    },
    onSuccess: () => invalidateCampaign(qc),
  });
}

export function useDuplicateCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap(await client.POST("/campaigns/{campaign_id}/duplicate", { params: { path: { campaign_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["campaigns"] }),
  });
}

export function useDeleteCampaign() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap(await client.DELETE("/campaigns/{campaign_id}", { params: { path: { campaign_id: id } } })),
    onSuccess: () => invalidateCampaign(qc),
  });
}

export function useCampaignEnrollments(id: string) {
  const ws = useWorkspaceId();
  return useQuery({
    queryKey: k.enrollments(ws, id),
    enabled: !!ws && !!id,
    refetchInterval: 20_000,
    queryFn: async () =>
      unwrap(
        await client.GET("/campaigns/{campaign_id}/enrollments", {
          params: { path: { campaign_id: id } },
        }),
      ),
  });
}

export function useRankCampaign(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () =>
      unwrap(await client.POST("/campaigns/{campaign_id}/rank", { params: { path: { campaign_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["enrollments"] }),
  });
}

// ---------- enrollment actions ----------

export function useApproveEnrollment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap(await client.POST("/enrollments/{enrollment_id}/approve", { params: { path: { enrollment_id: id } } })),
    onSuccess: () => invalidatePipeline(qc),
  });
}

export function useBulkApprove() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (ids: string[]) =>
      unwrap(await client.POST("/enrollments/bulk-approve", { body: { ids } })),
    onSuccess: () => invalidatePipeline(qc),
  });
}

export function useHandoff() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap(await client.POST("/enrollments/{enrollment_id}/handoff", { params: { path: { enrollment_id: id } } })),
    onSuccess: () => invalidateInbox(qc),
  });
}

export function useOptOut() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap(await client.POST("/enrollments/{enrollment_id}/opt-out", { params: { path: { enrollment_id: id } } })),
    onSuccess: () => invalidateInbox(qc),
  });
}

// ---------- approvals / messages ----------

export function useApprovals() {
  const ws = useWorkspaceId();
  return useQuery({
    queryKey: k.approvals(ws),
    enabled: !!ws,
    refetchInterval: 20_000,
    queryFn: async () => unwrap(await client.GET("/approvals")),
  });
}

export function useApproveMessage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (messageId: string) =>
      unwrap(await client.POST("/messages/{message_id}/approve", { params: { path: { message_id: messageId } } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["approvals"] });
      invalidateInbox(qc);
    },
  });
}

export function useEditMessage() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { messageId: string; subject?: string | null; body?: string | null }) =>
      unwrap(
        await client.PATCH("/messages/{message_id}", {
          params: { path: { message_id: vars.messageId } },
          body: { subject: vars.subject ?? null, body: vars.body ?? null },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["approvals"] }),
  });
}

// ---------- inbox ----------

export function useInbox() {
  const ws = useWorkspaceId();
  return useQuery({
    queryKey: k.inbox(ws),
    enabled: !!ws,
    refetchInterval: 25_000,
    queryFn: async () => unwrap(await client.GET("/inbox")),
  });
}

export function useConversation(id: string | null) {
  const ws = useWorkspaceId();
  return useQuery({
    queryKey: k.conversation(ws, id ?? ""),
    enabled: !!ws && !!id,
    queryFn: async () =>
      unwrap(await client.GET("/inbox/{enrollment_id}", { params: { path: { enrollment_id: id! } } })),
  });
}

export function useSendReply() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: { id: string; text: string }) =>
      unwrap(
        await client.POST("/inbox/{enrollment_id}/reply", {
          params: { path: { enrollment_id: vars.id } },
          body: { text: vars.text },
        }),
      ),
    onSuccess: () => invalidateInbox(qc),
  });
}

export function useDraftReply() {
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap(await client.POST("/inbox/{enrollment_id}/draft", { params: { path: { enrollment_id: id } } })),
  });
}

export function useMarkRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap(await client.POST("/inbox/{enrollment_id}/read", { params: { path: { enrollment_id: id } } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["inbox"] });
      qc.invalidateQueries({ queryKey: ["notifications"] });
    },
  });
}

// ---------- dashboard / analytics / notifications / audit ----------

export function useDashboard() {
  const ws = useWorkspaceId();
  return useQuery({
    queryKey: k.dashboard(ws),
    enabled: !!ws,
    queryFn: async () => unwrap(await client.GET("/dashboard/summary")),
  });
}

export function useAnalytics() {
  const ws = useWorkspaceId();
  return useQuery({
    queryKey: k.analytics(ws),
    enabled: !!ws,
    queryFn: async () => unwrap(await client.GET("/analytics")),
  });
}

export function useNotifications() {
  const ws = useWorkspaceId();
  return useQuery({
    queryKey: k.notifications(ws),
    enabled: !!ws,
    refetchInterval: 30_000,
    queryFn: async () => unwrap(await client.GET("/notifications")),
  });
}

export function useMarkNotificationsRead() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => unwrap(await client.POST("/notifications/read")),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }),
  });
}

export function useAudit() {
  const ws = useWorkspaceId();
  return useQuery({
    queryKey: k.audit(ws),
    enabled: !!ws,
    queryFn: async () => unwrap(await client.GET("/audit")),
  });
}

// ---------- settings ----------

export function useMembers() {
  const ws = useWorkspaceId();
  return useQuery({
    queryKey: k.members(ws),
    enabled: !!ws,
    queryFn: async () => unwrap(await client.GET("/settings/members")),
  });
}

export function useConnections() {
  const ws = useWorkspaceId();
  return useQuery({
    queryKey: k.connections(ws),
    enabled: !!ws,
    queryFn: async () => unwrap(await client.GET("/settings/connections")),
  });
}

export function usePeopleProviders() {
  return useQuery({
    queryKey: ["peopleProviders"],
    queryFn: async () => unwrap(await client.GET("/people/providers")),
  });
}

export function useSearchPeople() {
  return useMutation({
    mutationFn: async (query: S["PeopleSearchIn"]) =>
      unwrap(await client.POST("/people/search", { body: query })),
  });
}

export function useParsePeopleQuery() {
  return useMutation({
    mutationFn: async (text: string) =>
      unwrap(await client.POST("/people/parse", { body: { text } })),
  });
}

export function useImportPeople() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (hits: PersonHit[]) =>
      unwrap(await client.POST("/people/import", { body: { hits } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["contacts"] }),
  });
}

// ---- agent experience (shared read-model behind every UI variant) ----

export function useAgentActivity() {
  return useQuery({
    queryKey: ["agentActivity"],
    queryFn: async () =>
      unwrap(await client.GET("/agent/activity", { params: { query: { limit: 40 } } })),
    refetchInterval: 15000,
  });
}

export function useAgentState() {
  return useQuery({
    queryKey: ["agentState"],
    queryFn: async () => unwrap(await client.GET("/agent/state")),
    refetchInterval: 15000,
  });
}

export function useAgentChat() {
  return useMutation({
    mutationFn: async (message: string) =>
      unwrap(await client.POST("/agent/chat", { body: { message } })),
  });
}

// ---- campaign cockpit (per-campaign agent surface) ----

export function useCampaignFunnel(id: string) {
  const ws = useWorkspaceId();
  return useQuery({
    queryKey: ["campaignFunnel", ws, id],
    enabled: !!ws && !!id,
    refetchInterval: 20_000,
    queryFn: async () =>
      unwrap(await client.GET("/agent/funnel", { params: { query: { campaign_id: id } } })),
  });
}

export function useCampaignRuns(id: string) {
  const ws = useWorkspaceId();
  return useQuery({
    queryKey: ["campaignRuns", ws, id],
    enabled: !!ws && !!id,
    refetchInterval: 15_000,
    queryFn: async () =>
      unwrap(
        await client.GET("/agent/runs", { params: { query: { campaign_id: id, limit: 40 } } }),
      ),
  });
}

export function useCampaignChat(id: string) {
  return useMutation({
    mutationFn: async (message: string) =>
      unwrap(await client.POST("/agent/chat", { body: { message, campaign_id: id } })),
  });
}

export function useApplyAudience() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (v: { campaign_id: string; criteria: S["JsonObject"] }) =>
      unwrap(await client.POST("/agent/apply-audience", { body: v })),
    onSuccess: () => invalidateCampaign(qc),
  });
}

export function useDataProviders() {
  return useQuery({
    queryKey: ["dataProviders"],
    queryFn: async () => unwrap(await client.GET("/settings/data-providers")),
  });
}

export function useSaveDataProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (v: { provider: string; body: S["DataProviderIn"] }) =>
      unwrap(
        await client.PUT("/settings/data-providers/{provider}", {
          params: { path: { provider: v.provider } },
          body: v.body,
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["dataProviders"] }),
  });
}

export function useDeleteDataProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (provider: string) =>
      unwrap(
        await client.DELETE("/settings/data-providers/{provider}", {
          params: { path: { provider } },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["dataProviders"] }),
  });
}

export function useVerifyDataProvider() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (provider: string) =>
      unwrap(
        await client.POST("/settings/data-providers/{provider}/verify", {
          params: { path: { provider } },
        }),
      ),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["dataProviders"] }),
  });
}

export type Suppression = S["SuppressionOut"];

export function useSuppressions() {
  return useQuery({
    queryKey: ["suppressions"],
    queryFn: async () => unwrap(await client.GET("/suppressions")),
  });
}

export function useAddSuppression() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (body: S["SuppressionIn"]) =>
      unwrap(await client.POST("/suppressions", { body })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["suppressions"] }),
  });
}

export function useRemoveSuppression() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (email: string) =>
      unwrap(await client.DELETE("/suppressions/{email}", { params: { path: { email } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["suppressions"] }),
  });
}

export function useForgetContact() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap(await client.POST("/contacts/{contact_id}/forget", { params: { path: { contact_id: id } } })),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["contacts"] });
      qc.invalidateQueries({ queryKey: ["suppressions"] });
    },
  });
}

export function useWorkspaceSettings() {
  const ws = useWorkspaceId();
  return useQuery({
    queryKey: k.workspaceSettings(ws),
    enabled: !!ws,
    queryFn: async () => unwrap(await client.GET("/settings/workspace")),
  });
}

export function useUpdateWorkspaceSettings() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (patch: S["WorkspacePatch"]) =>
      unwrap(await client.PATCH("/settings/workspace", { body: patch })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["workspaceSettings"] }),
  });
}

export function useConnect() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (provider: "gmail" | "graph" | "linkedin") =>
      unwrap(await client.POST("/settings/connections/{provider}/connect", { params: { path: { provider } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["connections"] }),
  });
}

export function useDisconnect() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap(await client.POST("/settings/connections/{connection_id}/disconnect", { params: { path: { connection_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["connections"] }),
  });
}

export function useReauth() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap(await client.POST("/settings/connections/{connection_id}/reauth", { params: { path: { connection_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["connections"] }),
  });
}

export function useInviteMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (vars: S["InviteRequest"]) =>
      unwrap(await client.POST("/settings/members/invite", { body: vars })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["members"] }),
  });
}

export function useRemoveMember() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (id: string) =>
      unwrap(await client.DELETE("/settings/members/{user_id}", { params: { path: { user_id: id } } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["members"] }),
  });
}

export function useUpdateMemberRole() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (v: { id: string; role: S["RolePatch"]["role"] }) =>
      unwrap(await client.PATCH("/settings/members/{user_id}", { params: { path: { user_id: v.id } }, body: { role: v.role } })),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["members"] }),
  });
}

// ---------- search ----------

export function useSearch(q: string) {
  const ws = useWorkspaceId();
  return useQuery({
    queryKey: k.search(ws, q),
    enabled: !!ws && q.trim().length > 0,
    queryFn: async () => unwrap(await client.GET("/search", { params: { query: { q } } })),
  });
}

// ---------- invalidation helpers ----------

function invalidateInbox(qc: ReturnType<typeof useQueryClient>) {
  for (const key of ["inbox", "conversation", "notifications", "dashboard", "analytics", "audit"]) {
    qc.invalidateQueries({ queryKey: [key] });
  }
}
function invalidatePipeline(qc: ReturnType<typeof useQueryClient>) {
  for (const key of ["enrollments", "dashboard", "analytics", "approvals", "audit"]) {
    qc.invalidateQueries({ queryKey: [key] });
  }
}
