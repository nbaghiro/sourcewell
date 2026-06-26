import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import * as React from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";

import { AppLayout } from "@/components/app-layout";
import { BrandMark } from "@/components/brand-mark";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { WorkspaceProvider } from "@/components/workspace-provider";
import { AuthProvider, useAuth } from "@/lib/auth";
import { AgentPage } from "@/pages/agent-page";
import { AnalyticsPage } from "@/pages/analytics-page";
import { ApprovalsPage } from "@/pages/approvals-page";
import { AuditPage } from "@/pages/audit-page";
import { CampaignBuilderPage } from "@/pages/campaign-builder-page";
import { CampaignCockpitPage } from "@/pages/campaign-cockpit-page";
import { CampaignDetailPage } from "@/pages/campaign-detail-page";
import { CampaignsPage } from "@/pages/campaigns-page";
import { ContactDetailPage } from "@/pages/contact-detail-page";
import { ContactsPage } from "@/pages/contacts-page";
import { DashboardPage } from "@/pages/dashboard-page";
import { FindPeoplePage } from "@/pages/find-people-page";
import { InboxPage } from "@/pages/inbox-page";
import { LoginPage } from "@/pages/login-page";
import { PipelinePage } from "@/pages/pipeline-page";
import { SettingsPage } from "@/pages/settings-page";

const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 30_000, retry: 1, refetchOnWindowFocus: false } },
});

function Splash({ label }: { label: string }) {
  return (
    <div className="grid min-h-screen place-items-center bg-background">
      <div className="flex flex-col items-center gap-5">
        <div className="grid size-12 animate-pulse place-items-center rounded-xl bg-gradient-to-br from-score-from to-score-to text-primary-foreground shadow-sm">
          <BrandMark className="size-7" />
        </div>
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 className="size-4 animate-spin" /> {label}
        </div>
      </div>
    </div>
  );
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { status } = useAuth();
  if (status === "loading") return <Splash label="Loading your workspace…" />;
  if (status === "anon") return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function LoginRoute() {
  const { status } = useAuth();
  if (status === "loading") return <Splash label="Signing you in…" />;
  if (status === "authed") return <Navigate to="/" replace />;
  return <LoginPage />;
}

export default function App() {
  return (
    <BrowserRouter>
      <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <WorkspaceProvider>
          <TooltipProvider>
          <Routes>
            <Route path="/login" element={<LoginRoute />} />
            <Route
              element={
                <RequireAuth>
                  <AppLayout />
                </RequireAuth>
              }
            >
              <Route path="/" element={<DashboardPage />} />
              <Route path="/agent" element={<AgentPage />} />
              <Route path="/contacts" element={<ContactsPage />} />
              <Route path="/contacts/:id" element={<ContactDetailPage />} />
              <Route path="/people" element={<FindPeoplePage />} />
              <Route path="/campaigns" element={<CampaignsPage />} />
              <Route path="/campaigns/new" element={<CampaignBuilderPage />} />
              <Route path="/campaigns/:id" element={<CampaignDetailPage />} />
              <Route path="/campaigns/:id/cockpit" element={<CampaignCockpitPage />} />
              <Route path="/approvals" element={<ApprovalsPage />} />
              <Route path="/inbox" element={<InboxPage />} />
              <Route path="/pipeline" element={<PipelinePage />} />
              <Route path="/analytics" element={<AnalyticsPage />} />
              <Route path="/audit" element={<AuditPage />} />
              <Route path="/settings" element={<SettingsPage />} />
            </Route>
            <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
            <Toaster position="top-right" />
          </TooltipProvider>
        </WorkspaceProvider>
      </AuthProvider>
      </QueryClientProvider>
    </BrowserRouter>
  );
}
