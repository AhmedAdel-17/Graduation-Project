/**
 * Numbered section header used between the editorial cards.
 *
 *   01    ADVERSARIAL RESEARCH    ────────────────────
 */
export function SectionHeader({
  number,
  title,
}: {
  number: string;
  title: string;
}) {
  return (
    <div className="flex items-center gap-3 pt-2">
      <span className="font-mono text-xs text-muted-foreground">{number}</span>
      <span className="text-xs font-medium uppercase tracking-[0.2em] text-foreground">
        {title}
      </span>
      <span className="h-px flex-1 bg-border" />
    </div>
  );
}
