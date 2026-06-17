import { useMemo, useState } from "react";
import { ChevronRight } from "lucide-react";
import { cn } from "../../../lib/utils";
import { CopyButton } from "./CopyButton";

type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [k: string]: JsonValue };

// Theme-aware, dependency-free collapsible JSON tree. Unlike ui/JSONViewer
// (which uses fixed light-only tokens), this renders correctly in dark mode.
export function JsonTree({
  data,
  rootKey,
  defaultExpandDepth = 1,
  className,
  maxHeight = "60vh",
}: {
  data: unknown;
  rootKey?: string;
  defaultExpandDepth?: number;
  className?: string;
  maxHeight?: string;
}) {
  const json = useMemo(() => data as JsonValue, [data]);
  return (
    <div
      dir="ltr"
      className={cn(
        "relative rounded-md border border-stone-200 dark:border-[var(--hairline)] bg-stone-50 dark:bg-black/20 text-[12px] font-mono leading-relaxed",
        className
      )}
    >
      <div className="absolute top-1.5 right-1.5 z-10">
        <CopyButton value={JSON.stringify(json, null, 2)} label="Copy" />
      </div>
      <div className="px-3 py-2 overflow-auto" style={{ maxHeight }}>
        <TreeNode name={rootKey} value={json} depth={0} defaultExpandDepth={defaultExpandDepth} />
      </div>
    </div>
  );
}

function TreeNode({
  name,
  value,
  depth,
  defaultExpandDepth,
}: {
  name?: string;
  value: JsonValue;
  depth: number;
  defaultExpandDepth: number;
}) {
  const [open, setOpen] = useState(depth < defaultExpandDepth);

  if (value === null)
    return <Leaf name={name} text="null" cls="text-stone-400 dark:text-[var(--ink-3)]" />;
  if (typeof value === "string")
    return <Leaf name={name} text={`"${value}"`} cls="text-emerald-600 dark:text-emerald-400" />;
  if (typeof value === "number")
    return <Leaf name={name} text={String(value)} cls="text-blue-600 dark:text-sky-400" />;
  if (typeof value === "boolean")
    return <Leaf name={name} text={String(value)} cls="text-amber-600 dark:text-amber-400" />;

  const isArray = Array.isArray(value);
  const entries = isArray
    ? (value as JsonValue[]).map((v, i) => [String(i), v] as const)
    : Object.entries(value);

  if (entries.length === 0)
    return <Leaf name={name} text={isArray ? "[]" : "{}"} cls="text-stone-400 dark:text-[var(--ink-3)]" />;

  const summary = `${isArray ? "[" : "{"} ${entries.length} ${isArray ? "items" : "keys"} ${
    isArray ? "]" : "}"
  }`;

  return (
    <div className="flex flex-col">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1 text-start text-stone-500 dark:text-[var(--ink-3)] hover:text-ink"
      >
        <ChevronRight className={cn("h-3 w-3 transition-transform", open && "rotate-90")} aria-hidden />
        {name !== undefined && <span className="text-ink">"{name}"</span>}
        {name !== undefined && <span className="text-stone-400 dark:text-[var(--ink-3)]">:</span>}
        <span className="text-stone-400 dark:text-[var(--ink-3)]">
          {open ? (isArray ? "[" : "{") : summary}
        </span>
      </button>
      {open && (
        <div className="pl-4 border-l border-stone-200 dark:border-[var(--hairline)] ml-1.5 mt-0.5 flex flex-col gap-0.5">
          {entries.map(([k, v]) => (
            <TreeNode
              key={k}
              name={isArray ? undefined : k}
              value={v}
              depth={depth + 1}
              defaultExpandDepth={defaultExpandDepth}
            />
          ))}
          <span className="text-stone-400 dark:text-[var(--ink-3)]">{isArray ? "]" : "}"}</span>
        </div>
      )}
    </div>
  );
}

function Leaf({ name, text, cls }: { name?: string; text: string; cls: string }) {
  return (
    <div className="flex items-baseline gap-1">
      {name !== undefined && (
        <>
          <span className="text-ink">"{name}"</span>
          <span className="text-stone-400 dark:text-[var(--ink-3)]">:</span>
        </>
      )}
      <span className={cn("break-all", cls)}>{text}</span>
    </div>
  );
}
