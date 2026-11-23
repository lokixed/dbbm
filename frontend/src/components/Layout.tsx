import {
  Archive,
  Bell,
  CalendarClock,
  Database,
  HardDrive,
  LayoutDashboard,
  LogOut,
  Play,
  Settings,
  type LucideIcon,
} from "lucide-react";
import { NavLink, Outlet, useNavigate } from "react-router-dom";
import { useLogout, useMe } from "@/api/auth";
import { ThemeToggle } from "@/components/ThemeToggle";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

const NAV_ITEMS: { to: string; label: string; icon: LucideIcon }[] = [
  { to: "/", label: "Dashboard", icon: LayoutDashboard },
  { to: "/sources", label: "Sources", icon: Database },
  { to: "/destinations", label: "Destinations", icon: HardDrive },
  { to: "/jobs", label: "Jobs", icon: CalendarClock },
  { to: "/runs", label: "Runs", icon: Play },
  { to: "/backups", label: "Backups", icon: Archive },
  { to: "/notifications", label: "Notifications", icon: Bell },
  { to: "/settings", label: "Settings", icon: Settings },
];

export function Layout() {
  const navigate = useNavigate();
  const { data: user } = useMe();
  const logout = useLogout();

  const handleLogout = () => {
    logout.mutate(undefined, { onSettled: () => navigate("/login", { replace: true }) });
  };

  return (
    <div className="flex min-h-screen">
      <aside className="flex w-56 shrink-0 flex-col border-r bg-card">
        <div className="px-5 py-4 text-lg font-semibold tracking-tight">DBBackup</div>
        <nav className="flex flex-1 flex-col gap-1 px-3">
          {NAV_ITEMS.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 rounded-md px-3 py-2 text-sm font-medium text-muted-foreground transition-colors hover:bg-accent hover:text-accent-foreground",
                  isActive && "bg-accent text-accent-foreground",
                )
              }
            >
              <Icon className="h-4 w-4" />
              {label}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-end gap-2 border-b px-6">
          <span className="mr-2 text-sm text-muted-foreground">{user?.username}</span>
          <ThemeToggle />
          <Button variant="ghost" size="sm" onClick={handleLogout} disabled={logout.isPending}>
            <LogOut className="h-4 w-4" />
            Log out
          </Button>
        </header>
        <main className="flex-1 p-6">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
