import { Link, useRouterState, useNavigate } from "@tanstack/react-router";
import {
  LayoutDashboard,
  PlayCircle,
  BarChart3,
  History,
  Settings,
  Activity,
  ChevronLeft,
  ChevronRight,
  Sun,
  Moon,
  LogOut,
  User as UserIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { useUIStore, useAuthStore } from "@/lib/store";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

const NAV = [
  { to: "/", label: "Home", icon: LayoutDashboard, exact: true },
  { to: "/run", label: "Run analysis", icon: PlayCircle },
  { to: "/backtest", label: "Backtest", icon: BarChart3 },
  { to: "/history", label: "History", icon: History },
] as const;

export function Sidebar() {
  const collapsed = useUIStore((s) => s.sidebarCollapsed);
  const toggle = useUIStore((s) => s.toggleSidebar);
  const theme = useUIStore((s) => s.theme);
  const setTheme = useUIStore((s) => s.setTheme);
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const navigate = useNavigate();

  const path = useRouterState({ select: (s) => s.location.pathname });
  const isActive = (to: string, exact?: boolean) =>
    exact ? path === to : path === to || path.startsWith(to + "/");

  const handleLogout = () => {
    logout();
    navigate({ to: "/login" });
  };

  return (
    <aside
      className={cn(
        "sticky top-0 hidden h-screen shrink-0 flex-col border-r border-sidebar-border bg-sidebar text-sidebar-foreground md:flex",
        collapsed ? "w-16" : "w-60",
      )}
    >
      <div
        className={cn(
          "flex h-14 items-center border-b border-sidebar-border",
          collapsed ? "flex-col justify-center gap-1 px-2 py-2" : "justify-between px-3",
        )}
      >
        <Link to="/" className="flex items-center gap-2 overflow-hidden">
          <div className="flex h-8 w-8 shrink-0 items-center justify-center rounded-md bg-primary text-primary-foreground">
            <Activity className="h-4 w-4" strokeWidth={2.5} />
          </div>
          {!collapsed && (
            <span className="truncate text-sm font-semibold tracking-tight">TradingAgents</span>
          )}
        </Link>
        {!collapsed && (
          <button
            onClick={toggle}
            className="rounded p-1 text-muted-foreground hover:bg-sidebar-accent hover:text-foreground"
            aria-label="Collapse sidebar"
            title="Collapse sidebar"
          >
            <ChevronLeft className="h-4 w-4" />
          </button>
        )}
      </div>
      {collapsed && (
        <button
          onClick={toggle}
          className="mx-auto mt-1 mb-1 rounded p-1 text-muted-foreground hover:bg-sidebar-accent hover:text-foreground"
          aria-label="Expand sidebar"
          title="Expand sidebar"
        >
          <ChevronRight className="h-4 w-4" />
        </button>
      )}

      <nav className="flex-1 space-y-1 p-2">
        {NAV.map((item) => {
          const Icon = item.icon;
          const active = isActive(item.to, "exact" in item ? item.exact : false);
          return (
            <Link
              key={item.to}
              to={item.to}
              className={cn(
                "flex items-center gap-3 rounded-md px-2.5 py-2 text-sm font-medium transition-colors",
                active
                  ? "bg-sidebar-accent text-foreground"
                  : "text-muted-foreground hover:bg-sidebar-accent/50 hover:text-foreground",
                collapsed && "justify-center",
              )}
              title={collapsed ? item.label : undefined}
            >
              <Icon className={cn("h-4 w-4 shrink-0", active && "text-primary")} />
              {!collapsed && <span className="truncate">{item.label}</span>}
            </Link>
          );
        })}

        <div className="my-2 border-t border-sidebar-border" />

        <Link
          to="/settings"
          className={cn(
            "flex items-center gap-3 rounded-md px-2.5 py-2 text-sm font-medium transition-colors",
            isActive("/settings")
              ? "bg-sidebar-accent text-foreground"
              : "text-muted-foreground hover:bg-sidebar-accent/50 hover:text-foreground",
            collapsed && "justify-center",
          )}
          title={collapsed ? "Settings" : undefined}
        >
          <Settings className="h-4 w-4 shrink-0" />
          {!collapsed && <span>Settings</span>}
        </Link>
      </nav>

      <div className="border-t border-sidebar-border p-2">
        <div
          className={cn(
            "flex items-center gap-2",
            collapsed && "flex-col gap-2",
          )}
        >
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <button
                className={cn(
                  "flex items-center gap-2 rounded-md hover:bg-sidebar-accent",
                  collapsed
                    ? "h-9 w-9 justify-center p-0"
                    : "min-w-0 flex-1 p-1.5",
                )}
                title={collapsed ? user?.name ?? "Account" : undefined}
              >
                <Avatar className="h-7 w-7 shrink-0">
                  <AvatarFallback className="bg-primary/15 text-xs text-primary">
                    {user?.name?.split(" ").map((p) => p[0]).slice(0, 2).join("") || "U"}
                  </AvatarFallback>
                </Avatar>
                {!collapsed && (
                  <div className="min-w-0 flex-1 text-left">
                    <div className="truncate text-xs font-medium">{user?.name || "—"}</div>
                    <div className="truncate text-[10px] text-muted-foreground">{user?.email}</div>
                  </div>
                )}
              </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent side={collapsed ? "right" : "top"} align="start" className="w-56">
              <DropdownMenuLabel>{user?.name}</DropdownMenuLabel>
              <DropdownMenuSeparator />
              <DropdownMenuItem onClick={() => navigate({ to: "/settings" })}>
                <UserIcon className="mr-2 h-4 w-4" /> Profile
              </DropdownMenuItem>
              <DropdownMenuItem onClick={handleLogout}>
                <LogOut className="mr-2 h-4 w-4" /> Logout
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <button
            onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            className={cn(
              "rounded text-muted-foreground hover:bg-sidebar-accent hover:text-foreground",
              collapsed ? "p-2" : "p-1.5",
            )}
            aria-label="Toggle theme"
            title={theme === "dark" ? "Switch to light" : "Switch to dark"}
          >
            {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
          </button>
        </div>
      </div>
    </aside>
  );
}
