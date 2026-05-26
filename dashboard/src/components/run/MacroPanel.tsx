import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { MacroSnapshot, Run } from "@/types/api";

type MacroLike = MacroSnapshot | Run["macro_context"];

export function MacroPanel({ macro }: { macro: MacroLike }) {
  const items: { label: string; value: string }[] = [
    { label: "CBE rate", value: `${macro.cbe_rate}%` },
    { label: "USD/EGP", value: macro.usd_egp.toFixed(2) },
    { label: "EGX30", value: macro.egx30_trend },
    { label: "Brent", value: `$${macro.brent_usd.toFixed(1)}` },
    { label: "FX trend", value: macro.fx_trend },
    { label: "IMF", value: macro.imf_program_active ? "Active" : "—" },
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-sm">Macro context</CardTitle>
      </CardHeader>
      <CardContent className="grid grid-cols-2 gap-2">
        {items.map((i) => (
          <div key={i.label} className="rounded-md border border-border bg-secondary/30 p-2.5">
            <div className="text-[10px] uppercase tracking-wider text-muted-foreground">
              {i.label}
            </div>
            <div className="mt-0.5 font-mono text-sm font-semibold tabular">{i.value}</div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
}
