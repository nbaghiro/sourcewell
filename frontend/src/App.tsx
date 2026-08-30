import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import * as React from "react";
import { BrowserRouter, Navigate, Route, Routes, useSearchParams } from "react-router-dom";

import { AppLayout } from "@/components/app-layout";
import { BrandMark } from "@/components/brand-mark";
import { Toaster } from "@/components/ui/sonner";
import { toast } from "sonner";
import { TooltipProvider } from "@/components/ui/tooltip";
import { WorkspaceProvider } from "@/components/workspace-provider";
import { AuthProvider, useAuth } from "@/lib/auth";
import { CampaignBuilderPage } from "@/pages/campaign-builder-page";
import { CampaignDetailPage } from "@/pages/campaign-detail-page";
import { CampaignsPage } from "@/pages/campaigns-page";
import { ContactDetailPage } from "@/pages/contact-detail-page";
import { PeoplePage } from "@/pages/people-page";
import { DashboardPage } from "@/pages/dashboard-page";
import { FindPeoplePage } from "@/pages/find-people-page";
import { InboxPage } from "@/pages/inbox-page";
import { ForgotPasswordPage } from "@/pages/forgot-password-page";
import { LoginPage } from "@/pages/login-page";
import { ResetPasswordPage } from "@/pages/reset-password-page";
import { SignupPage } from "@/pages/signup-page";
import { VerifyEmailPage } from "@/pages/verify-email-page";
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
  const { status, me } = useAuth();
  if (status === "loading") return <Splash label="Loading your workspace…" />;
  if (status === "anon") return <Navigate to="/login" replace />;
  // Signed in via Google/Microsoft but the signup profile is still outstanding: the account
  // exists and is verified, it just has no username or company yet. Finish that first.
  if (me && !me.profile_complete) return <Navigate to="/signup" replace />;
  return <>{children}</>;
}

function LoginRoute() {
  const { status } = useAuth();
  if (status === "loading") return <Splash label="Signing you in…" />;
  if (status === "authed") return <Navigate to="/" replace />;
  return <LoginPage />;
}

function ForgotPasswordRoute() {
  const { status } = useAuth();
  if (status === "loading") return <Splash label="One moment…" />;
  if (status === "authed") return <Navigate to="/" replace />;
  return <ForgotPasswordPage />;
}

function ResetPasswordRoute() {
  const { status } = useAuth();
  // A signed-in user following their own reset link has already been signed in by it.
  if (status === "loading") return <Splash label="One moment…" />;
  if (status === "authed") return <Navigate to="/" replace />;
  return <ResetPasswordPage />;
}

function VerifyEmailRoute() {
  const { status } = useAuth();
  if (status === "loading") return <Splash label="Checking your link…" />;
  if (status === "authed") return <Navigate to="/" replace />;
  return <VerifyEmailPage />;
}

/** Where the emailed links land once they've minted a session: `/?verified=1` for a signup
 *  confirmation, `/?invited=1` for an accepted invitation. */
const ARRIVAL_TOASTS: Record<string, string> = {
  verified: "Email confirmed — welcome to Sourcewell.",
  invited: "Invitation accepted — welcome to the team.",
};

function ArrivalToast() {
  const [params, setParams] = useSearchParams();
  React.useEffect(() => {
    const key = Object.keys(ARRIVAL_TOASTS).find((k) => params.get(k) === "1");
    if (!key) return;
    toast.success(ARRIVAL_TOASTS[key]);
    params.delete(key);
    setParams(params, { replace: true });
  }, [params, setParams]);
  return null;
}

function SignupRoute() {
  const { status, me } = useAuth();
  if (status === "loading") return <Splash label="Setting up your workspace…" />;
  // The one authed state that belongs here: an OAuth signup that hasn't been finished. The page
  // renders its completion mode — email fixed to the provider's, no password to choose.
  if (status === "authed" && me?.profile_complete !== false) return <Navigate to="/" replace />;
  return <SignupPage />;
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
            <Route path="/signup" element={<SignupRoute />} />
            <Route path="/verify-email" element={<VerifyEmailRoute />} />
            <Route path="/forgot-password" element={<ForgotPasswordRoute />} />
            <Route path="/reset-password" element={<ResetPasswordRoute />} />
            <Route
              element={
                <RequireAuth>
                  <ArrivalToast />
                  <AppLayout />
                </RequireAuth>
              }
            >
              <Route path="/" element={<DashboardPage />} />
              <Route path="/people" element={<PeoplePage />} />
              <Route path="/people/find" element={<FindPeoplePage />} />
              <Route path="/people/:id" element={<ContactDetailPage />} />
              <Route path="/campaigns" element={<CampaignsPage />} />
              <Route path="/campaigns/new" element={<CampaignBuilderPage />} />
              <Route path="/campaigns/:id" element={<CampaignDetailPage />} />
              <Route path="/inbox" element={<InboxPage />} />
              <Route path="/pipeline" element={<PipelinePage />} />
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
