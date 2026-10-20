import { lazy, Suspense, useEffect } from "react";
import { BrowserRouter, Routes, Route, Outlet, useNavigate } from "react-router-dom";
import { DashboardShell } from "./layouts/DashboardShell";
import { RequireAdmin } from "./components/RequireAdmin";
import { setNavigate } from "./lib/navigation";
import { PageFallback } from "./components/PageFallback";
import { BASE_PATH } from "@/lib/basePath";

// Every page is its own chunk, so a student opening a chat link downloads the chat page only, not
// the whole teacher dashboard (analytics, latency lab, evaluation, ...).
const LandingPage = lazy(() => import("./pages/Landing").then((m) => ({ default: m.LandingPage })));
const LoginPage = lazy(() => import("./pages/Login").then((m) => ({ default: m.LoginPage })));
const RegisterPage = lazy(() => import("./pages/Register").then((m) => ({ default: m.RegisterPage })));
const ForgotPasswordPage = lazy(() =>
  import("./pages/ForgotPassword").then((m) => ({ default: m.ForgotPasswordPage })),
);
const ResetPasswordPage = lazy(() => import("./pages/ResetPassword").then((m) => ({ default: m.ResetPasswordPage })));
const OverviewPage = lazy(() => import("./pages/Dashboard/Overview").then((m) => ({ default: m.OverviewPage })));
const ConfiguratorPage = lazy(() =>
  import("./pages/Dashboard/Configurator").then((m) => ({ default: m.ConfiguratorPage })),
);
const AnalyticsPage = lazy(() => import("./pages/Dashboard/Analytics").then((m) => ({ default: m.AnalyticsPage })));
const ApiDashboardPage = lazy(() =>
  import("./pages/Dashboard/ApiDashboard").then((m) => ({ default: m.ApiDashboardPage })),
);
const VoicesPage = lazy(() => import("./pages/Dashboard/Voices").then((m) => ({ default: m.VoicesPage })));
const PronunciationPage = lazy(() =>
  import("./pages/Dashboard/Pronunciation").then((m) => ({ default: m.PronunciationPage })),
);
const KnowledgePage = lazy(() => import("./pages/Dashboard/Knowledge").then((m) => ({ default: m.KnowledgePage })));
const EvaluationPage = lazy(() => import("./pages/Dashboard/Evaluation").then((m) => ({ default: m.EvaluationPage })));
const LatencyLabPage = lazy(() => import("./pages/Dashboard/LatencyLab").then((m) => ({ default: m.LatencyLabPage })));
const MotionPromptPage = lazy(() =>
  import("./pages/Dashboard/MotionPrompt").then((m) => ({ default: m.MotionPromptPage })),
);
const ProfilePage = lazy(() => import("./pages/Dashboard/Profile").then((m) => ({ default: m.ProfilePage })));
const AdminUsersPage = lazy(() => import("./pages/Dashboard/Admin/Users").then((m) => ({ default: m.AdminUsersPage })));
const AdminSettingsPage = lazy(() =>
  import("./pages/Dashboard/Admin/Settings").then((m) => ({ default: m.AdminSettingsPage })),
);
const AdminPagesPage = lazy(() => import("./pages/Dashboard/Admin/Pages").then((m) => ({ default: m.AdminPagesPage })));
const AdminPageEditorPage = lazy(() =>
  import("./pages/Dashboard/Admin/Pages/Editor").then((m) => ({ default: m.AdminPageEditorPage })),
);
const ForcePasswordChangePage = lazy(() =>
  import("./pages/Dashboard/ForcePasswordChange").then((m) => ({ default: m.ForcePasswordChangePage })),
);
const PublicChatPage = lazy(() => import("./pages/PublicChat").then((m) => ({ default: m.PublicChatPage })));
const ImprintPage = lazy(() => import("./pages/Imprint").then((m) => ({ default: m.ImprintPage })));
const PrivacyPage = lazy(() => import("./pages/Privacy").then((m) => ({ default: m.PrivacyPage })));
const CustomPage = lazy(() => import("./pages/CustomPage").then((m) => ({ default: m.CustomPage })));
const CreditsPage = lazy(() => import("./pages/Credits").then((m) => ({ default: m.CreditsPage })));

// Layout route: wraps every /dashboard/* screen in the shared sidebar shell (Screen 1c).
function DashboardLayout() {
  return (
    <DashboardShell>
      {/* Inside the shell, so the sidebar stays put while a dashboard page's chunk loads. */}
      <Suspense fallback={<PageFallback />}>
        <Outlet />
      </Suspense>
    </DashboardShell>
  );
}

/** Hands this router's navigate() to lib/navigation.ts so code outside the component tree (the
 * global QueryClient error handler in main.tsx) can redirect on a session-expired response.
 * Renders nothing — must live inside <BrowserRouter> for useNavigate() to work. */
function NavigateBridge() {
  const navigate = useNavigate();
  useEffect(() => {
    setNavigate(navigate);
  }, [navigate]);
  return null;
}

export function App() {
  return (
    <BrowserRouter basename={BASE_PATH || undefined}>
      <NavigateBridge />
      <Suspense fallback={<PageFallback />}>
        <Routes>
          <Route path="/" element={<LandingPage />} />
          <Route path="/login" element={<LoginPage />} />
          <Route path="/register" element={<RegisterPage />} />
          <Route path="/forgot-password" element={<ForgotPasswordPage />} />
          <Route path="/reset-password" element={<ResetPasswordPage />} />
          <Route path="/impressum" element={<ImprintPage />} />
          <Route path="/datenschutz" element={<PrivacyPage />} />
          <Route path="/credits" element={<CreditsPage />} />
          <Route path="/p/:slug" element={<CustomPage />} />
          <Route path="/dashboard" element={<DashboardLayout />}>
            <Route index element={<OverviewPage />} />
            <Route path="projects/:id" element={<ConfiguratorPage />} />
            <Route path="analytics" element={<AnalyticsPage />} />
            <Route path="api" element={<ApiDashboardPage />} />
            <Route path="voices" element={<VoicesPage />} />
            <Route path="pronunciation" element={<PronunciationPage />} />
            <Route path="knowledge" element={<KnowledgePage />} />
            <Route path="evaluation" element={<EvaluationPage />} />
            <Route path="latency" element={<LatencyLabPage />} />
            <Route path="motion-prompt" element={<MotionPromptPage />} />
            <Route path="profile" element={<ProfilePage />} />
            <Route path="change-password-required" element={<ForcePasswordChangePage />} />
            <Route path="admin" element={<RequireAdmin> <AdminUsersPage /> </RequireAdmin> } />
            <Route path="admin/settings" element={<RequireAdmin> <AdminSettingsPage /> </RequireAdmin> } />
            <Route path="admin/pages" element={<RequireAdmin> <AdminPagesPage /> </RequireAdmin> } />
            <Route path="admin/pages/:id" element={<RequireAdmin> <AdminPageEditorPage /> </RequireAdmin> } />
          </Route>
          <Route path="/c/:projectSlug" element={<PublicChatPage />} />
        </Routes>
      </Suspense>
    </BrowserRouter>
  );
}