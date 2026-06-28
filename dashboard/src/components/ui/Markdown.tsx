import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { cn } from "../../lib/utils";

// Whitelisted markdown renderer. No raw HTML. GFM enabled so pipe tables,
// strikethrough, task lists, `---` rules and autolinks render properly —
// agent theses lean on tables + headings heavily.
//
//   variant="dark"  → slate UI kit (workspace / run pages)        [default]
//   variant="paper" → warm editorial theme (HomeScreen agent cards)
type Variant = "dark" | "paper";

const DARK =
  "text-sm text-fg leading-relaxed " +
  "[&_h1]:text-base [&_h1]:font-semibold [&_h1]:mt-4 [&_h1]:mb-2 " +
  "[&_h2]:text-sm [&_h2]:font-semibold [&_h2]:mt-3 [&_h2]:mb-1.5 [&_h2]:text-fg " +
  "[&_h3]:text-xs [&_h3]:font-semibold [&_h3]:mt-2 [&_h3]:mb-1 [&_h3]:uppercase [&_h3]:tracking-wider [&_h3]:text-fg-muted " +
  "[&_p]:my-1.5 " +
  "[&_ul]:list-disc [&_ul]:ps-5 [&_ul]:my-1.5 " +
  "[&_ol]:list-decimal [&_ol]:ps-5 [&_ol]:my-1.5 " +
  "[&_li]:my-0.5 " +
  "[&_hr]:my-3 [&_hr]:border-line " +
  "[&_strong]:text-fg [&_strong]:font-semibold " +
  "[&_em]:text-fg-muted [&_em]:italic " +
  "[&_code]:font-mono [&_code]:text-[12px] [&_code]:bg-ink-800 [&_code]:rounded [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-brand-400 " +
  "[&_pre]:bg-ink-900 [&_pre]:border [&_pre]:border-line [&_pre]:rounded-md [&_pre]:p-3 [&_pre]:my-2 [&_pre]:overflow-auto " +
  "[&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-fg " +
  "[&_blockquote]:border-s-2 [&_blockquote]:border-line-strong [&_blockquote]:ps-3 [&_blockquote]:text-fg-muted [&_blockquote]:my-2 " +
  "[&_a]:text-accent [&_a]:underline [&_a]:underline-offset-2 hover:[&_a]:text-brand-400 " +
  "[&_table]:w-full [&_table]:text-xs [&_table]:my-2 [&_table]:border-collapse " +
  "[&_th]:text-start [&_th]:font-semibold [&_th]:px-2 [&_th]:py-1 [&_th]:border-b [&_th]:border-line " +
  "[&_td]:px-2 [&_td]:py-1 [&_td]:border-b [&_td]:border-line/60";

// Warm "paper" theme — tuned to the HomeScreen design system
// (--ink / stone / hairline). Tables get a real header band + hairline rows
// so the valuation/scenario tables the agents emit actually read as tables.
const PAPER =
  "text-[13.5px] text-ink-2 leading-[1.7] " +
  "[&_h1]:display [&_h1]:text-[17px] [&_h1]:font-semibold [&_h1]:text-ink [&_h1]:mt-5 [&_h1]:mb-2 " +
  "[&_h2]:text-[14.5px] [&_h2]:font-semibold [&_h2]:text-ink [&_h2]:mt-4 [&_h2]:mb-2 [&_h2]:pb-1.5 [&_h2]:border-b [&_h2]:border-stone-200/70 dark:[&_h2]:border-[var(--hairline)] " +
  "[&_h3]:text-[11px] [&_h3]:font-semibold [&_h3]:uppercase [&_h3]:tracking-[0.12em] [&_h3]:text-stone-500 [&_h3]:mt-3.5 [&_h3]:mb-1.5 " +
  "[&_h4]:text-[12.5px] [&_h4]:font-semibold [&_h4]:text-ink [&_h4]:mt-3 [&_h4]:mb-1 " +
  "[&_p]:my-2 " +
  "[&_ul]:list-disc [&_ul]:ps-5 [&_ul]:my-2 [&_ul]:space-y-1 " +
  "[&_ol]:list-decimal [&_ol]:ps-5 [&_ol]:my-2 [&_ol]:space-y-1 " +
  "[&_li]:marker:text-stone-400 " +
  "[&_hr]:my-4 [&_hr]:border-0 [&_hr]:border-t [&_hr]:border-stone-200 dark:[&_hr]:border-[var(--hairline)] " +
  "[&_strong]:text-ink [&_strong]:font-semibold " +
  "[&_em]:text-ink-2 [&_em]:italic " +
  "[&_code]:font-mono [&_code]:text-[12px] [&_code]:bg-stone-100 dark:[&_code]:bg-white/10 [&_code]:rounded [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:text-ink " +
  "[&_pre]:bg-stone-50 dark:[&_pre]:bg-white/[0.04] [&_pre]:border [&_pre]:border-stone-200 dark:[&_pre]:border-[var(--hairline)] [&_pre]:rounded-lg [&_pre]:p-3 [&_pre]:my-3 [&_pre]:overflow-auto [&_pre]:text-[12px] " +
  "[&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-ink-2 " +
  "[&_blockquote]:border-s-2 [&_blockquote]:border-stone-300 dark:[&_blockquote]:border-[var(--hairline-2)] [&_blockquote]:ps-3 [&_blockquote]:text-ink-3 [&_blockquote]:my-3 " +
  "[&_a]:text-emerald-700 [&_a]:underline [&_a]:underline-offset-2 hover:[&_a]:text-emerald-800 " +
  // tables: wrapped so they scroll on narrow viewports + read as a real grid
  "[&_table]:w-full [&_table]:text-[12.5px] [&_table]:my-3 [&_table]:border-collapse [&_table]:border [&_table]:border-stone-200 dark:[&_table]:border-[var(--hairline)] [&_table]:rounded-lg [&_table]:overflow-hidden " +
  "[&_thead]:bg-stone-50 dark:[&_thead]:bg-white/[0.03] " +
  "[&_th]:text-start [&_th]:text-ink [&_th]:font-semibold [&_th]:px-3 [&_th]:py-2 [&_th]:border-b [&_th]:border-stone-200 dark:[&_th]:border-[var(--hairline)] " +
  "[&_td]:px-3 [&_td]:py-2 [&_td]:text-ink-2 [&_td]:border-b [&_td]:border-stone-100 dark:[&_td]:border-[var(--hairline)] " +
  "[&_tbody_tr:last-child_td]:border-b-0 " +
  "[&_tbody_tr:hover]:bg-stone-50/60 dark:[&_tbody_tr:hover]:bg-white/[0.02]";

export function Markdown({
  children,
  className,
  variant = "dark",
}: {
  children: string;
  className?: string;
  variant?: Variant;
}) {
  return (
    <div className={cn(variant === "paper" ? PAPER : DARK, className)}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} skipHtml>
        {children}
      </ReactMarkdown>
    </div>
  );
}
