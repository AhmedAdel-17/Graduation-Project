import { useMemo, useState } from "react";
import { Minus, Plus } from "lucide-react";
import { Markdown } from "./Markdown";
import { cn } from "../../lib/utils";

/**
 * Renders long agent prose as a stack of collapsible sections, split at the
 * document's top-level markdown headings. Each section shows just its title with
 * a ＋ toggle; clicking reveals the body. Sub-headings stay inside their section.
 *
 * If the text has no headings, it falls back to a plain <Markdown> render.
 */
interface Section {
  title: string;
  body: string;
}

const HEADING_RE = /^(#{1,6})\s+(.+?)\s*#*\s*$/;

function parseSections(md: string): { preamble: string; sections: Section[] } {
  const lines = md.split("\n");

  // The "section" level is the shallowest heading present (## if the doc mixes
  // ## and ###), so sub-headings remain nested inside their parent section.
  let minLevel = 99;
  for (const l of lines) {
    const m = HEADING_RE.exec(l);
    if (m) minLevel = Math.min(minLevel, m[1].length);
  }
  if (minLevel === 99) return { preamble: md, sections: [] };

  const preamble: string[] = [];
  const sections: Section[] = [];
  let cur: { title: string; body: string[] } | null = null;

  for (const l of lines) {
    const m = HEADING_RE.exec(l);
    if (m && m[1].length === minLevel) {
      if (cur) sections.push({ title: cur.title, body: cur.body.join("\n").trim() });
      cur = { title: m[2].trim(), body: [] };
    } else if (cur) {
      cur.body.push(l);
    } else {
      preamble.push(l);
    }
  }
  if (cur) sections.push({ title: cur.title, body: cur.body.join("\n").trim() });

  return { preamble: preamble.join("\n").trim(), sections };
}

export function SectionedMarkdown({
  children,
  variant = "paper",
}: {
  children: string;
  variant?: "dark" | "paper";
}) {
  const { preamble, sections } = useMemo(() => parseSections(children), [children]);
  const [open, setOpen] = useState<Record<number, boolean>>({});

  // Not enough structure to bother sectioning — render as-is.
  if (sections.length < 2) {
    return <Markdown variant={variant}>{children}</Markdown>;
  }

  return (
    <div className="space-y-2.5">
      {preamble && <Markdown variant={variant}>{preamble}</Markdown>}

      {sections.map((s, i) => {
        const isOpen = !!open[i];
        return (
          <div
            key={i}
            className="rounded-xl border border-stone-200/80 overflow-hidden dark:border-[var(--hairline)]"
          >
            <button
              type="button"
              aria-expanded={isOpen}
              onClick={() => setOpen((o) => ({ ...o, [i]: !o[i] }))}
              className={cn(
                "w-full flex items-center justify-between gap-3 px-4 py-3 text-left transition-colors",
                isOpen
                  ? "bg-stone-50/70 dark:bg-white/[0.03]"
                  : "hover:bg-stone-50/50 dark:hover:bg-white/[0.02]"
              )}
            >
              <span className="display text-[13.5px] font-semibold text-ink leading-snug">
                {s.title}
              </span>
              <span
                className={cn(
                  "flex h-6 w-6 shrink-0 items-center justify-center rounded-md border transition-colors",
                  isOpen
                    ? "border-[var(--brand-green)]/40 text-[var(--brand-green)]"
                    : "border-stone-200 text-stone-500 dark:border-[var(--hairline)]"
                )}
              >
                {isOpen ? <Minus className="h-3.5 w-3.5" strokeWidth={2.4} /> : <Plus className="h-3.5 w-3.5" strokeWidth={2.4} />}
              </span>
            </button>
            {isOpen && (
              <div className="px-4 pb-4 pt-2 border-t border-stone-200/70 dark:border-[var(--hairline)]">
                <Markdown variant={variant}>{s.body || "_No details provided._"}</Markdown>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}
