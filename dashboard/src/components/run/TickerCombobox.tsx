import { useMemo, useState } from "react";
import { Check, ChevronsUpDown } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { EGX_TICKERS, SECTORS } from "@/lib/constants";
import { TickerLogo } from "./TickerLogo";

export function TickerCombobox({
  value,
  onChange,
}: {
  value?: string;
  onChange: (s: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const selected = EGX_TICKERS.find((t) => t.symbol === value);

  const grouped = useMemo(() => {
    const out: Record<string, typeof EGX_TICKERS> = {};
    for (const s of SECTORS) out[s] = [];
    for (const t of EGX_TICKERS) out[t.sector].push(t);
    return out;
  }, []);

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="outline"
          role="combobox"
          aria-expanded={open}
          className="w-full justify-between font-normal"
        >
          {selected ? (
            <span className="flex min-w-0 items-center gap-2">
              <TickerLogo symbol={selected.symbol} size="md" />
              <span className="font-mono text-sm">{selected.symbol}</span>
              <span className="truncate text-muted-foreground">{selected.name_en}</span>
            </span>
          ) : (
            <span className="text-muted-foreground">Select ticker…</span>
          )}
          <ChevronsUpDown className="ml-2 h-4 w-4 opacity-50" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-[--radix-popover-trigger-width] p-0" align="start">
        <Command>
          <CommandInput placeholder="Search ticker or name…" />
          <CommandList className="max-h-[320px]">
            <CommandEmpty>No ticker found.</CommandEmpty>
            {SECTORS.map((sector) => (
              <CommandGroup key={sector} heading={sector}>
                {grouped[sector].map((t) => (
                  <CommandItem
                    key={t.symbol}
                    value={`${t.symbol} ${t.name_en} ${t.name_ar}`}
                    onSelect={() => {
                      onChange(t.symbol);
                      setOpen(false);
                    }}
                    className="flex items-center gap-2"
                  >
                    <Check
                      className={cn("h-4 w-4", value === t.symbol ? "opacity-100" : "opacity-0")}
                    />
                    <TickerLogo symbol={t.symbol} size="sm" />
                    <span className="font-mono text-xs">{t.symbol}</span>
                    <span className="truncate text-sm">{t.name_en}</span>
                    <span dir="rtl" className="ml-auto truncate text-xs text-muted-foreground">
                      {t.name_ar}
                    </span>
                  </CommandItem>
                ))}
              </CommandGroup>
            ))}
          </CommandList>
        </Command>
      </PopoverContent>
    </Popover>
  );
}
