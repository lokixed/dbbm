import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useEffect } from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation, useNavigate } from "react-router-dom";
import { ThemeProvider } from "xedonium/theme";
import { setUnauthorizedHandler } from "@/api/client";
import { Layout } from "@/components/Layout";
import { ProtectedRoute } from "@/components/ProtectedRoute";
import { ToastProvider } from "@/components/toast";
import { pageTitle } from "@/lib/pageTitle";
import AuditPage from "@/pages/Audit";
import BackupsPage from "@/pages/Backups";
import DashboardPage from "@/pages/Dashboard";
import DestinationsPage from "@/pages/Destinations";
import DocsPage from "@/pages/Docs";
import JobsPage from "@/pages/Jobs";
import LoginPage from "@/pages/Login";
import NotificationsPage from "@/pages/Notifications";
import RunDetailPage from "@/pages/RunDetail";
import RunsPage from "@/pages/Runs";
import SettingsPage from "@/pages/Settings";
import SetupPage from "@/pages/Setup";
import SourcesPage from "@/pages/Sources";

const queryClient = new QueryClient({
  defaultOptions: { queries: { refetchOnWindowFocus: false, retry: 1 } },
});

// Screen readers announce the document title on navigation, so each page gets its own.
function RouteTitle() {
  const { pathname } = useLocation();
  useEffect(() => {
    document.title = `${pageTitle(pathname)} - DBBM`;
  }, [pathname]);
  return null;
}

function UnauthorizedRedirect() {
  const navigate = useNavigate();
  useEffect(() => {
    setUnauthorizedHandler(() => {
      queryClient.clear();
      navigate("/login", { replace: true });
    });
    return () => setUnauthorizedHandler(null);
  }, [navigate]);
  return null;
}

export default function App() {
  return (
    <ThemeProvider storageKey="dbbm-theme">
      <QueryClientProvider client={queryClient}>
        <ToastProvider>
          <BrowserRouter>
            <UnauthorizedRedirect />
            <RouteTitle />
            <Routes>
              <Route path="/docs" element={<DocsPage />} />
              <Route path="/about" element={<Navigate to="/docs" replace />} />
              <Route path="/setup" element={<SetupPage />} />
              <Route path="/login" element={<LoginPage />} />
              <Route element={<ProtectedRoute />}>
                <Route element={<Layout />}>
                  <Route index element={<DashboardPage />} />
                  <Route path="sources" element={<SourcesPage />} />
                  <Route path="destinations" element={<DestinationsPage />} />
                  <Route path="jobs" element={<JobsPage />} />
                  <Route path="runs" element={<RunsPage />} />
                  <Route path="runs/:id" element={<RunDetailPage />} />
                  <Route path="backups" element={<BackupsPage />} />
                  <Route path="notifications" element={<NotificationsPage />} />
                  <Route path="audit" element={<AuditPage />} />
                  <Route path="settings" element={<SettingsPage />} />
                </Route>
              </Route>
            </Routes>
          </BrowserRouter>
        </ToastProvider>
      </QueryClientProvider>
    </ThemeProvider>
  );
}
