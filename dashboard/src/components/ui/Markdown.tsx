import ReactMarkdown from "react-markdown";
import { cn } from "../../lib/utils";

// Whitelisted markdown renderer. No raw HTML, no extensions — we render plain
// agent prose. Lists, headings, code, emphasis, tables only.
export function Markdown({
  children,
  className,
}: {
  children: string;
  className?: string;
}) {
  return (
    <div
      className={cn(
        "text-sm text-fg leading-relaxed",
        "[&_h1]:text-base [&_h1]:font-semibold [&_h1]:mt-4 [&_h1]:mb-2",
        "[&_h2]:text-sm [&_h2]:font-semibold [&_h2]:mt-3 [&_h2]:mb-1.5 [&_h2]:text-fg",
        "[&_h3]:text-xs [&_h3]:font-semibold [&_h3]:mt-2 [&_h3]:mb-1 [&_h3]:uppercase [&_h3]:tracking-wider [&_h3]:text-fg-muted",
        "[&_p]:my-1.5",
        "[&_ul]:list-disc [&_ul]:ps-5 [&_ul]:my-1.5",
        "[&_ol]:list-decimal [&_ol]:ps-5 [&_ol]:my-1.5",
        "[&_li]:my-0.5",
        "[&_strong]:text-fg [&_strong]:font-semibold",
        "[&_em]:text-fg-muted [&_em]:italic",
        "[&_code]:font-mono [&_code]:text-[12px] [&_code]:bg-ink-800 [&_code]:rounded [&_code]:px-1 [&_code]:py-0.5 [&_code]:text-brand-400",
        "[&_pre]:bg-ink-900 [&_pre]:border [&_pre]:border-line [&_pre]:rounded-md [&_pre]:p-3 [&_pre]:my-2 [&_pre]:overflow-auto",
        "[&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-fg",
        "[&_blockquote]:border-s-2 [&_blockquote]:border-line-strong [&_blockquote]:ps-3 [&_blockquote]:text-fg-muted [&_blockquote]:my-2",
        "[&_a]:text-accent [&_a]:underline [&_a]:underline-offset-2 hover:[&_a]:text-brand-400",
        "[&_table]:w-full [&_table]:text-xs [&_table]:my-2 [&_table]:border-collapse",
        "[&_th]:text-start [&_th]:font-semibold [&_th]:px-2 [&_th]:py-1 [&_th]:border-b [&_th]:border-line",
        "[&_td]:px-2 [&_td]:py-1 [&_td]:border-b [&_td]:border-line/60",
        className
      )}
    >
      <ReactMarkdown
        // Disable raw HTML; react-markdown strips it by default.
        skipHtml
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
