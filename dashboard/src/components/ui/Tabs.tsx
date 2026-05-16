import {
  createContext,
  useContext,
  useId,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { cn } from "../../lib/utils";

interface TabsCtx {
  value: string;
  setValue: (v: string) => void;
  groupId: string;
}

const Ctx = createContext<TabsCtx | null>(null);

function useTabs() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("Tabs.* must be used inside <Tabs>");
  return ctx;
}

export function Tabs({
  value,
  defaultValue,
  onValueChange,
  children,
  className,
}: {
  value?: string;
  defaultValue?: string;
  onValueChange?: (v: string) => void;
  children: ReactNode;
  className?: string;
}) {
  const groupId = useId();
  const [internal, setInternal] = useState<string>(defaultValue ?? "");
  const controlled = value !== undefined;
  const current = controlled ? value : internal;

  const ctx = useMemo<TabsCtx>(
    () => ({
      value: current,
      groupId,
      setValue: (v) => {
        if (!controlled) setInternal(v);
        onValueChange?.(v);
      },
    }),
    [current, controlled, groupId, onValueChange]
  );

  return (
    <div className={className}>
      <Ctx.Provider value={ctx}>{children}</Ctx.Provider>
    </div>
  );
}

export function TabsList({
  children,
  className,
  "aria-label": ariaLabel,
}: {
  children: ReactNode;
  className?: string;
  "aria-label"?: string;
}) {
  return (
    <div
      role="tablist"
      aria-label={ariaLabel}
      className={cn(
        "inline-flex items-center gap-1 p-1 rounded-lg border border-line bg-ink-900/60",
        className
      )}
    >
      {children}
    </div>
  );
}

export function TabsTrigger({
  value,
  children,
  className,
}: {
  value: string;
  children: ReactNode;
  className?: string;
}) {
  const ctx = useTabs();
  const active = ctx.value === value;
  return (
    <button
      type="button"
      role="tab"
      id={`${ctx.groupId}-trigger-${value}`}
      aria-selected={active}
      aria-controls={`${ctx.groupId}-panel-${value}`}
      tabIndex={active ? 0 : -1}
      onClick={() => ctx.setValue(value)}
      className={cn(
        "h-7 px-3 rounded-md text-xs font-medium transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50",
        active
          ? "bg-ink-800 text-fg border border-line-strong"
          : "text-fg-muted hover:text-fg",
        className
      )}
    >
      {children}
    </button>
  );
}

export function TabsContent({
  value,
  children,
  className,
}: {
  value: string;
  children: ReactNode;
  className?: string;
}) {
  const ctx = useTabs();
  if (ctx.value !== value) return null;
  return (
    <div
      role="tabpanel"
      id={`${ctx.groupId}-panel-${value}`}
      aria-labelledby={`${ctx.groupId}-trigger-${value}`}
      className={cn("mt-4 focus:outline-none", className)}
      tabIndex={0}
    >
      {children}
    </div>
  );
}
