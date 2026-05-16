import { useMemo, useState } from "react";
import { ChevronRight, Copy } from "lucide-react";
import { cn } from "../../lib/utils";

type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [k: string]: JsonValue };

// Light, dependency-free JSON tree. Collapsible by default at depth >= 1.
// Renders left-to-right always (numbers/strings stay LTR even in RTL pages).
export function JSONViewer({
  data,
  rootKey,
  defaultExpandDepth = 1,
  className,
}: {
  data: unknown;
  rootKey?: string;
  defaultExpandDepth?: number;
  className?: string;
}) {
  const json = useMemo(() => data as JsonValue, [data]);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(JSON.stringify(json, null, 2));
    } catch {
      /* clipboard blocked; silent */
    }
  };
  return (
    <div
      dir="ltr"
      className={cn(
        "relative rounded-md border border-line bg-ink-900/60 text-[12px] font-mono leading-relaxed",
        className
      )}
    >
      <button
        type="button"
        onClick={copy}
        title="Copy JSON"
        className="absolute top-1.5 end-1.5 h-6 w-6 rounded-md text-fg-subtle hover:text-fg hover:bg-ink-800 inline-flex items-center justify-center focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand-500/50"
      >
        <Copy className="h-3 w-3" aria-hidden />
      </button>
      <div className="px-3 py-2 overflow-auto max-h-[60vh]">
        <Node
          name={rootKey}
          value={json}
          depth={0}
          defaultExpandDepth={defaultExpandDepth}
        />
      </div>
    </div>
  );
}

function Node({
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

  if (value === null) return <Leaf name={name} text="null" cls="text-fg-subtle" />;
  if (typeof value === "string")
    return <Leaf name={name} text={`"${value}"`} cls="text-brand-400" />;
  if (typeof value === "number")
    return <Leaf name={name} text={String(value)} cls="text-accent" />;
  if (typeof value === "boolean")
    return <Leaf name={name} text={String(value)} cls="text-amber-400" />;

  const isArray = Array.isArray(value);
  const entries = isArray
    ? (value as JsonValue[]).map((v, i) => [String(i), v] as const)
    : Object.entries(value);

  if (entries.length === 0) {
    return (
      <Leaf
        name={name}
        text={isArray ? "[]" : "{}"}
        cls="text-fg-subtle"
      />
    );
  }

  const summary = `${isArray ? "[" : "{"} ${entries.length} ${
    isArray ? "items" : "keys"
  } ${isArray ? "]" : "}"}`;

  return (
    <div className="flex flex-col">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className="inline-flex items-center gap-1 text-start hover:text-fg text-fg-muted"
      >
        <ChevronRight
          className={cn(
            "h-3 w-3 transition-transform",
            open && "rotate-90"
          )}
          aria-hidden
        />
        {name !== undefined && (
          <span className="text-fg">"{name}"</span>
        )}
        {name !== undefined && <span className="text-fg-subtle">:</span>}
        <span className="text-fg-subtle">{open ? (isArray ? "[" : "{") : summary}</span>
      </button>
      {open && (
        <div className="ps-4 border-s border-line ms-1.5 mt-0.5 flex flex-col gap-0.5">
          {entries.map(([k, v]) => (
            <Node
              key={k}
              name={isArray ? undefined : k}
              value={v}
              depth={depth + 1}
              defaultExpandDepth={defaultExpandDepth}
            />
          ))}
          <span className="text-fg-subtle">{isArray ? "]" : "}"}</span>
        </div>
      )}
    </div>
  );
}

function Leaf({
  name,
  text,
  cls,
}: {
  name?: string;
  text: string;
  cls: string;
}) {
  return (
    <div className="flex items-baseline gap-1">
      {name !== undefined && (
        <>
          <span className="text-fg">"{name}"</span>
          <span className="text-fg-subtle">:</span>
        </>
      )}
      <span className={cls}>{text}</span>
    </div>
  );
}
