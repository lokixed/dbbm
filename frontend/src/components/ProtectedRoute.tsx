import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useAuthStatus, useMe } from "@/api/auth";
import { FullPageSpinner } from "@/components/Spinner";

export function ProtectedRoute() {
  const location = useLocation();
  const status = useAuthStatus();
  const me = useMe();

  if (status.isPending || me.isPending) return <FullPageSpinner />;
  if (status.data?.setup_required) return <Navigate to="/setup" replace />;
  if (!me.data) return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  return <Outlet />;
}
