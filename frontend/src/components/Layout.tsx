import { useState } from "react";
import { Link, Outlet, useLocation, useNavigate } from "react-router-dom";
import Button from "xedonium/Button";
import Drawer from "xedonium/Drawer";
import Sidebar, { type SidebarItem } from "xedonium/Sidebar";
import { useTheme } from "xedonium/theme";
import ArchiveIcon from "xedonium/icons/Archive";
import BellIcon from "xedonium/icons/Bell";
import CalendarClockIcon from "xedonium/icons/CalendarClock";
import DatabaseIcon from "xedonium/icons/Database";
import HardDriveIcon from "xedonium/icons/HardDrive";
import LayoutDashboardIcon from "xedonium/icons/LayoutDashboard";
import LogOutIcon from "xedonium/icons/LogOut";
import MenuIcon from "xedonium/icons/Menu";
import MoonIcon from "xedonium/icons/Moon";
import PlayIcon from "xedonium/icons/Play";
import FileTextIcon from "xedonium/icons/FileText";
import SettingsIcon from "xedonium/icons/Settings";
import SunIcon from "xedonium/icons/Sun";
import { useLogout, useMe, useVersion } from "@/api/auth";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Logo } from "@/components/Logo";

const NAV_ITEMS: SidebarItem[] = [
  { key: "/", label: "Dashboard", href: "/", icon: <LayoutDashboardIcon className="h-4 w-4" /> },
  { key: "/sources", label: "Sources", href: "/sources", icon: <DatabaseIcon className="h-4 w-4" /> },
  { key: "/destinations", label: "Destinations", href: "/destinations", icon: <HardDriveIcon className="h-4 w-4" /> },
  { key: "/jobs", label: "Jobs", href: "/jobs", icon: <CalendarClockIcon className="h-4 w-4" /> },
  { key: "/runs", label: "Runs", href: "/runs", icon: <PlayIcon className="h-4 w-4" /> },
  { key: "/backups", label: "Backups", href: "/backups", icon: <ArchiveIcon className="h-4 w-4" /> },
  { key: "/notifications", label: "Notifications", href: "/notifications", icon: <BellIcon className="h-4 w-4" /> },
  { key: "/audit", label: "Audit log", href: "/audit", icon: <FileTextIcon className="h-4 w-4" /> },
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
  const version = useVersion();
  const [menuOpen, setMenuOpen] = useState(false);
  const [confirmingLogout, setConfirmingLogout] = useState(false);

  const handleLogout = () => {
    logout.mutate(undefined, { onSettled: () => navigate("/login", { replace: true }) });
  };

  const { activeTheme, toggleTheme } = useTheme();

  const items: SidebarItem[] = [
    ...NAV_ITEMS,
    { key: "account", label: user?.username ?? "Account", section: true },
    {
      key: "theme",
      label: activeTheme === "dark" ? "Light theme" : "Dark theme",
      icon: activeTheme === "dark" ? <SunIcon className="h-4 w-4" /> : <MoonIcon className="h-4 w-4" />,
      onClick: toggleTheme,
    },
    { key: "logout", label: "Log out", icon: <LogOutIcon className="h-4 w-4" />, onClick: () => setConfirmingLogout(true) },
  ];

  const sidebar = (
    <Sidebar
      items={items}
      activeKey={activeKey(pathname)}
      header={
        <span className="flex items-center gap-2 px-2">
          <Logo size={28} />
          <span className="text-lg font-semibold tracking-tight">DBBM</span>
          {version.data && <span className="ml-2 text-xs text-app-muted">v{version.data.version}</span>}
        </span>
      }
      linkComponent={Link}
      linkProp="to"
      label="Main navigation"
    />
  );

  return (
    <div className="flex h-screen overflow-hidden bg-app-bg text-app-text">
      <a
        href="#main-content"
        className="sr-only focus:not-sr-only focus:fixed focus:left-3 focus:top-3 focus:z-[100] focus:border focus:border-app-border focus:bg-app-card focus:px-3 focus:py-2 focus:text-sm focus:text-app-text"
      >
        Skip to main content
      </a>
      <aside className="hidden shrink-0 md:block">{sidebar}</aside>
      <main id="main-content" tabIndex={-1} className="relative min-w-0 flex-1 overflow-y-auto p-4 outline-none sm:p-6">
        <div className="mb-4 md:hidden">
          <Button variant="secondary" aria-label="Open menu" onClick={() => setMenuOpen(true)}>
            <MenuIcon className="h-4 w-4" />
          </Button>
        </div>
        <Outlet />
      </main>
      <Drawer open={menuOpen} onClose={() => setMenuOpen(false)} side="left" width="max-w-xs" padded={false} title="DBBM">
        <div onClick={() => setMenuOpen(false)}>{sidebar}</div>
      </Drawer>
      <ConfirmDialog
        open={confirmingLogout}
        title="Log out?"
        description="You will need to sign in again to manage backups."
        confirmLabel="Log out"
        pendingLabel="Logging out..."
        tone="danger"
        pending={logout.isPending}
        onConfirm={handleLogout}
        onCancel={() => setConfirmingLogout(false)}
      />
    </div>
  );
}
