import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import AppShell from "xedonium/AppShell";
import Button from "xedonium/Button";
import Sidebar, { type SidebarItem } from "xedonium/Sidebar";
import ThemeToggle from "xedonium/ThemeToggle";
import ArchiveIcon from "xedonium/icons/Archive";
import BellIcon from "xedonium/icons/Bell";
import CalendarClockIcon from "xedonium/icons/CalendarClock";
import DatabaseIcon from "xedonium/icons/Database";
import HardDriveIcon from "xedonium/icons/HardDrive";
import LayoutDashboardIcon from "xedonium/icons/LayoutDashboard";
import LogOutIcon from "xedonium/icons/LogOut";
import PlayIcon from "xedonium/icons/Play";
import SettingsIcon from "xedonium/icons/Settings";
import { useLogout, useMe } from "@/api/auth";

const NAV_ITEMS: SidebarItem[] = [
  { key: "/", label: "Dashboard", href: "/", icon: <LayoutDashboardIcon className="h-4 w-4" /> },
  { key: "/sources", label: "Sources", href: "/sources", icon: <DatabaseIcon className="h-4 w-4" /> },
  { key: "/destinations", label: "Destinations", href: "/destinations", icon: <HardDriveIcon className="h-4 w-4" /> },
  { key: "/jobs", label: "Jobs", href: "/jobs", icon: <CalendarClockIcon className="h-4 w-4" /> },
  { key: "/runs", label: "Runs", href: "/runs", icon: <PlayIcon className="h-4 w-4" /> },
  { key: "/backups", label: "Backups", href: "/backups", icon: <ArchiveIcon className="h-4 w-4" /> },
  { key: "/notifications", label: "Notifications", href: "/notifications", icon: <BellIcon className="h-4 w-4" /> },
  { key: "/settings", label: "Settings", href: "/settings", icon: <SettingsIcon className="h-4 w-4" /> },
];

function activeKey(pathname: string): string {
  const match = NAV_ITEMS.find(
    (item) => item.key !== "/" && (pathname === item.key || pathname.startsWith(`${item.key}/`)),
  );
  return match?.key ?? "/";
}

export function Layout() {
  const navigate = useNavigate();
  const { pathname } = useLocation();
  const { data: user } = useMe();
  const logout = useLogout();

  const handleLogout = () => {
    logout.mutate(undefined, { onSettled: () => navigate("/login", { replace: true }) });
  };

  const header = (
    <div className="flex h-14 items-center justify-end gap-2 border-b border-app-border px-6">
      <span className="mr-2 text-sm text-app-muted">{user?.username}</span>
      <ThemeToggle />
      <Button variant="flat" onClick={handleLogout} disabled={logout.isPending}>
        <LogOutIcon className="h-4 w-4" />
        Log out
      </Button>
    </div>
  );

  return (
    <AppShell
      header={header}
      sidebarTitle="DBBackup"
      sidebar={
        <Sidebar
          items={NAV_ITEMS}
          activeKey={activeKey(pathname)}
          header={<span className="px-2 text-lg font-semibold tracking-tight">DBBackup</span>}
          linkComponent={Link}
          linkProp="to"
          label="Main navigation"
        />
      }
    >
      <main className="p-6">
        <Outlet />
      </main>
    </AppShell>
  );
}
